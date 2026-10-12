import hashlib
from datetime import timedelta

from sqlalchemy import func, select
from wks_core.storage.database import Blob, Upload, now
from wks_core.storage.stabilization import UploadPart


def start(env, raw, key="resume"):
    return env["s"].register(
        env["a"],
        key,
        {
            "namespace_id": env["ns_a"],
            "title": "Resumable source",
            "kind": "upload",
            "upload": {
                "filename": "resume.txt",
                "declared_media_type": "text/plain",
                "byte_size": len(raw),
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            },
        },
    )


def send(env, uid, offset, raw):
    return env["http"].put(
        f"/v1/uploads/{uid}/parts",
        params={"offset": offset, "checksum_sha256": hashlib.sha256(raw).hexdigest()},
        content=raw,
        headers=env["headers"],
    )


def test_offset_resume_replay_final_sha_and_single_version(env):
    env["s"].settings.upload_min_part_bytes = 1
    raw = b"A complete resumable original with lexical searchable content."
    env["s"].settings.namespace_max_bytes = len(raw)
    receipt = start(env, raw)
    uid = receipt["upload_id"]
    half = len(raw) // 2
    first = send(env, uid, 0, raw[:half])
    assert first.status_code == 200
    assert first.json()["offset"] == half
    assert send(env, uid, 0, raw[:half]).json()["offset"] == half
    assert send(env, uid, 0, b"wrong").status_code == 409
    assert send(env, uid, half + 1, raw[half:]).status_code == 409
    status = env["http"].get(f"/v1/uploads/{uid}", headers=env["headers"])
    assert status.json()["offset"] == half
    assert send(env, uid, half, raw[half:]).status_code == 200
    committed = env["http"].post(
        f"/v1/uploads/{uid}/commit", headers=env["headers"] | {"Idempotency-Key": "commit"}
    )
    assert committed.status_code == 200
    replay = env["http"].post(
        f"/v1/uploads/{uid}/commit", headers=env["headers"] | {"Idempotency-Key": "commit"}
    )
    assert replay.json() == committed.json()
    original = env["http"].get(
        f"/v1/sources/{receipt['source_id']}/versions/{receipt['source_version_id']}/original",
        headers=env["headers"],
    )
    assert original.content == raw
    source = env["s"].source_get(env["a"], receipt["source_id"])
    assert len(source["versions"]) == 1
    assert source["versions"][0]["checksum"] == hashlib.sha256(raw).hexdigest()
    with env["s"].sessions() as db:
        assert db.scalar(select(func.count(UploadPart.offset))) == 2
        assert db.scalar(select(func.count(Blob.id))) == 1


def test_aborted_expired_and_cross_namespace_parts_never_commit(env):
    env["s"].settings.upload_min_part_bytes = 1
    raw = b"guarded content"
    uid = start(env, raw)["upload_id"]
    denied = env["http"].put(
        f"/v1/uploads/{uid}/parts",
        params={"offset": 0, "checksum_sha256": hashlib.sha256(raw).hexdigest()},
        content=raw,
        headers={"Authorization": "Bearer " + env["b_token"]},
    )
    assert denied.status_code == 404
    assert send(env, uid, 0, raw[:4]).status_code == 200
    aborted = env["http"].post(
        f"/v1/uploads/{uid}/abort", headers=env["headers"] | {"Idempotency-Key": "abort"}
    )
    assert aborted.status_code == 200
    assert send(env, uid, 4, raw[4:]).status_code == 410
    assert (
        env["http"]
        .post(f"/v1/uploads/{uid}/commit", headers=env["headers"] | {"Idempotency-Key": "commit"})
        .status_code
        == 410
    )
    uid = start(env, raw, key="expired")["upload_id"]
    with env["s"].sessions.begin() as db:
        db.get(Upload, uid).expires_at = now() - timedelta(seconds=1)
    assert send(env, uid, 0, raw).status_code == 410


def test_empty_upload_commits_with_empty_sha(env):
    receipt = start(env, b"")
    result = env["http"].post(
        f"/v1/uploads/{receipt['upload_id']}/commit",
        headers=env["headers"] | {"Idempotency-Key": "empty-commit"},
    )
    assert result.status_code == 200
    assert (
        env["s"].source_get(env["a"], receipt["source_id"])["versions"][0]["checksum"]
        == hashlib.sha256(b"").hexdigest()
    )
