import hashlib
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from wks_api.authentication.models import Account, AuthAttempt, WebSession
from wks_api.authentication.passwords import password_matches
from wks_api.authentication.session import COOKIE
from wks_api.http.app import create_app
from wks_core.storage.database import Client

PASSWORD = "a valid test password 2026"
NEW_PASSWORD = "a different test password 2026"


def setup(env, username="engineer"):
    response = env["http"].post("/app/setup", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201
    return response.json()


def test_empty_installation_guides_setup_despite_integration_clients(env):
    assert env["http"].get("/app/setup").json() == {"setup_required": True}
    assert env["http"].get("/app/session").status_code == 401
    result = setup(env, "  Engineer  ")
    assert result["username"] == "engineer" and result["recovery_token"].startswith("wks-recovery-")
    assert env["http"].get("/app/setup").json() == {"setup_required": False}
    identity = env["http"].get("/app/session").json()
    assert identity["username"] == "engineer" and "recovery_token" not in identity
    with env["s"].sessions() as db:
        account = db.scalar(select(Account))
        assert account.password_hash != PASSWORD and password_matches(
            PASSWORD, account.password_hash
        )
        assert (
            account.recovery_token_hash
            == hashlib.sha256(result["recovery_token"].encode()).hexdigest()
        )
        assert result["recovery_token"] not in repr(account)
        assert account.client_id != env["a_id"]
    assert env["http"].get("/v1/namespaces").json()["items"] == []


def test_existing_account_closes_setup_and_preserves_identity(env):
    setup(env)
    before = env["http"].cookies.get(COOKIE)
    response = env["http"].post("/app/setup", json={"username": "second", "password": NEW_PASSWORD})
    assert response.status_code == 409 and response.json()["code"] == "auth.setup_complete"
    assert env["http"].cookies.get(COOKIE) == before
    with env["s"].sessions() as db:
        assert db.scalar(select(func.count()).select_from(Account)) == 1
        assert db.scalar(select(func.count()).select_from(Client)) == 4


def test_username_password_login_and_generic_invalid_credentials(env):
    setup(env)
    for username, password in [("engineer", "incorrect"), ("unknown", PASSWORD)]:
        response = env["http"].post(
            "/app/session", json={"username": username, "password": password}
        )
        assert response.status_code == 401 and response.json()["code"] == "auth.invalid_credentials"
        assert password not in response.text
    response = env["http"].post("/app/session", json={"username": "ENGINEER", "password": PASSWORD})
    assert response.status_code == 200 and "recovery_token" not in response.json()
    assert env["http"].get("/app/session").json()["username"] == "engineer"


def test_weak_password_and_validation_responses_never_echo_credentials(env):
    for password in ["weak.secret", " " * 12, "x" * 129]:
        response = env["http"].post(
            "/app/setup", json={"username": "engineer", "password": password}
        )
        assert response.status_code == 422
        assert password not in response.text
    assert env["http"].get("/app/setup").json()["setup_required"]
    oversized = env["http"].post(
        "/app/recover", content=b"x" * 8193, headers={"Content-Type": "application/json"}
    )
    assert oversized.status_code == 413


def test_account_entry_points_reject_cross_origin_requests(env):
    for path, body in [
        ("/app/setup", {"username": "engineer", "password": PASSWORD}),
        ("/app/session", {"username": "engineer", "password": PASSWORD}),
        ("/app/recover", {"recovery_token": "arbitrary", "password": NEW_PASSWORD}),
    ]:
        response = env["http"].post(path, json=body, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
    assert env["http"].get("/app/setup").json()["setup_required"]


def test_recovery_token_is_single_use_rotates_password_and_revokes_all_sessions(env):
    result = setup(env)
    cookie_one = env["http"].cookies.get(COOKIE)
    env["http"].post("/app/session", json={"username": "engineer", "password": PASSWORD})
    cookie_two = env["http"].cookies.get(COOKIE)
    recovered = env["http"].post(
        "/app/recover", json={"recovery_token": result["recovery_token"], "password": NEW_PASSWORD}
    )
    assert recovered.status_code == 200
    assert recovered.json()["recovery_token"] != result["recovery_token"]
    for cookie in [cookie_one, cookie_two]:
        assert (
            env["http"].get("/v1/namespaces", headers={"Cookie": COOKIE + "=" + cookie}).status_code
            == 401
        )
    assert (
        env["http"]
        .post("/app/session", json={"username": "engineer", "password": PASSWORD})
        .status_code
        == 401
    )
    assert (
        env["http"]
        .post("/app/session", json={"username": "engineer", "password": NEW_PASSWORD})
        .status_code
        == 200
    )
    assert (
        env["http"]
        .post(
            "/app/recover", json={"recovery_token": result["recovery_token"], "password": PASSWORD}
        )
        .status_code
        == 401
    )
    with env["s"].sessions() as db:
        account = db.scalar(select(Account))
        assert password_matches(NEW_PASSWORD, account.password_hash)
        assert (
            account.recovery_token_hash
            == hashlib.sha256(recovered.json()["recovery_token"].encode()).hexdigest()
        )


def test_invalid_recovery_never_changes_password_or_returns_an_account(env):
    setup(env)
    response = env["http"].post(
        "/app/recover", json={"recovery_token": "invalid", "password": NEW_PASSWORD}
    )
    assert response.status_code == 401 and response.json()["code"] == "auth.invalid_recovery"
    assert "username" not in response.json()
    assert (
        env["http"]
        .post("/app/session", json={"username": "engineer", "password": PASSWORD})
        .status_code
        == 200
    )


def test_concurrent_first_account_setup_has_one_winner_across_api_instances(env):
    apps = [create_app(env["s"], env["e"]) for _ in range(2)]

    def attempt(item):
        app, username = item
        with TestClient(app) as client:
            return client.post(
                "/app/setup", json={"username": username, "password": PASSWORD}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, zip(apps, ["first", "second"])))
    assert sorted(statuses) == [201, 409]
    with env["s"].sessions() as db:
        assert db.scalar(select(func.count()).select_from(Account)) == 1


def test_concurrent_recovery_consumes_one_token_once(env):
    result = setup(env)

    apps = [create_app(env["s"], env["e"]) for _ in range(2)]

    def attempt(item):
        app, password = item
        with TestClient(app) as client:
            return client.post(
                "/app/recover",
                json={"recovery_token": result["recovery_token"], "password": password},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, zip(apps, [NEW_PASSWORD, "another valid test password"])))
    assert sorted(statuses) == [200, 401]


