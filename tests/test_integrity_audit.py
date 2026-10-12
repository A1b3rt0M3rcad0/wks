from conftest import register_text
from wks_core.storage.integrity import audit


def test_missing_original_changes_readiness_until_restored_and_verified(env):
    receipt = register_text(env)
    blob = env["s"].binary(env["a"], sid=receipt["source_id"], vid=receipt["source_version_id"])
    path = env["s"].store.path(blob["key"])
    raw = path.read_bytes()
    path.unlink()
    report = audit(env["s"])
    assert report["outcomes"]["missing"] == 1
    assert env["http"].get("/health/ready").status_code == 503
    path.write_bytes(raw)
    assert audit(env["s"])["outcomes"]["valid"] == 1
    assert env["http"].get("/health/ready").status_code == 200
    path.write_bytes(b"corrupted bytes")
    assert audit(env["s"])["outcomes"]["corrupt"] == 1
    assert env["http"].get("/health/ready").status_code == 503
