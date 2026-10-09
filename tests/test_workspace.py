import ast
import hashlib
from datetime import timedelta
from pathlib import Path

from conftest import process_all, register_text
from sqlalchemy import select
from wks_api.authentication.session import COOKIE, WebSession
from wks_core.storage.database import Client, now


def login(env):
    response = env["http"].post("/app/session", json={"token": env["a_token"]})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_web_session_private_cookie_and_owner_scoped_catalog(env):
    csrf = login(env)
    cookie = env["http"].cookies.get(COOKIE)
    assert env["a_token"] not in cookie
    assert env["a_token"] not in env["http"].get("/app/session").text
    with env["s"].sessions() as db:
        session = db.get(WebSession, hashlib.sha256(cookie.encode()).hexdigest())
        assert session.client_id == env["a_id"]
    namespaces = env["http"].get("/v1/namespaces").json()
    assert [n["id"] for n in namespaces["items"]] == [env["ns_a"]]
    for path in ("summary", "collections"):
        assert env["http"].get("/v1/namespaces/" + env["ns_b"] + "/" + path).status_code == 404
    assert env["http"].get("/v1/namespaces/" + env["ns_a"] + "/summary").status_code == 200
    response = env["http"].post(
        "/v1/collections",
        json={"namespace_id": env["ns_a"], "title": "UI"},
        headers={"X-WKS-CSRF": csrf, "Idempotency-Key": "ui-collection"},
    )
    assert response.status_code == 200
    assert (
        env["http"]
        .get("/v1/namespaces/" + env["ns_a"] + "/collections")
        .json()["items"][0]["title"]
        == "UI"
    )


def test_browser_mutations_require_csrf_and_same_origin(env):
    response = env["http"].post(
        "/app/session", json={"token": env["a_token"]}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    csrf = login(env)
    body = {"title": "Unsafe"}
    assert (
        env["http"]
        .post("/v1/namespaces", json=body, headers={"Idempotency-Key": "unsafe"})
        .status_code
        == 403
    )
    assert (
        env["http"]
        .post(
            "/v1/namespaces",
            json=body,
            headers={
                "Idempotency-Key": "unsafe",
                "X-WKS-CSRF": csrf,
                "Origin": "https://evil.example",
            },
        )
        .status_code
        == 403
    )
    assert (
        env["http"]
        .post(
            "/v1/namespaces",
            json=body,
            headers={"Idempotency-Key": "safe", "X-WKS-CSRF": csrf, "Origin": "http://testserver"},
        )
        .status_code
        == 200
    )


def test_logout_revokes_cookie_and_client_disable_is_immediate(env):
    csrf = login(env)
    cookie = env["http"].cookies.get(COOKIE)
    assert env["http"].delete("/app/session", headers={"X-WKS-CSRF": csrf}).status_code == 200
    assert (
        env["http"].get("/v1/namespaces", headers={"Cookie": COOKIE + "=" + cookie}).status_code
        == 401
    )
    login(env)
    with env["s"].sessions.begin() as db:
        db.get(Client, env["a_id"]).active = False
    assert env["http"].get("/v1/namespaces").status_code == 401


def test_expired_browser_session_is_denied(env):
    login(env)
    with env["s"].sessions.begin() as db:
        db.scalar(select(WebSession)).expires_at = now() - timedelta(seconds=1)
    assert env["http"].get("/app/session").status_code == 401
    assert env["http"].get("/v1/namespaces").status_code == 401


def test_namespace_discovery_pagination_and_summary_counts(env):
    s = env["s"]
    s.namespace_create(env["a"], "second", {"title": "Another"})
    first = s.namespace_list(env["a"], 1)
    second = s.namespace_list(env["a"], 1, first["next_cursor"])
    assert first["items"][0]["id"] != second["items"][0]["id"] and not second["next_cursor"]
    register_text(env)
    register_text(env, user="b")
    process_all(env)
    assert s.namespace_summary(env["a"], env["ns_a"])["total_sources"] == 1
    listed = (
        env["http"]
        .get(
            "/v1/sources",
            params={"namespace_id": env["ns_a"], "availability": "text_ready"},
            headers=env["headers"],
        )
        .json()
    )
    assert len(listed["items"]) == 1
    assert (
        env["http"]
        .get(
            "/v1/sources",
            params={"namespace_id": env["ns_a"], "availability": "metadata_only"},
            headers=env["headers"],
        )
        .json()["items"]
        == []
    )


def test_workspace_assets_have_csp_and_session_cookie_flags(env):
    response = env["http"].get("/app/")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    response = env["http"].post("/app/session", json={"token": env["a_token"]})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert env["http"].get("/app/app.js").status_code == 200


def test_core_never_imports_api_or_transport_frameworks():
    files = list(Path("packages/wks-core/src/wks_core").rglob("*.py"))
    assert files
    for file in files:
        for node in ast.walk(ast.parse(file.read_text())):
            imports = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [name.name for name in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                (name or "").startswith(("wks_api", "fastapi", "mcp", "uvicorn"))
                for name in imports
            ), file
