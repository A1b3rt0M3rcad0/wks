import json
import os
import subprocess
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from wks.application.service import file_digest
from wks.infrastructure.database import Blob


def pg_command(command, url, pg_container=None):
    u = make_url(url)
    env = os.environ.copy()
    # Pass credentials through environment, never CLI args or logs.
    if pg_container:
        argv = [
            "docker",
            "exec",
            "-i",
            pg_container,
            command,
            "-U",
            u.username or "postgres",
            "-d",
            u.database,
        ]
    else:
        argv = [
            command,
            "-h",
            u.host or "localhost",
            "-p",
            str(u.port or 5432),
            "-U",
            u.username or "postgres",
            "-d",
            u.database,
        ]
        env["PGPASSWORD"] = u.password or ""
    return argv, env


def backup(service, destination, pg_container=None):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    objects = root / "objects"
    objects.mkdir(mode=0o700)
    argv, env = pg_command("pg_dump", service.settings.database_url, pg_container)
    with service.sessions.begin() as db:
        # All WKS writes acquire the shared form. Originals are immutable and this
        # exclusive lock keeps purge/upload/publication synchronized with pg_dump.
        db.execute(text("SELECT pg_advisory_xact_lock(874201)"))
        with (root / "database.dump").open("wb") as f:
            subprocess.run(
                argv + ["--format=custom", "--no-owner", "--no-acl"],
                env=env,
                stdout=f,
                stderr=subprocess.PIPE,
                check=True,
            )
        manifest = {
            "schema_version": "0004",
            "database_sha256": file_digest(root / "database.dump"),
            "objects": [],
        }
        for blob in db.scalars(select(Blob)).all():
            if not service.store.exists(blob.storage_key):
                # Purged tombstone metadata is not a missing active original.
                active = db.scalar(
                    text("""SELECT EXISTS(
                  SELECT 1 FROM source_versions v JOIN sources s ON s.id=v.source_id
                  WHERE v.original_blob_id=:id AND s.state <> 'deleted'
                  UNION ALL SELECT 1 FROM assets WHERE blob_id=:id)"""),
                    {"id": blob.id},
                )
                if active:
                    raise RuntimeError("Active object missing; coordinated backup refused")
                continue
            target = objects / blob.id
            service.store.materialize(blob.storage_key, target)
            if file_digest(target) != blob.sha256:
                raise RuntimeError("Object checksum mismatch; backup refused")
            manifest["objects"].append(
                {"id": blob.id, "key": blob.storage_key, "sha256": blob.sha256, "size": blob.size}
            )
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {
        "directory": str(root),
        "objects": len(manifest["objects"]),
        "database_sha256": manifest["database_sha256"],
    }


def restore(service, source, pg_container=None):
    root = Path(source)
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        manifest["schema_version"] != "0004"
        or file_digest(root / "database.dump") != manifest["database_sha256"]
    ):
        raise RuntimeError("Invalid backup manifest or database checksum")
    for item in manifest["objects"]:
        from uuid import UUID

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
    argv, env = pg_command("pg_restore", service.settings.database_url, pg_container)
    with (root / "database.dump").open("rb") as f:
        subprocess.run(
            argv + ["--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"],
            env=env,
            stdin=f,
            stderr=subprocess.PIPE,
            check=True,
        )
    for item in manifest["objects"]:
        if not service.store.exists(item["key"]):
            service.store.put_file(item["key"], root / "objects" / item["id"])
    return {"objects_restored": len(manifest["objects"]), "schema_version": "0004"}


def reindex(service):
    with service.sessions.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(874201)"))
        result = db.execute(
            text("""UPDATE search_segments seg
          SET search_vector=setweight(to_tsvector(
              CASE WHEN seg.locale='en' THEN 'wks_english' WHEN seg.locale='pt' THEN 'wks_portuguese' ELSE 'wks_simple' END::regconfig, normalize(s.title,NFC)), 'A') ||
              to_tsvector(CASE WHEN seg.locale='en' THEN 'wks_english' WHEN seg.locale='pt' THEN 'wks_portuguese' ELSE 'wks_simple' END::regconfig, normalize(seg.text,NFC))
          FROM representations r JOIN source_versions v ON r.source_version_id=v.id JOIN sources s ON s.id=v.source_id
          WHERE seg.representation_id=r.id AND s.state='stored'""")
        )
        return {"segments_reindexed": result.rowcount, "references_preserved": True}


def garbage_collect(service):
    from uuid import UUID

    from wks.infrastructure.database import CursorSnapshot, Namespace, Upload, now

    removed = 0
    with service.sessions.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(874201)"))
        from sqlalchemy import delete

        db.execute(delete(CursorSnapshot).where(CursorSnapshot.expires_at < now()))
        expired = db.scalars(
            select(Upload)
            .where(Upload.expires_at < now(), Upload.status != "committed")
            .with_for_update()
        ).all()
        for upload in expired:
            if upload.staged_blob_id:
                blob = db.get(Blob, upload.staged_blob_id)
                if blob:
                    service.store.delete(blob.storage_key)
                    removed += 1
            upload.staged_blob_id, upload.status = None, "expired"
        known = set(db.scalars(select(Blob.storage_key)).all())
        namespaces = set(db.scalars(select(Namespace.id)).all())
        for key in set(service.store.keys()) - known:
            parts = key.split("/")
            if len(parts) != 2 or parts[0] not in namespaces:
                continue
            try:
                UUID(parts[1])
            except ValueError:
                continue
            service.store.delete(key)
            removed += 1
    return {"objects_removed": removed, "expired_sessions": len(expired)}
