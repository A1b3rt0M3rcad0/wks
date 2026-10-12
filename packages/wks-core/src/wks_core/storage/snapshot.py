"""MVCC snapshots with durable blob pins and privacy replay, without long write locks."""

import json
import subprocess
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from wks_core.application.utilities import file_digest
from wks_core.domain.models import new_id
from wks_core.storage.database import now
from wks_core.storage.schema import LATEST_SCHEMA
from wks_core.storage.stabilization import BackupSnapshot
from wks_core.version import build_info


def backup(service, destination, pg_container=None):
    from wks_core.storage.maintenance import pg_command

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    (root / "objects").mkdir(mode=0o700)
    identity = new_id()
    engine = service.sessions.kw["bind"]
    connection = engine.connect()
    locked = False
    try:
        connection.execute(text("SELECT pg_advisory_lock(874201)"))
        connection.commit()
        locked = True
        with service.sessions.begin() as db:
            db.add(
                BackupSnapshot(id=identity, state="copying", expires_at=now() + timedelta(hours=24))
            )
            db.flush()
            db.execute(
                text(
                    """INSERT INTO backup_pins(snapshot_id,blob_id) SELECT :snapshot,b.id FROM blob_objects b WHERE
                        EXISTS(SELECT 1 FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.original_blob_id=b.id AND s.deleted_at IS NULL)
                        OR EXISTS(SELECT 1 FROM assets a JOIN representations r ON r.id=a.representation_id JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id WHERE a.blob_id=b.id AND s.deleted_at IS NULL)
                        OR EXISTS(SELECT 1 FROM export_artifacts e JOIN representations r ON r.id=e.representation_id JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id WHERE e.blob_id=b.id AND s.deleted_at IS NULL)
                        OR EXISTS(SELECT 1 FROM upload_sessions u JOIN source_versions v ON v.id=u.source_version_id JOIN sources s ON s.id=v.source_id WHERE u.staged_blob_id=b.id AND u.status='received' AND u.expires_at>now() AND s.deleted_at IS NULL)"""
                ),
                {"snapshot": identity},
            )
        connection = connection.execution_options(isolation_level="REPEATABLE READ")
        transaction = connection.begin()
        snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
        connection.execute(text("SELECT pg_advisory_unlock(874201)"))
        locked = False
        watermark = service.privacy_ledger.watermark()
        argv, environment = pg_command("pg_dump", service.settings.database_url, pg_container)
        with (root / "database.dump").open("wb") as stream:
            subprocess.run(
                argv + ["--format=custom", "--no-owner", "--no-acl", "--snapshot=" + snapshot],
                env=environment,
                stdout=stream,
                stderr=subprocess.PIPE,
                check=True,
            )
        metadata = {
            "application": build_info(),
            "created_at": now().isoformat(),
            "policy": {
                "storage_provider": service.settings.storage_provider,
                "gc_grace_seconds": service.settings.gc_grace_seconds,
                "receipt_retention_days": service.settings.receipt_retention_days,
            },
            "schema_version": LATEST_SCHEMA,
            "snapshot_id": identity,
            "database_sha256": file_digest(root / "database.dump"),
            "privacy_ledger_watermark": watermark,
            "privacy_ledger_path": str(service.privacy_ledger.root),
            "object_manifest_file": "objects.jsonl",
            "objects": [],
        }
        count = 0
        query = text(
            "SELECT b.id,b.storage_key,b.sha256,b.size FROM blob_objects b JOIN backup_pins p ON p.blob_id=b.id WHERE p.snapshot_id=:snapshot ORDER BY b.id"
        )
        with (root / "objects.jsonl").open("w") as manifest:
            rows = connection.execution_options(stream_results=True).execute(
                query, {"snapshot": identity}
            )
            for blob_id, key, sha, size in rows:
                if not service.store.exists(key):
                    active = connection.scalar(
                        text(
                            """SELECT EXISTS(SELECT 1 FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.original_blob_id=:id AND s.deleted_at IS NULL UNION ALL SELECT 1 FROM assets WHERE blob_id=:id UNION ALL SELECT 1 FROM export_artifacts WHERE blob_id=:id)"""
                        ),
                        {"id": blob_id},
                    )
                    if active:
                        raise RuntimeError("Active original missing; snapshot refused")
                    continue
                path = root / "objects" / blob_id
                service.store.materialize(key, path)
                if file_digest(path) != sha or path.stat().st_size != size:
                    raise RuntimeError("Original checksum mismatch; snapshot refused")
                item = {"id": blob_id, "key": key, "sha256": sha, "size": size}
                manifest.write(json.dumps(item) + "\n")
                # Keep the 0.3 manifest form for small backups; large corpora use streaming JSONL.
                if count < 1000:
                    metadata["objects"].append(item)
                else:
                    metadata["objects"] = []
                count += 1
        metadata["object_count"] = count
        metadata["object_manifest_sha256"] = file_digest(root / "objects.jsonl")
        (root / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (root / "manifest.sha256").write_text(file_digest(root / "manifest.json") + "\n")
        transaction.commit()
        return {
            "directory": str(root),
            "snapshot_id": identity,
            "objects": count,
            "database_sha256": metadata["database_sha256"],
            "write_lock": "snapshot_selection_only",
        }
    finally:
        if locked:
            try:
                connection.execute(text("SELECT pg_advisory_unlock(874201)"))
                connection.commit()
            except Exception:
                pass
        connection.close()
        with service.sessions.begin() as db:
            record = db.get(BackupSnapshot, identity)
            if record:
                record.state = "complete"
            db.execute(
                text("DELETE FROM backup_pins WHERE snapshot_id=:snapshot"), {"snapshot": identity}
            )


def manifest_objects(root, manifest):
    if manifest.get("object_manifest_file"):
        if manifest["object_manifest_file"] != "objects.jsonl":
            raise RuntimeError("Invalid object manifest path")
        path = root / "objects.jsonl"
        if file_digest(path) != manifest["object_manifest_sha256"]:
            raise RuntimeError("Object manifest checksum mismatch")
        with path.open() as stream:
            for line in stream:
                yield json.loads(line)
    else:
        yield from manifest["objects"]


def restore(service, source, pg_container=None, ledger_directory=None):
    from uuid import UUID

    from wks_core.storage.maintenance import pg_command
    from wks_core.storage.privacy import PrivacyLedger

    root = Path(source)
    if (root / "manifest.sha256").exists() and file_digest(root / "manifest.json") != (
        root / "manifest.sha256"
    ).read_text().strip():
        raise RuntimeError("Backup manifest checksum mismatch")
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        manifest["schema_version"] != LATEST_SCHEMA
        or file_digest(root / "database.dump") != manifest["database_sha256"]
    ):
        raise RuntimeError("Invalid backup manifest or database checksum")
    for item in manifest_objects(root, manifest):
        UUID(item["id"])
        path = root / "objects" / item["id"]
        if (
            path.is_symlink()
            or file_digest(path) != item["sha256"]
            or path.stat().st_size != item["size"]
        ):
            raise RuntimeError("Invalid object checksum or size")
    with service.sessions() as db:
        if db.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        ):
            raise RuntimeError("Restore requires an empty destination database")
    ledger_path = Path(ledger_directory or manifest.get("privacy_ledger_path", ""))
    if not ledger_path.is_dir() or not (ledger_path / "signing-key").is_file():
        raise RuntimeError("Live external privacy ledger is required before restoring")
    ledger = PrivacyLedger(service, ledger_path)
    # Validate all signed records before changing the destination.
    for _ in ledger.events():
        pass
    marker = service.settings.storage_path.parent / (
        "restore-blocked-"
        + __import__("hashlib")
        .sha256(service.settings.database_url.rsplit("/", 1)[-1].encode())
        .hexdigest()[:16]
    )
    marker.write_text("Restore verification pending\n")
    service.restore_blocked = True
    argv, environment = pg_command("pg_restore", service.settings.database_url, pg_container)
    with (root / "database.dump").open("rb") as stream:
        subprocess.run(
            argv + ["--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"],
            env=environment,
            stdin=stream,
            stderr=subprocess.PIPE,
            check=True,
        )
    count = 0
    for item in manifest_objects(root, manifest):
        if not service.store.exists(item["key"]):
            service.store.put_file(item["key"], root / "objects" / item["id"])
        if not service.store.verify_checksum(item["key"], item["sha256"], item["size"]):
            raise RuntimeError("Restored object checksum mismatch")
        count += 1
    with service.sessions.begin() as db:
        from sqlalchemy import update
        from wks_core.storage.database import Blob

        db.execute(
            update(Blob).values(
                provider=service.settings.storage_provider, location=service.store.location()
            )
        )
        db.execute(text("DELETE FROM web_sessions"))
        db.execute(text("DELETE FROM web_auth_attempts"))
        # Only received whole objects and committed originals belong to the
        # snapshot's pins. Temporary multipart offsets must not survive without
        # their bytes and falsely advertise resumability after disaster recovery.
        db.execute(
            text(
                "UPDATE upload_sessions SET status='expired',received_offset=0,expires_at=now() WHERE status='pending'"
            )
        )
        db.execute(text("UPDATE backup_snapshots SET state='complete'"))
        db.execute(text("DELETE FROM backup_pins"))
    replayed = ledger.replay(service, after=manifest.get("privacy_ledger_watermark"))
    marker.unlink()
    service.restore_blocked = False
    return {
        "objects_restored": count,
        "schema_version": LATEST_SCHEMA,
        "privacy_events_replayed": replayed,
    }
