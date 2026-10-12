"""Incremental retention and object GC with persisted cursor and orphan quarantine."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, delete, exists, or_, select, text
from wks_core.storage.database import Blob, CursorSnapshot, Receipt, Upload, now
from wks_core.storage.journal import pinned
from wks_core.storage.stabilization import (
    BlobOperation,
    OrphanCandidate,
    RetentionSweep,
    UploadPart,
)


def garbage_collect(service, page_size=100, cursor=None, grace_seconds=None):
    size = min(max(page_size, 1), 1000)
    grace = service.settings.gc_grace_seconds if grace_seconds is None else max(0, grace_seconds)
    cutoff = now() - timedelta(seconds=grace)
    removed, expired = 0, 0
    with service.sessions.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
        checkpoint = db.get(RetentionSweep, "objects")
        start = cursor if cursor is not None else checkpoint.cursor if checkpoint else None
        db.execute(delete(CursorSnapshot).where(CursorSnapshot.expires_at < now()))
        records = db.scalars(
            select(Upload)
            .where(
                or_(Upload.expires_at < now(), Upload.status.in_(["committed", "aborted"])),
                or_(
                    Upload.status.not_in(["committed", "expired", "aborted"]),
                    and_(Upload.status != "committed", Upload.staged_blob_id.is_not(None)),
                    exists(
                        select(UploadPart.upload_id)
                        .join(BlobOperation, BlobOperation.id == UploadPart.journal_id)
                        .where(UploadPart.upload_id == Upload.id, BlobOperation.state != "cleaned")
                    ),
                ),
            )
            .order_by(Upload.id)
            .limit(size)
            .with_for_update(skip_locked=True)
        ).all()
        upload_ids = []
        staged = []
        for record in records:
            upload_ids.append(record.id)
            if record.status == "committed":
                continue
            if record.status != "expired":
                record.status = "expired"
                expired += 1
            if record.staged_blob_id and not pinned(db, record.staged_blob_id):
                staged.append((record.id, db.get(Blob, record.staged_blob_id).storage_key))
        if not checkpoint:
            checkpoint = RetentionSweep(id="objects")
            db.add(checkpoint)
    for uid, key in staged:
        service.store.delete(key)
        removed += 1
        with service.sessions.begin() as db:
            record = db.get(Upload, uid)
            if record and record.status == "expired":
                record.staged_blob_id = None
    for uid in upload_ids:
        service.cleanup_upload_parts(uid)
    page = service.store.list_prefix_page(cursor=start, page_size=size)
    for key in page["keys"]:
        parts = key.split("/")
        if len(parts) != 2:
            continue
        try:
            UUID(parts[0])
            UUID(parts[1])
        except ValueError:
            continue
        delete_object = False
        with service.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            blob = db.scalar(select(Blob).where(Blob.storage_key == key))
            intent = db.scalar(select(BlobOperation).where(BlobOperation.storage_key == key))
            from wks_core.storage.retirement import BlobRetirement

            retired = db.get(BlobRetirement, blob.id) if blob else None
            known = (blob and not (retired and retired.state == "deleted")) or (
                intent
                and intent.state in {"proposed", "verified", "attached", "reconcile_required"}
                and intent.updated_at > cutoff
            )
            if known or (blob and pinned(db, blob.id)):
                db.execute(delete(OrphanCandidate).where(OrphanCandidate.storage_key == key))
                continue
            candidate = db.get(OrphanCandidate, key)
            if not candidate:
                db.add(OrphanCandidate(storage_key=key))
                continue
            if candidate.observed_at > cutoff:
                continue
            # Recheck references after the quarantine interval before crossing the IO boundary.
            if intent:
                current = db.get(BlobOperation, intent.id, with_for_update=True)
                if current.updated_at > cutoff or current.state == "attached":
                    continue
                current.state = "deleting"
                current.updated_at = now()
            delete_object = True
        if delete_object:
            service.store.delete(key)
            removed += 1
            with service.sessions.begin() as db:
                db.execute(delete(OrphanCandidate).where(OrphanCandidate.storage_key == key))
                if intent and intent.state != "attached":
                    current = db.get(BlobOperation, intent.id)
                    if current:
                        current.state = "cleaned"
                        current.updated_at = now()
    with service.sessions.begin() as db:
        checkpoint = db.get(RetentionSweep, "objects", with_for_update=True)
        checkpoint.cursor = page["next_cursor"]
        checkpoint.updated_at = now()
        # Receipts remain while an active source is referenced; arbitrary time pruning is opt-in.
        db.execute(
            delete(Receipt).where(
                Receipt.created_at
                < now() - timedelta(days=service.settings.receipt_retention_days),
                ~exists(
                    select(Upload.id).where(
                        Upload.id == Receipt.result["upload_id"].astext,
                        Upload.status.in_(["pending", "received"]),
                    )
                ),
            )
        )
    return {
        "objects_removed": removed,
        "expired_sessions": expired,
        "objects_inspected": len(page["keys"]),
        "next_cursor": page["next_cursor"],
        "quarantine_seconds": grace,
    }