def test_login_throttle_is_shared_across_api_instances_and_does_not_store_input(env):
    for _ in range(20):
        assert (
            env["http"]
            .post("/app/session", json={"username": "unknown", "password": PASSWORD})
            .status_code
            == 401
        )
    with TestClient(create_app(env["s"], env["e"])) as other:
        response = other.post("/app/session", json={"username": "unknown", "password": PASSWORD})
    assert response.status_code == 429 and response.json()["retryable"]
    with env["s"].sessions() as db:
        bucket = db.scalar(select(AuthAttempt))
        assert len(bucket.key_hash) == 64 and PASSWORD not in bucket.key_hash


def test_recovery_rotation_requires_session_csrf_and_password(env):
    created = setup(env)
    assert env["http"].post("/app/recovery-token", json={"password": PASSWORD}).status_code == 403
    headers = {"X-WKS-CSRF": created["csrf_token"]}
    assert (
        env["http"]
        .post("/app/recovery-token", json={"password": "incorrect"}, headers=headers)
        .status_code
        == 401
    )
    response = env["http"].post("/app/recovery-token", json={"password": PASSWORD}, headers=headers)
    assert response.status_code == 200
    assert (
        env["http"]
        .post(
            "/app/recover",
            json={"recovery_token": created["recovery_token"], "password": NEW_PASSWORD},
        )
        .status_code
        == 401
    )
    assert (
        env["http"]
        .post(
            "/app/recover",
            json={"recovery_token": response.json()["recovery_token"], "password": NEW_PASSWORD},
        )
        .status_code
        == 200
    )


def test_disabled_account_cannot_login_or_be_reactivated_by_recovery(env):
    created = setup(env)
    with env["s"].sessions.begin() as db:
        account = db.scalar(select(Account))
        db.get(Client, account.client_id).active = False
    assert env["http"].get("/app/session").status_code == 401
    assert (
        env["http"]
        .post("/app/session", json={"username": "engineer", "password": PASSWORD})
        .status_code
        == 401
    )
    assert (
        env["http"]
        .post(
            "/app/recover",
            json={"recovery_token": created["recovery_token"], "password": NEW_PASSWORD},
        )
        .status_code
        == 401
    )


def test_production_account_cookie_is_secure_and_origin_is_canonical(env):
    env["s"].settings.env = "production"
    env["s"].settings.web_public_origin = "https://knowledge.example"
    with TestClient(create_app(env["s"], env["e"]), base_url="https://knowledge.example") as client:
        response = client.post(
            "/app/setup",
            json={"username": "engineer", "password": PASSWORD},
            headers={"Origin": "https://knowledge.example"},
        )
        assert response.status_code == 201
        assert all(
            flag in response.headers["set-cookie"]
            for flag in ["Secure", "HttpOnly", "SameSite=strict"]
        )
        assert client.get("/app/session").status_code == 200
        with env["s"].sessions() as db:
            assert len(db.scalars(select(WebSession)).all()) == 1
