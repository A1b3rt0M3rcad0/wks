"""Public v1 response schemas. Preserve direct entity responses from 0.3.0."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer


class Response(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def public_fields(self, serialize):
        value = serialize(self)
        return {
            key: item
            for key, item in value.items()
            if key in self.model_fields_set or key in (self.model_extra or {})
        }


class ErrorResponse(Response):
    code: str
    message: str
    request_id: str | None = None
    retryable: bool = False
    write_outcome: Literal["confirmed", "rejected", "unknown", "reconcile_required"] | None = None
    details: list[dict[str, JsonValue]] | None = None


class HealthResponse(Response):
    status: str
    version: str | None = None
    commit: str | None = None


class NamespaceResponse(Response):
    id: str
    title: str
    external_ref: str | None = None
    revision: int | None = None
    state: str | None = None


class CollectionResponse(NamespaceResponse):
    namespace_id: str | None = None


class SummaryResponse(Response):
    namespace_id: str
    title: str
    total_sources: int
    availability_counts: dict[str, int]
    pending_jobs: int
    collection_count: int


class LinkResponse(Response):
    collection_id: str
    source_id: str
    linked: bool


class VersionResponse(Response):
    id: str
    revision_number: int
    committed: bool
    checksum: str | None = None
    byte_size: int | None = None
    media_type: str | None = None
    representation_id: str | None = None


class ProcessingSummary(Response):
    operation_id: str
    state: str
    error_code: str | None = None


class SourceResponse(Response):
    id: str
    source_id: str
    namespace_id: str
    title: str
    description: str
    kind: str
    tags: list[str]
    metadata: dict[str, JsonValue]
    state: str
    processing: ProcessingSummary | None = None
    availability: str
    current_version_id: str | None = None
    source_ref: str | None = None
    versions: list[VersionResponse]
    revision: int | None = None


class ReceiptResponse(Response):
    source_id: str | None = None
    source_version_id: str | None = None
    upload_id: str | None = None
    operation_id: str | None = None
    status: str | None = None
    next_action: str | None = None
    write_outcome: str | None = None


class UploadResponse(Response):
    upload_id: str
    status: str
    sha256: str | None = None
    offset: int | None = None
    expected_size: int | None = None
    expires_at: str | None = None


class BlockResponse(Response):
    id: str
    type: str
    text: str = ""
    locator: dict[str, JsonValue] = Field(default_factory=dict)
    origin_kind: str | None = None
    parent_id: str | None = None
    asset_refs: list[str] = Field(default_factory=list)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    ordinal: int | None = None
    confidence: float | None = None


class ReadResponse(Response):
    representation_id: str
    source_version_id: str
    index_generation: int
    blocks: list[BlockResponse]
    markdown: str
    coverage: dict[str, JsonValue]
    warnings: list[str]
    next_cursor: str | None = None
    complete: bool | None = None
    content_trust: str
    representation_schema_version: str | None = None


class SearchHit(Response):
    source_id: str
    source_version_id: str
    representation_id: str
    segment_id: str
    segment_ref: str
    title: str
    snippet: str
    rank: float
    locator: dict[str, JsonValue]
    origin_kind: str
    asset_refs: list[str]
    match_positions: list[list[int]] | None = None
    match_origin: str | None = None
    source_version_ref: str | None = None
    score: float | None = None


class SearchResponse(Response):
    query: str
    items: list[SearchHit]
    next_cursor: str | None = None
    index_kind: str
    scope: str | None = None
    index_generations: dict[str, int] | None = None
    content_trust: str | None = None
    warning: str | None = None
    complete: bool | None = None
    facets: dict[str, dict[str, int]] | None = None


class AssetResponse(Response):
    asset_id: str | None = None
    asset_ref: str
    media_type: str
    byte_size: int
    checksum_sha256: str | None = None
    content_path: str
    locator: dict[str, JsonValue]


class OperationResponse(Response):
    operation_id: str
    source_id: str
    source_version_id: str
    processing_state: str
    current_stage: str
    availability: str
    coverage: dict[str, JsonValue]
    published_representation_id: str | None = None
    attempt: int
    next_retry_at: str | None = None
    updated_at: str
    error_code: str | None = None


class GrantResponse(Response):
    id: str
    expires_at: str | None = None
    scope_hash: str | None = None
    revoked: bool | None = None


class LifecycleResponse(Response):
    source_id: str
    state: str
    cleanup_state: str | None = None
    write_outcome: str | None = None


class ReferenceResponse(Response):
    ref: str | None = None
    verified: bool | None = None
    source_version_id: str | None = None
    checksum: str | None = None
    asset_ref: str | None = None
    representation_id: str | None = None
    segment_id: str | None = None
    block_id: str | None = None


T = TypeVar("T")


class Page(Response, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    complete: bool | None = None


class ExportResponse(Response):
    id: str
    representation_id: str
    format: Literal[
        "markdown-single", "markdown-bundle", "structured-json", "plain-text", "source-original"
    ]
    status: str
    sha256: str | None = None
    byte_size: int | None = None
    content_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class CapabilityResponse(Response):
    version: str
    representation_schema_version: str
    formats: list[dict[str, JsonValue]]
    limits: dict[str, JsonValue]
    search_modes: list[str]
    worker_queues: list[str]
    actions: list[str]


class MetadataResponse(Response):
    id: str
    revision: int
    reindex_required: bool


class BlockPageResponse(Response):
    representation_id: str
    block_id: str
    encoding: str
    content: str
    offset: int
    total_chars: int
    next_offset: int | None
    complete: bool
    checksum: str
    content_trust: str


class PreflightResponse(Response):
    source_id: str
    revision: int
    versions: int
    action: str
    confirmation_token: str
    expires_at: str
    effect: str


class ClientResponse(Response):
    id: str
    name: str
    role: str
    audience: str


class CredentialResponse(Response):
    id: str
    client_id: str | None = None
    token: str | None = Field(default=None, repr=False)
    expires_at: str | None = None
    secret_available: bool | None = None
    revoked: bool | None = None


class EventResponse(Response):
    id: str
    type: str
    payload: dict[str, JsonValue]
    occurred_at: str


class HistoryResponse(Response):
    source_id: str
    representations: list[dict[str, JsonValue]]
    operations: list[dict[str, JsonValue]]
    bounded: bool
    complete: bool
    next_cursor: str | None


class SetupResponse(Response):
    setup_required: bool


class SessionResponse(Response):
    authenticated: bool
    csrf_token: str | None = Field(default=None, repr=False)
    recovery_token: str | None = Field(default=None, repr=False)
    username: str | None = None
    name: str | None = None
    upload_max_bytes: int | None = None


class RecoveryTokenResponse(Response):
    username: str
    recovery_token: str = Field(repr=False)


class SuggestionResponse(Response):
    text: str
    score: float
    origin: str


class SuggestionsResponse(Response):
    query: str
    suggestions: list[SuggestionResponse]
    scope: str
    complete: bool


class RepresentationResponse(Response):
    id: str
    source_version_id: str
    schema_version: str
    producer: dict[str, JsonValue]
    artifact_digest: str
    index_generation: int
    availability: str
    coverage: dict[str, JsonValue]
    warnings: list[str]
    block_count: int
    published_at: str
    ref: str


class ContainerPreflightResponse(Response):
    id: str
    kind: str
    revision: int
    source_count: int
    confirmation_token: str
    expires_at: str
    effect: str


class ContainerLifecycleResponse(Response):
    id: str
    revision: int
    state: str
    source_bytes_deleted: bool
