from conftest import register_text
from wks_core.storage.migration import migrate
from wks_core.storage.storage import FilesystemStore


def test_explicit_migration_preserves_original_and_is_resumable(env):
    receipt = register_text(env)
    original = env["s"].binary(env["a"], sid=receipt["source_id"], vid=receipt["source_version_id"])
    target = FilesystemStore(env["tmp"] / "destination")
    result = migrate(env["s"], target, page_size=1)
    assert result["objects_migrated"] == 1 and result["original_objects_removed"] is False
    assert target.verify_checksum(original["key"], original["sha256"], original["size"])
    assert env["s"].store.exists(original["key"])
    assert migrate(env["s"], target)["objects_migrated"] == 0
