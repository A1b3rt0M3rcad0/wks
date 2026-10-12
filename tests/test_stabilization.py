"""Functional invariants for the stabilization interfaces; real PostgreSQL."""

import json

import pytest
from conftest import process_all, register_text
from sqlalchemy import select
from wks_core.domain.models import Error
from wks_core.storage.database import Client, Receipt
from wks_core.storage.stabilization import RepresentationBlock


def delegated(env, actions, collection=None):
    grant = env["s"].grant_create(
        env["a"],
        "delegate-" + str(len(actions)) + str(collection),
        {
            "caller_binding": env["executor_id"],
            "audience": "wks",
            "namespace_id": env["ns_a"],
            "collection_id": collection,
            "allowed_actions": actions,
            "purpose": "functional verification",
            "ttl_seconds": 300,
        },
    )
    return env["s"].authenticate(env["executor_token"], grant["id"])


def test_namespace_write_grant_cannot_cross_namespace_or_administer(env):
    p = delegated(env, ["source:create", "source:read", "operation:read"])
    body = {
        "namespace_id": env["ns_a"],
        "kind": "text",
        "title": "delegated",
        "text": "Delegated text",
    }
    result = env["s"].register(p, "delegated-source", body)
    assert env["s"].register(p, "delegated-source", body) == result
    assert env["s"].source_get(p, result["source_id"])["namespace_id"] == env["ns_a"]
    with pytest.raises(Error):
        env["s"].register(p, "other", {**body, "namespace_id": env["ns_b"]})
    with pytest.raises(Error):
        env["s"].namespace_create(p, "namespace", {"title": "escape"})
    with pytest.raises(Error):
        env["s"].client_create(p, "identity", {"name": "unauthorized"})


def test_receipt_is_bound_to_grant_and_revocation(env):
    p = delegated(env, ["source:create", "operation:read"])
    result = env["s"].register(
        p,
        "write-once",
        {"namespace_id": env["ns_a"], "kind": "text", "title": "once", "text": "persisted"},
    )
    assert env["s"].receipt(p, "write-once") == result
    q = delegated(env, ["source:create", "operation:read", "source:read"])
    with pytest.raises(Error):
        env["s"].receipt(q, "write-once")
    env["s"].grant_revoke(env["a"], "revoke", p.grant_id)
    with pytest.raises(Error):
        env["s"].receipt(p, "write-once")


def test_collection_grant_requires_membership_for_destructive_operations(env):
    cid = env["s"].collection_create(
        env["a"], "collection", {"namespace_id": env["ns_a"], "title": "scope"}
    )["id"]
    outside = register_text(env, key="outside")
    p = delegated(env, ["source:create", "source:delete", "source:read"], cid)
    own = env["s"].register(
        p,
        "inside",
        {"namespace_id": env["ns_a"], "kind": "text", "title": "inside", "text": "content"},
    )
    with pytest.raises(Error):
        env["s"].revoke(p, "cross-collection", outside["source_id"], purge=True)
    with pytest.raises(Error, match="preflight"):
        env["s"].revoke(p, "delete-without-confirmation", own["source_id"], purge=True)
    preflight = env["s"].preflight(p, own["source_id"])
    result = env["s"].revoke(
        p,
        "delete-with-confirmation",
        own["source_id"],
        purge=True,
        confirmation_token=preflight["confirmation_token"],
    )
    assert result["state"] == "deleted"
    assert (
        env["s"].revoke(
            p,
            "delete-with-confirmation",
            own["source_id"],
            purge=True,
            confirmation_token=preflight["confirmation_token"],
        )
        == result
    )


def test_metadata_concurrency_and_original_are_independent(env):
    registered = register_text(env)
    sid, vid = registered["source_id"], registered["source_version_id"]
    previous = env["s"].source_get(env["a"], sid)
    patched = env["http"].patch(
        f"/v1/sources/{sid}",
        headers=env["headers"] | {"Idempotency-Key": "patch", "If-Match": '"1"'},
        json={"title": "New title", "metadata": {"nested": {"items": [1, True, None]}}},
    )
    assert patched.status_code == 200
    assert patched.json()["revision"] == 2
    stale = env["http"].patch(
        f"/v1/sources/{sid}",
        headers=env["headers"] | {"Idempotency-Key": "stale", "If-Match": '"1"'},
        json={"title": "stale"},
    )
    assert stale.status_code == 412
    current = env["s"].source_get(env["a"], sid)
    assert current["versions"][0]["checksum"] == previous["versions"][0]["checksum"]
    assert current["versions"][0]["id"] == vid


