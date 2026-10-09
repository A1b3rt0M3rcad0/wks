import json
import os
import shutil
import subprocess
from datetime import timedelta

import pytest
from conftest import process_all, register_text
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from wks_api.authentication.models import Account, AuthAttempt
from wks_api.authentication.passwords import password_hash, token_hash
from wks_api.authentication.session import WebSession
from wks_api.http.app import create_app
from wks_api.server.bootstrap import build
from wks_core.domain.models import new_id
from wks_core.storage.database import now
from wks_core.storage.maintenance import backup, reindex, restore


def test_A35_coordinated_backup_restore_same_refs_acl(env):
    container = os.environ.get("WKS_PG_TEST_CONTAINER")
    if not container and not shutil.which("pg_dump"):
        container = "wks-postgres"
    r = register_text(env)
    other = register_text(env, user="b", title="Private B")
    process_all(env)
    original = env["s"].search(env["a"], {"query": "paralelo"})
    with env["s"].sessions.begin() as db:
        db.add(
            Account(
                client_id=env["a"].client_id,
                username="restored-account",
                password_hash=password_hash("test-backup-password"),
                recovery_token_hash=token_hash("test-recovery-secret"),
            )
        )
        db.add(AuthAttempt(key_hash="b" * 64, window_started=now(), attempts=20))
        db.add(
            WebSession(
                token_hash="a" * 64,
                client_id=env["a"].client_id,
                expires_at=now() + timedelta(hours=1),
            )
        )
    dest = env["tmp"] / "backup"
    result = backup(env["s"], dest, container)
    assert result["objects"] == 2
    admin_url = env["s"].settings.database_url.rsplit("/", 1)[0] + "/postgres"
    name = "wks_restore_" + new_id().replace("-", "")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    settings = env["s"].settings.model_copy(
        update={
            "database_url": admin_url.rsplit("/", 1)[0] + "/" + name,
            "storage_path": env["tmp"] / "restored-objects",
        }
    )
    s, engine = build(settings)
    try:
        restore(s, dest, container)
        with s.sessions() as db:
            assert db.get(WebSession, "a" * 64) is None
            assert db.get(AuthAttempt, "b" * 64) is None
            assert db.get(Account, env["a"].client_id).recovery_token_hash == token_hash(
                "test-recovery-secret"
            )
        with TestClient(create_app(s, engine)) as http:
            response = http.post(
                "/app/session",
                json={"username": "restored-account", "password": "test-backup-password"},
            )
            assert response.status_code == 200
            assert http.get("/app/session").json()["username"] == "restored-account"
            assert http.get("/v1/namespaces").status_code == 200
        p = s.authenticate(env["a_token"])
        restored = s.search(p, {"query": "paralelo"})
        assert restored["items"] == original["items"]
        from wks_core.domain.models import Error

        with pytest.raises(Error):
            s.source_get(p, other["source_id"])
        assert s.binary(p, sid=r["source_id"], vid=r["source_version_id"])["sha256"]
        assert reindex(s)["segments_reindexed"] > 0
        assert s.search(p, {"query": "paralelo"})["items"] == original["items"]
        manifest = json.loads((dest / "manifest.json").read_text())
        blob = dest / "objects" / manifest["objects"][0]["id"]
        blob.write_bytes(b"tampered")
        with pytest.raises(RuntimeError, match="checksum"):
            restore(s, dest, container)
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def test_A34_recapture_new_version_idempotent(env, monkeypatch):
    from wks_core.storage.capture import capture_source

    s = env["s"]
    s.settings.web_capture_enabled = True
    r = s.register(
        env["a"],
        "bookmark",
        {
            "namespace_id": env["ns_a"],
            "kind": "bookmark",
            "title": "Public page",
            "external_uri": "https://example.com",
        },
    )
    calls = []

    def fake_fetch(uri, path, settings):
        calls.append(uri)
        path.write_text("Captured paralelo " + str(len(calls)))
        return {
            "effective_uri": uri,
            "declared_mime": "text/plain",
            "media_type": "text/plain",
            "status": 200,
        }

    monkeypatch.setattr("wks_core.storage.capture.fetch_public", fake_fetch)
    one = capture_source(s, env["a"], "capture1", r["source_id"])
    assert capture_source(s, env["a"], "capture1", r["source_id"]) == one
    two = capture_source(s, env["a"], "capture2", r["source_id"])
    assert two["source_version_id"] != one["source_version_id"] and len(calls) == 2
    process_all(env)
    assert len(s.source_get(env["a"], r["source_id"])["versions"]) == 3


def test_cursor_snapshot_survives_new_publications(env):
    for i in range(3):
        register_text(env, key=str(i))
    process_all(env)
    body = {"query": "paralelo", "page_size": 1}
    first = env["s"].search(env["a"], body)
    r = register_text(env, key="new")
    process_all(env)
    results = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = env["s"].search(env["a"], body | {"cursor": cursor})
        results += page["items"]
        cursor = page["next_cursor"]
    assert len(results) == 3 and r["source_id"] not in {i["source_id"] for i in results}


def test_worker_timeout_kills_child_group_and_retries(env):
    from wks_worker.worker import Worker

    r = register_text(env)
    env["s"].settings.processing_timeout_seconds = 1
    from unittest.mock import patch

    with patch("wks_worker.worker.subprocess.Popen") as popen:
        proc = popen.return_value
        proc.pid = 99999999
        proc.wait.side_effect = [subprocess.TimeoutExpired("test", 1), 0]
        with patch("wks_worker.worker.os.killpg") as kill:
            Worker(env["s"]).run_once()
        assert kill.called
    status = env["s"].status(env["a"], r["operation_id"])
    assert (
        status["processing_state"] == "retry_wait" and status["error_code"] == "processing.timeout"
    )
