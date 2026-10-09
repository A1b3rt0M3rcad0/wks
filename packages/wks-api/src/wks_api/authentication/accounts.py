"""First-account setup, password login and atomic recovery for the human workspace."""

from datetime import timedelta

from fastapi import Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from wks_core.domain.models import Error, new_id
from wks_core.storage.database import Client, now

from .models import Account, AuthAttempt, WebSession
from .passwords import DUMMY_PASSWORD, password_hash, password_matches, recovery_token, token_hash


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("username", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class SetupBody(Credentials):
    password: str = Field(min_length=12, max_length=128, repr=False)

    @field_validator("password")
    @classmethod
    def nonempty_password(cls, value):
        if not value.strip():
            raise ValueError("A password must not consist of whitespace")
        return value


class RecoveryBody(BaseModel):
    recovery_token: str = Field(min_length=1, max_length=256, repr=False)
    password: str = Field(min_length=12, max_length=128, repr=False)
    _nonempty = field_validator("password")(SetupBody.nonempty_password.__func__)


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=128, repr=False)


def json_origin(service, request: Request):
    from .session import check_origin

    check_origin(service, request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise Error("contract.invalid", "JSON request required", 415)


class Accounts:
    def __init__(self, service):
        self.service = service

    def setup_required(self):
        with self.service.sessions() as db:
            return db.scalar(select(Account.client_id).limit(1)) is None

    def throttle(self, request, action, limit=20):
        # Request.client respects only the proxy addresses explicitly trusted by Uvicorn.
        address = request.client.host if request.client else "unknown"
        key = token_hash(action + ":" + address)
        instant, cutoff = now(), now() - timedelta(minutes=5)
        statement = insert(AuthAttempt).values(key_hash=key, window_started=instant, attempts=1)
        expired = AuthAttempt.window_started <= cutoff
        statement = statement.on_conflict_do_update(
            index_elements=[AuthAttempt.key_hash],
            set_={
                "attempts": case((expired, 1), else_=AuthAttempt.attempts + 1),
                "window_started": case((expired, instant), else_=AuthAttempt.window_started),
            },
        ).returning(AuthAttempt.attempts)
        with self.service.sessions.begin() as db:
            db.execute(
                delete(AuthAttempt).where(AuthAttempt.window_started < cutoff - timedelta(days=1))
            )
            attempts = db.scalar(statement)
        if attempts > limit:
            raise Error("auth.rate_limited", "Try again later", 429, retryable=True)

    def _client(self, db, account):
        client = db.get(Client, account.client_id) if account else None
        if not client or not client.active or client.role != "client":
            raise Error("auth.invalid_credentials", "Invalid credentials", 401)
        return client

    def setup(self, body, request):
        from .session import issue_session

        json_origin(self.service, request)
        self.throttle(request, "setup", 10)
        # Serialize the empty-installation decision across all API workers and replicas.
        with self.service.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            db.execute(text("SELECT pg_advisory_xact_lock(894031)"))
            if db.scalar(select(Account.client_id).limit(1)):
                raise Error("auth.setup_complete", "First account already exists", 409)
            client = Client(
                name="web-account-" + new_id(),
                token_hash=token_hash(recovery_token()),
                role="client",
                audience="wks",
                active=True,
            )
            db.add(client)
            db.flush()
            secret = recovery_token()
            db.add(
                Account(
                    client_id=client.id,
                    username=body.username,
                    password_hash=password_hash(body.password),
                    recovery_token_hash=token_hash(secret),
                )
            )
            response = issue_session(
                self.service,
                db,
                client.id,
                request,
                {"username": body.username, "recovery_token": secret},
                status=201,
            )
        return response

    def login(self, body, request):
        from .session import issue_session

        json_origin(self.service, request)
        self.throttle(request, "login")
        with self.service.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            account = db.scalar(
                select(Account).where(Account.username == body.username).with_for_update()
            )
            valid = password_matches(
                body.password, account.password_hash if account else DUMMY_PASSWORD
            )
            if not valid or not account:
                raise Error("auth.invalid_credentials", "Invalid credentials", 401)
            client = self._client(db, account)
            response = issue_session(
                self.service, db, client.id, request, {"username": account.username}
            )
        return response

    def recover(self, body, request):
        from .session import issue_session

        json_origin(self.service, request)
        self.throttle(request, "recover", 5)
        with self.service.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            account = db.scalar(
                select(Account)
                .where(Account.recovery_token_hash == token_hash(body.recovery_token))
                .with_for_update()
            )
            if not account:
                raise Error("auth.invalid_recovery", "Invalid recovery token", 401)
            client = self._client(db, account)
            secret = recovery_token()
            account.password_hash = password_hash(body.password)
            account.recovery_token_hash = token_hash(secret)
            client.token_hash = token_hash(recovery_token())
            db.execute(delete(WebSession).where(WebSession.client_id == client.id))
            response = issue_session(
                self.service,
                db,
                client.id,
                request,
                {"username": account.username, "recovery_token": secret},
            )
        return response

    def rotate_recovery(self, body, request, principal):
        json_origin(self.service, request)
        self.throttle(request, "rotate", 5)
        with self.service.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            account = db.get(Account, principal.client_id, with_for_update=True)
            valid = password_matches(
                body.password, account.password_hash if account else DUMMY_PASSWORD
            )
            if not valid or not account:
                raise Error("auth.invalid_credentials", "Invalid credentials", 401)
            self._client(db, account)
            secret = recovery_token()
            account.recovery_token_hash = token_hash(secret)
        return {"username": account.username, "recovery_token": secret}