def test_normalized_block_exact_resolution_and_revocation(env):
    registered = register_text(env, value="First paragraph.\n\nSecond paragraph.")
    process_all(env)
    rid = env["s"].status(env["a"], registered["operation_id"])["published_representation_id"]
    with env["s"].sessions() as db:
        rows = db.scalars(
            select(RepresentationBlock)
            .where(RepresentationBlock.representation_id == rid)
            .order_by(RepresentationBlock.ordinal)
        ).all()
        assert rows
        target = rows[-1]
        expected = target.value
    chunks, offset = [], 0
    while True:
        result = env["s"].block(env["a"], rid, target.block_id, offset=offset, page_chars=17)
        chunks.append(result["content"])
        if result["complete"]:
            break
        offset = result["next_offset"]
    assert json.loads("".join(chunks)) == expected
    pages, cursor = [], None
    while True:
        page = env["s"].read(env["a"], rid, 1, cursor)
        pages.extend(page["blocks"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert [b["id"] for b in pages] == [b.block_id for b in rows]
    env["s"].revoke(env["a"], "revoke", registered["source_id"])
    with pytest.raises(Error):
        env["s"].block(env["a"], rid, target.block_id)


def test_operator_credentials_are_once_only_and_revalidated(env):
    with pytest.raises(Error):
        env["s"].client_create(env["a"], "not-operator", {"name": "bot"})
    with env["s"].sessions.begin() as db:
        db.get(Client, env["a_id"]).operator = True
    identity = env["s"].client_create(env["a"], "bot", {"name": "bot"})["id"]
    credential = env["s"].credential_create(env["a"], "issue", identity)
    p = env["s"].authenticate(credential["token"])
    assert env["s"].credential_create(env["a"], "issue", identity)["token"] is None
    with env["s"].sessions() as db:
        assert credential["token"] not in json.dumps(db.get(Receipt, (env["a_id"], "issue")).result)
    env["s"].credential_revoke(env["a"], "revoke-credential", credential["id"])
    with pytest.raises(Error):
        env["s"].namespace_list(p)
    with pytest.raises(Error):
        env["s"].authenticate(credential["token"])


def test_backup_replays_deletions_grant_revocations_and_account_recovery(env):
    import os

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from wks_api.http.app import create_app
    from wks_api.server.bootstrap import build
    from wks_core.domain.models import new_id
    from wks_core.storage.maintenance import backup, restore

    http = env["http"]
    created = http.post(
        "/app/setup", json={"username": "operator", "password": "first-password-for-backup"}
    )
    assert created.status_code == 201
    old_recovery = created.json()["recovery_token"]
    registered = register_text(env)
    process_all(env)
    delegate = delegated(env, ["source:read"])
    import hashlib

    unfinished = env["s"].register(
        env["a"],
        "pending-backup-upload",
        {
            "namespace_id": env["ns_a"],
            "kind": "upload",
            "title": "Uncommitted transfer",
            "upload": {
                "filename": "pending.bin",
                "declared_media_type": "application/octet-stream",
                "byte_size": 131072,
                "checksum_sha256": hashlib.sha256(b"X" * 131072).hexdigest(),
            },
        },
    )
    part_file = env["tmp"] / "pending-part"
    part_file.write_bytes(b"X" * 65536)
    env["s"].receive_part(
        env["a"], unfinished["upload_id"], part_file, 0, hashlib.sha256(b"X" * 65536).hexdigest()
    )
    destination = env["tmp"] / "privacy-backup"
    container = os.environ.get("WKS_PG_TEST_CONTAINER", "wks-postgres")
    backup(env["s"], destination, container)
    env["s"].revoke(env["a"], "delete-after-snapshot", registered["source_id"], purge=True)
    env["s"].grant_revoke(env["a"], "revoke-after-snapshot", delegate.grant_id)
    recovered = http.post(
        "/app/recover",
        json={"recovery_token": old_recovery, "password": "new-password-after-snapshot"},
    )
    assert recovered.status_code == 200
    root_url = env["s"].settings.database_url.rsplit("/", 1)[0]
    identity = "wks_privacy_restore_" + new_id().replace("-", "")
    admin = create_engine(root_url + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{identity}"'))
    settings = env["s"].settings.model_copy(
        update={
            "database_url": root_url + "/" + identity,
            "storage_path": env["tmp"] / "privacy-restored",
        }
    )
    restored, engine = build(settings)
    try:
        restore(restored, destination, container)
        owner = restored.authenticate(env["a_token"])
        expired = restored.upload_status(owner, unfinished["upload_id"])
        assert expired["status"] == "expired" and expired["offset"] == 0
        with pytest.raises(Error):
            restored.source_get(owner, registered["source_id"])
        with pytest.raises(Error):
            restored.authenticate(env["executor_token"], delegate.grant_id)
        with TestClient(create_app(restored, engine)) as client:
            assert (
                client.post(
                    "/app/session",
                    json={"username": "operator", "password": "first-password-for-backup"},
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/app/session",
                    json={"username": "operator", "password": "new-password-after-snapshot"},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/app/recover",
                    json={"recovery_token": old_recovery, "password": "invalid-restored-password"},
                ).status_code
                == 401
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{identity}" WITH (FORCE)'))
        admin.dispose()


def test_container_deletion_requires_exact_impact_and_preserves_source_bytes(env):
    s = env["s"]
    source = register_text(env)
    cid = s.collection_create(
        env["a"], "container", {"namespace_id": env["ns_a"], "title": "Grouping"}
    )["id"]
    s.collection_link(env["a"], "link", cid, source["source_id"])
    report = s.container_preflight(env["a"], "collection", cid)
    assert report["source_count"] == 1
    with pytest.raises(Error):
        s.container_delete(env["a"], "no-confirmation", "collection", cid, None)
    s.container_delete(
        env["a"], "delete-container", "collection", cid, report["confirmation_token"]
    )
    assert s.binary(env["a"], sid=source["source_id"], vid=source["source_version_id"])["sha256"]
    namespace_report = s.container_preflight(env["a"], "namespace", env["ns_a"])
    with pytest.raises(Error, match="explicitly"):
        s.container_delete(
            env["a"], "nonempty", "namespace", env["ns_a"], namespace_report["confirmation_token"]
        )
