"""Opaque browser sessions. Credentials remain outside browser storage and tool arguments."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, delete
from sqlalchemy.orm import Mapped, mapped_column
from wks_core.domain.models import Error, Principal
from wks_core.storage.database import Base, Client, now

COOKIE = "wks_session"


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginBody(BaseModel):
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


def install_sessions(app, service):
    @app.post("/app/session")
    def login(body: LoginBody, request: Request):
        from fastapi.responses import JSONResponse

        check_origin(service, request)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise Error("contract.invalid", "JSON request required", 415)
        p = service.authenticate(body.token)
        if p.role != "client":
            raise Error("auth.scope_mismatch", "Workspace client required", 403)
        token = secrets.token_urlsafe(48)
        with service.sessions.begin() as db:
            db.execute(delete(WebSession).where(WebSession.expires_at <= now()))
            old = request.cookies.get(COOKIE)
            previous = db.get(WebSession, hashlib.sha256(old.encode()).hexdigest()) if old else None
            if previous:
                db.delete(previous)
            db.add(
                WebSession(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    client_id=p.client_id,
                    expires_at=now() + timedelta(seconds=service.settings.web_session_ttl_seconds),
                )
            )
        response = JSONResponse({"authenticated": True, "csrf_token": csrf(service, token)})
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

    @app.get("/app/session")
    def identity(request: Request):
        p = resolve(service, request)
        with service.sessions() as db:
            caller = db.get(Client, p.client_id)
            return {
                "authenticated": True,
                "name": caller.name,
                "csrf_token": csrf(service, request.cookies[COOKIE]),
                "upload_max_bytes": min(service.settings.upload_max_bytes, 64 * 1024 * 1024),
            }

    @app.delete("/app/session")
    def logout(request: Request):
        from fastapi.responses import JSONResponse

        check_origin(service, request)
        token = request.cookies.get(COOKIE, "")
        # Origin and CSRF are still required for an expired session's cookie cleanup.
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
