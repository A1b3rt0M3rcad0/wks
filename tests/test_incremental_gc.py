from datetime import timedelta
from uuid import uuid4

from wks_core.storage.collection import garbage_collect
from wks_core.storage.database import now
from wks_core.storage.stabilization import OrphanCandidate
from wks_core.storage.storage import FilesystemStore


def test_native_directory_cursor_resumes_without_duplicates(tmp_path):
    store = FilesystemStore(tmp_path / "objects")
    raw = tmp_path / "input"
    raw.write_bytes(b"owned bytes")
    for namespace in ("first", "second"):
        for number in range(27):
            store.put_file(f"{namespace}/file-{number}", raw)
    values = []
    cursor = None
    while True:
        result = store.list_prefix_page(cursor=cursor, page_size=7)
        assert len(result["keys"]) <= 7
        values.extend(result["keys"])
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert len(values) == 54 and len(set(values)) == 54


def test_orphan_requires_quarantine_and_live_original_is_preserved(env):
    from conftest import register_text

    receipt = register_text(env)
    raw = env["tmp"] / "orphan"
    raw.write_bytes(b"unreferenced staging bytes")
    key = env["ns_a"] + "/" + str(uuid4())
    env["s"].store.put_file(key, raw)
    first = garbage_collect(env["s"], grace_seconds=60)
    assert first["objects_removed"] == 0 and env["s"].store.exists(key)
    with env["s"].sessions.begin() as db:
        candidate = db.get(OrphanCandidate, key)
        assert candidate
        candidate.observed_at = now() - timedelta(seconds=120)
    second = garbage_collect(env["s"], grace_seconds=60)
    assert second["objects_removed"] == 1 and not env["s"].store.exists(key)
    assert env["s"].binary(env["a"], sid=receipt["source_id"], vid=receipt["source_version_id"])[
        "sha256"
    ]
