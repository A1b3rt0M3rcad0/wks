"""Operator audits original/derived bytes with paged state and honest verification level."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, select, text
from sqlalchemy.orm import Mapped, mapped_column
from wks_core.storage.database import Base, Blob, now


class BlobIntegrity(Base):
    __tablename__ = "blob_integrity"
    blob_id: Mapped[str] = mapped_column(ForeignKey("blob_objects.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(30))
    verification: Mapped[str] = mapped_column(String(30))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def audit(service, page_size=100, cursor=None, verify=True):
    size = min(max(page_size, 1), 1000)
    with service.sessions() as db:
        query = select(Blob)
        if cursor:
            query = query.where(Blob.id > cursor)
        rows = db.scalars(query.order_by(Blob.id).limit(size + 1)).all()
        values = [(row.id, row.storage_key, row.sha256, row.size) for row in rows[:size]]
    counts = {"valid": 0, "missing": 0, "corrupt": 0, "error": 0}
    for identity, key, sha, byte_size in values:
        with service.sessions() as db:
            active = db.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.original_blob_id=:id AND s.deleted_at IS NULL UNION ALL SELECT 1 FROM assets WHERE blob_id=:id UNION ALL SELECT 1 FROM export_artifacts WHERE blob_id=:id)"
                ),
                {"id": identity},
            )
        if not active:
            continue
        try:
            if not service.store.exists(key):
                status = "missing"
            elif service.store.head(key)["size"] != byte_size:
                status = "corrupt"
            elif verify and not service.store.verify_checksum(key, sha, byte_size):
                status = "corrupt"
            else:
                status = "valid"
        except Exception:
            status = "error"
        with service.sessions.begin() as db:
            record = db.get(BlobIntegrity, identity)
            if not record:
                record = BlobIntegrity(blob_id=identity)
                db.add(record)
            record.status = status
            record.verification = "sha256" if verify else "size_only"
            record.checked_at = now()
        counts[status] += 1
    return {
        "inspected": sum(counts.values()),
        "outcomes": counts,
        "verification": "sha256" if verify else "size_only",
        "next_cursor": values[-1][0] if len(rows) > size else None,
    }


def known_critical_failures(db):
    return bool(
        db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM blob_integrity b JOIN source_versions v ON v.original_blob_id=b.blob_id JOIN sources s ON s.id=v.source_id WHERE b.status IN ('missing','corrupt') AND s.deleted_at IS NULL)"
            )
        )
    )
