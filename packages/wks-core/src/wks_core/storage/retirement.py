"""Durable physical deletion intents retained after canonical references are purged."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, select
from sqlalchemy.orm import Mapped, mapped_column
from wks_core.storage.database import Base, Blob, now
from wks_core.storage.journal import pinned


class BlobRetirement(Base):
    __tablename__ = "blob_retirements"
    blob_id: Mapped[str] = mapped_column(ForeignKey("blob_objects.id"), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def retire(db, source_id, blob_ids):
    for blob_id in blob_ids:
        if not db.get(BlobRetirement, blob_id):
            db.add(BlobRetirement(blob_id=blob_id, source_id=source_id))


def reconcile_source(service, source_id):
    with service.sessions() as db:
        rows = db.scalars(
            select(BlobRetirement).where(
                BlobRetirement.source_id == source_id, BlobRetirement.state == "pending"
            )
        ).all()
        pending = [
            (row.blob_id, db.get(Blob, row.blob_id).storage_key)
            for row in rows
            if not pinned(db, row.blob_id)
        ]
    for blob_id, key in pending:
        service.store.delete(key)
        with service.sessions.begin() as db:
            row = db.get(BlobRetirement, blob_id)
            if row:
                row.state = "deleted"
    with service.sessions() as db:
        return (
            db.scalar(
                select(BlobRetirement.blob_id)
                .where(BlobRetirement.source_id == source_id, BlobRetirement.state == "pending")
                .limit(1)
            )
            is None
        )
