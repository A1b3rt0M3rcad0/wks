from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from wks_core.domain.models import new_id


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Client(Identity, Base):
    __tablename__ = "clients"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="client")
    audience: Mapped[str] = mapped_column(String(100), default="wks")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Namespace(Identity, Base):
    __tablename__ = "namespaces"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    external_ref: Mapped[str | None] = mapped_column(String(200))
    index_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __table_args__ = (UniqueConstraint("client_id", "external_ref"),)


class Source(Identity, Base):
    __tablename__ = "sources"
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    external_uri: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="registered")
    availability: Mapped[str] = mapped_column(String(40), default="metadata_only")
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("id", "namespace_id"),)


class Collection(Identity, Base):
    __tablename__ = "collections"
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("id", "namespace_id"),)


class CollectionSource(Base):
    __tablename__ = "collection_sources"
    collection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = (
        ForeignKeyConstraint(
            ["collection_id", "namespace_id"], ["collections.id", "collections.namespace_id"]
        ),
        ForeignKeyConstraint(["source_id", "namespace_id"], ["sources.id", "sources.namespace_id"]),
    )


class Blob(Identity, Base):
    __tablename__ = "blob_objects"
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    __table_args__ = (UniqueConstraint("id", "namespace_id"),)


class SourceVersion(Identity, Base):
    __tablename__ = "source_versions"
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    namespace_id: Mapped[str] = mapped_column(String(36), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    original_blob_id: Mapped[str | None] = mapped_column(String(36))
    original_filename: Mapped[str | None] = mapped_column(String(500))
    declared_media_type: Mapped[str] = mapped_column(
        String(200), default="application/octet-stream"
    )
    media_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    checksum: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    current_representation_id: Mapped[str | None] = mapped_column(String(36))
    external_uri: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("source_id", "revision_no"),
        UniqueConstraint("id", "namespace_id"),
        ForeignKeyConstraint(["source_id", "namespace_id"], ["sources.id", "sources.namespace_id"]),
        ForeignKeyConstraint(
            ["original_blob_id", "namespace_id"], ["blob_objects.id", "blob_objects.namespace_id"]
        ),
    )


class Upload(Identity, Base):
    __tablename__ = "upload_sessions"
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"))
    expected_sha: Mapped[str] = mapped_column(String(64))
    expected_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    staged_blob_id: Mapped[str | None] = mapped_column(ForeignKey("blob_objects.id"))


class ProcessingRun(Identity, Base):
    __tablename__ = "processing_runs"
    namespace_id: Mapped[str] = mapped_column(String(36), index=True)
    source_version_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    intent_key: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSONB)
    config_digest: Mapped[str] = mapped_column(String(64))
    processor_digest: Mapped[str] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    owner: Mapped[str | None] = mapped_column(String(100))
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_representation_id: Mapped[str | None] = mapped_column(String(36))
    coverage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("source_version_id", "config_digest", "processor_digest", "intent_key"),
        ForeignKeyConstraint(
            ["source_version_id", "namespace_id"],
            ["source_versions.id", "source_versions.namespace_id"],
        ),
    )


class Stage(Identity, Base):
    __tablename__ = "processing_stages"
    run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30))
    facts: Mapped[dict] = mapped_column(JSONB, default=dict)
    duration_seconds: Mapped[float | None]


class Representation(Identity, Base):
    __tablename__ = "representations"
    namespace_id: Mapped[str] = mapped_column(String(36), index=True)
    source_version_id: Mapped[str] = mapped_column(String(36))
    processing_run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), unique=True)
    structure: Mapped[list] = mapped_column(JSONB)
    markdown: Mapped[str] = mapped_column(Text)
    coverage: Mapped[dict] = mapped_column(JSONB)
    warnings: Mapped[list] = mapped_column(JSONB)
    availability: Mapped[str] = mapped_column(String(40))
    producer: Mapped[dict] = mapped_column(JSONB)
    artifact_digest: Mapped[str] = mapped_column(String(64))
    index_generation: Mapped[int] = mapped_column(BigInteger)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("id", "namespace_id"),
        ForeignKeyConstraint(
            ["source_version_id", "namespace_id"],
            ["source_versions.id", "source_versions.namespace_id"],
        ),
    )


class Asset(Identity, Base):
    __tablename__ = "assets"
    namespace_id: Mapped[str] = mapped_column(String(36))
    representation_id: Mapped[str] = mapped_column(String(36), index=True)
    blob_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(30))
    locator: Mapped[dict] = mapped_column(JSONB)
    caption: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["representation_id", "namespace_id"],
            ["representations.id", "representations.namespace_id"],
        ),
        ForeignKeyConstraint(
            ["blob_id", "namespace_id"], ["blob_objects.id", "blob_objects.namespace_id"]
        ),
    )


class Segment(Identity, Base):
    __tablename__ = "search_segments"
    namespace_id: Mapped[str] = mapped_column(String(36), index=True)
    representation_id: Mapped[str] = mapped_column(String(36), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    block_refs: Mapped[list] = mapped_column(JSONB)
    asset_refs: Mapped[list] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    origin_kind: Mapped[str] = mapped_column(String(30))
    locale: Mapped[str] = mapped_column(String(20))
    locator: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    search_vector: Mapped[str] = mapped_column(TSVECTOR)
    __table_args__ = (
        UniqueConstraint("representation_id", "ordinal"),
        ForeignKeyConstraint(
            ["representation_id", "namespace_id"],
            ["representations.id", "representations.namespace_id"],
        ),
        Index("ix_segments_fts", "search_vector", postgresql_using="gin"),
    )


class Grant(Identity, Base):
    __tablename__ = "scope_grants"
    issuer: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    caller_binding: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    audience: Mapped[str] = mapped_column(String(100))
    source_version_ids: Mapped[list] = mapped_column(JSONB)
    allowed_actions: Mapped[list] = mapped_column(JSONB)
    purpose: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope_hash: Mapped[str] = mapped_column(String(64))


class Receipt(Base):
    __tablename__ = "idempotency_receipts"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(250), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CursorSnapshot(Base):
    __tablename__ = "cursor_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    context_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Event(Identity, Base):
    __tablename__ = "outbox_events"
    namespace_id: Mapped[str] = mapped_column(ForeignKey("namespaces.id"), index=True)
    type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Audit(Identity, Base):
    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(30))


def database(url):
    engine = create_engine(
        url, pool_pre_ping=True, connect_args={"options": "-c statement_timeout=15000"}
    )
    return engine, sessionmaker(engine, expire_on_commit=False)
