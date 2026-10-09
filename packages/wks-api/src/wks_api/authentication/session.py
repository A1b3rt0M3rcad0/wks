"""Opaque, revocable browser sessions. Credentials never enter browser storage."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from wks_core.domain.models import Error, Principal
from wks_core.storage.database import Client, now

from .models import Account, WebSession

COOKIE = "wks_session"


class LegacyLoginBody(BaseModel):
    token: str = Field(min_length=32, max_length=4096, repr=False)


def check_origin(service, request):
    expected = service.settings.web_public_origin or str(request.base_url).rstrip("/")
    origin = request.headers.get("origin")
    if origin is not None and not hmac.compare_digest(origin, expected):
        raise Error("auth.csrf_invalid", "Request origin unavailable", 403)


def csrf(service, token):
    return hmac.new(service.cursor_key.encode(), (token + "/csrf").encode(), "sha256").hexdigest()


def resolve(service, request: Request, mutation=False):
    token = request.cookies.get(COOKIE, "")
    with service.sessions() as db:
        session = db.get(WebSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
        if not session or session.expires_at <= now():
            raise Error("auth.unauthenticated", "Authentication required", 401)
        caller = db.get(Client, session.client_id)
        if not caller or not caller.active or caller.role != "client":
            raise Error("auth.unauthenticated", "Authentication required", 401)
        p = Principal(caller.id, caller.role, caller.audience)
        service.grant(db, p)
        if mutation:
            check_origin(service, request)
            if not hmac.compare_digest(request.headers.get("x-wks-csrf", ""), csrf(service, token)):
                raise Error("auth.csrf_invalid", "Request verification required", 403)
        return p


def issue_session(service, db, client_id, request, extra=None, status=200):
    token = secrets.token_urlsafe(48)
    db.execute(delete(WebSession).where(WebSession.expires_at <= now()))
    previous = request.cookies.get(COOKIE)
    if previous:
        db.execute(
            delete(WebSession).where(
                WebSession.token_hash == hashlib.sha256(previous.encode()).hexdigest()
            )
        )
    db.add(
        WebSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            client_id=client_id,
            expires_at=now() + timedelta(seconds=service.settings.web_session_ttl_seconds),
        )
    )
    response = JSONResponse(
        {"authenticated": True, "csrf_token": csrf(service, token), **(extra or {})},
        status_code=status,
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=service.settings.env == "production",
        samesite="strict",
        max_age=service.settings.web_session_ttl_seconds,
        path="/",
    )
    return response


def install_sessions(app, service):
    from .accounts import Accounts, Credentials, PasswordBody, RecoveryBody, SetupBody, json_origin

    accounts = Accounts(service)

    @app.get("/app/setup")
    def setup_state():
        return {"setup_required": accounts.setup_required()}

    @app.post("/app/setup", status_code=201)
    def setup(body: SetupBody, request: Request):
        return accounts.setup(body, request)

    @app.post("/app/session")
    def login(body: Credentials | LegacyLoginBody, request: Request):
        if isinstance(body, Credentials):
            return accounts.login(body, request)
        # Existing provisioned clients retain their transport contract; the human UI uses passwords.
        json_origin(service, request)
        accounts.throttle(request, "login")
        p = service.authenticate(body.token)
        if p.role != "client":
            raise Error("auth.scope_mismatch", "Workspace client required", 403)
        with service.sessions.begin() as db:
            response = issue_session(service, db, p.client_id, request)
        return response

    @app.post("/app/recover")
    def recover(body: RecoveryBody, request: Request):
        return accounts.recover(body, request)

    @app.post("/app/recovery-token")
    def rotate(body: PasswordBody, request: Request):
        return accounts.rotate_recovery(body, request, resolve(service, request, mutation=True))

    @app.get("/app/session")
    def identity(request: Request):
        p = resolve(service, request)
        with service.sessions() as db:
            caller, account = db.get(Client, p.client_id), db.get(Account, p.client_id)
            return {
                "authenticated": True,
                "name": account.username if account else caller.name,
                "username": account.username if account else None,
                "csrf_token": csrf(service, request.cookies[COOKIE]),
                "upload_max_bytes": min(service.settings.upload_max_bytes, 64 * 1024 * 1024),
            }

    @app.delete("/app/session")
    def logout(request: Request):
        check_origin(service, request)
        token = request.cookies.get(COOKIE, "")
        if token and not hmac.compare_digest(
            request.headers.get("x-wks-csrf", ""), csrf(service, token)
        ):
            raise Error("auth.csrf_invalid", "Request verification required", 403)
        with service.sessions.begin() as db:
            session = db.get(WebSession, hashlib.sha256(token.encode()).hexdigest())
            if session:
                db.delete(session)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/")
        return response
