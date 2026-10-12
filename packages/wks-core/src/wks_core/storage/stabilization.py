"""Storage records for recoverable writes and bounded canonical projections."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from wks_core.storage.database import Base, Identity, now


class RepresentationBlock(Base):
    __tablename__ = "representation_blocks"
    representation_id: Mapped[str] = mapped_column(
        ForeignKey("representations.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(40))
    parent_id: Mapped[str | None] = mapped_column(String(36))
    value: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("representation_id", "block_id"),)


class BlobOperation(Identity, Base):
    __tablename__ = "blob_operations"
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(250))
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    blob_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error_code: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("namespace_id", "kind", "resource_id"),)


class BackupSnapshot(Identity, Base):
    __tablename__ = "backup_snapshots"
    state: Mapped[str] = mapped_column(String(30), default="copying")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    watermark: Mapped[str | None] = mapped_column(String(36))


class BackupPin(Base):
    __tablename__ = "backup_pins"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("backup_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    blob_id: Mapped[str] = mapped_column(ForeignKey("blob_objects.id"), primary_key=True)


class ExportArtifact(Identity, Base):
    __tablename__ = "export_artifacts"
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    representation_id: Mapped[str] = mapped_column(
        ForeignKey("representations.id", ondelete="CASCADE")
    )
    format: Mapped[str] = mapped_column(String(40))
    options_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    blob_id: Mapped[str | None] = mapped_column(String(36))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint("representation_id", "format", "options_digest"),)


class UploadPart(Base):
    __tablename__ = "upload_parts"
    upload_id: Mapped[str] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    offset: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    journal_id: Mapped[str] = mapped_column(ForeignKey("blob_operations.id"))


class ClientCredential(Identity, Base):
    __tablename__ = "client_credentials"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="integration credential")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    owner: Mapped[str] = mapped_column(String(100), primary_key=True)
    queues: Mapped[list] = mapped_column(JSONB)
    capabilities: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class RetentionSweep(Base):
    __tablename__ = "retention_sweeps"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrphanCandidate(Base):
    __tablename__ = "orphan_candidates"
    storage_key: Mapped[str] = mapped_column(Text, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DestructivePreflight(Identity, Base):
    __tablename__ = "destructive_preflights"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    action: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class SourceMetadataRevision(Base):
    __tablename__ = "source_metadata_revisions"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    value: Mapped[dict] = mapped_column(JSONB)
    generation: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionWindow(Base):
    __tablename__ = "action_windows"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer)
