"""Durable blob intents: network/filesystem IO precedes short metadata attachment."""

from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, text
from wks_core.application.utilities import file_digest
from wks_core.domain.models import Error, new_id
from wks_core.storage.database import Blob, now
from wks_core.storage.stabilization import BackupPin, BackupSnapshot, BlobOperation


def stage(service, namespace_id, path, mime, kind="put", resource_id=None):
    path = Path(path)
    sha, size = file_digest(path), path.stat().st_size
    resource_id = resource_id or new_id()
    service.checkpoint("blob.before_intent")
    with service.sessions.begin() as db:
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 41))"),
            {"key": namespace_id + ":" + kind + ":" + resource_id},
        )
        op = db.scalar(
            select(BlobOperation).where(
                BlobOperation.namespace_id == namespace_id,
                BlobOperation.kind == kind,
                BlobOperation.resource_id == resource_id,
            )
        )
        if op and (op.sha256 != sha or op.size != size):
            raise Error(
                "operation.idempotency_conflict", "Blob intent already has another digest", 409
            )
        if op and op.state == "deleting":
            raise Error("operation.reconcile_required", "Intent cleanup is in progress", 503, True)
        if op and op.state in {"cleaned", "failed"}:
            service.quota(db, namespace_id, size if kind not in {"part", "upload"} else 0)
            op.state, op.updated_at = "proposed", now()
        if not op:
            if kind in {"part", "upload"}:
                from wks_core.storage.database import SourceVersion, Upload

                upload = db.get(Upload, resource_id.split(":")[0])
                version = db.get(SourceVersion, upload.source_version_id) if upload else None
                if (
                    not upload
                    or not version
                    or version.namespace_id != namespace_id
                    or upload.status not in {"pending", "received"}
                    or upload.expires_at <= now()
                ):
                    raise Error("upload.expired", "Upload reservation unavailable", 410)
                service.quota(db, namespace_id, 0)
            else:
                service.quota(db, namespace_id, size)
            op = BlobOperation(
                namespace_id=namespace_id,
                kind=kind,
                resource_id=resource_id,
                storage_key=f"{namespace_id}/{new_id()}",
                sha256=sha,
                size=size,
                mime=mime,
                provider=service.settings.storage_provider,
            )
            db.add(op)
            db.flush()
        identity, key, state = op.id, op.storage_key, op.state
    service.checkpoint("blob.after_intent")
    if state != "attached":
        try:
            if not service.store.exists(key):
                try:
                    service.checkpoint("blob.before_put")
                    service.store.put_file(key, path)
                    service.checkpoint("blob.after_put")
                except FileExistsError:
                    pass
                except Exception as exc:
                    # A conditional S3 PUT lost a race only if the immutable object now exists.
                    if not service.store.exists(key):
                        raise exc
            service.checkpoint("blob.before_verify")
            if not service.store.verify_checksum(key, sha, size):
                raise Error("upload.checksum_mismatch", "Stored object integrity failed")
        except Exception:
            with service.sessions.begin() as db:
                op = db.get(BlobOperation, identity)
                op.state, op.error_code, op.updated_at = (
                    "reconcile_required",
                    "storage.put_uncertain",
                    now(),
                )
            raise Error(
                "operation.reconcile_required", "Blob write needs reconciliation", 503, True
            ) from None
        service.checkpoint("blob.after_verify")
        with service.sessions.begin() as db:
            op = db.get(BlobOperation, identity)
            if op.state != "attached":
                op.state, op.error_code, op.updated_at = "verified", None, now()
    service.checkpoint("blob.after_verified_commit")
    return identity


def attach(service, db, identity, namespace_id):
    service.checkpoint("blob.before_attach")
    op = db.scalar(select(BlobOperation).where(BlobOperation.id == identity).with_for_update())
    if not op or op.namespace_id != namespace_id or op.state not in {"verified", "attached"}:
        raise Error("operation.reconcile_required", "Blob is not verified", 503, True)
    if op.blob_id:
        return db.get(Blob, op.blob_id)
    blob = Blob(
        namespace_id=namespace_id,
        storage_key=op.storage_key,
        sha256=op.sha256,
        size=op.size,
        mime=op.mime,
        provider=op.provider,
        location=service.store.location(),
    )
    service.checkpoint("blob.before_metadata_insert")
    db.add(blob)
    db.flush()
    op.blob_id, op.state, op.updated_at = blob.id, "attached", now()
    service.checkpoint("blob.after_attach")
    return blob


def reconcile(service, page_size=100, cursor=None, grace_seconds=3600):
    cutoff = now() - timedelta(seconds=grace_seconds)
    with service.sessions() as db:
        stmt = select(BlobOperation).where(
            BlobOperation.state.in_(["proposed", "reconcile_required"])
        )
        if cursor:
            stmt = stmt.where(BlobOperation.id > cursor)
        rows = db.scalars(stmt.order_by(BlobOperation.id).limit(page_size + 1)).all()
        values = [
            (op.id, op.storage_key, op.sha256, op.size, op.updated_at) for op in rows[:page_size]
        ]
    verified, failed = 0, 0
    for identity, key, sha, size, updated in values:
        exists = service.store.exists(key)
        valid = exists and service.store.verify_checksum(key, sha, size)
        with service.sessions.begin() as db:
            op = db.get(BlobOperation, identity)
            if op.state not in {"proposed", "reconcile_required", "failed"}:
                continue
            if valid:
                op.state, op.error_code = "verified", None
                verified += 1
            elif updated < cutoff and op.state != "failed":
                op.state, op.error_code = "failed", "storage.original_unavailable"
                failed += 1
            if valid or (updated < cutoff and op.state != "failed"):
                op.updated_at = now()
    return {
        "verified": verified,
        "failed": failed,
        "next_cursor": values[-1][0] if len(rows) > page_size else None,
    }


def pinned(db, blob_id):
    return (
        db.scalar(
            select(BackupPin.blob_id)
            .join(BackupSnapshot)
            .where(
                BackupPin.blob_id == blob_id,
                BackupSnapshot.expires_at > now(),
                BackupSnapshot.state == "copying",
            )
        )
        is not None
    )
