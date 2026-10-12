from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NamespaceBody(Model):
    title: str = Field(min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)


class CollectionBody(NamespaceBody):
    namespace_id: UUID


class UploadBody(Model):
    filename: str = Field(min_length=1, max_length=500)
    declared_media_type: str = Field(default="application/octet-stream", max_length=200)
    byte_size: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value):
        if "/" in value or "\\" in value or "\0" in value or value in {".", ".."}:
            raise ValueError("A filename must not contain a path")
        return value


class VersionBody(Model):
    text: str | None = None
    upload: UploadBody | None = None

    @model_validator(mode="after")
    def content(self):
        if self.text is not None and self.upload is not None:
            raise ValueError("Choose text or upload")
        return self


class SourceBody(VersionBody):
    namespace_id: UUID
    kind: Literal["upload", "text", "bookmark"]
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)
    external_ref: str | None = Field(default=None, max_length=250)

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value):
        from wks_core.domain.canonical import validate_metadata

        return validate_metadata(value)

    external_uri: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def input_kind(self):
        if self.kind == "upload" and self.upload is None:
            raise ValueError("upload is required")
        if self.kind == "text" and self.text is None:
            raise ValueError("text is required")
        if self.kind == "bookmark":
            from urllib.parse import urlsplit

            u = urlsplit(self.external_uri or "")
            if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
                raise ValueError("A public HTTP(S) bookmark URI is required")
            if self.upload is not None or self.text is not None:
                raise ValueError("Bookmarks contain no captured content")
        return self


class ProcessBody(Model):
    source_version_id: UUID | None = None


class Filters(Model):
    tags: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=3)
    namespace_id: UUID | None = None
    collection_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_version_ids: list[UUID] = Field(default_factory=list, max_length=100)
    media_types: list[str] = Field(default_factory=list, max_length=20)
    representation: Literal["current_published", "historical"] = "current_published"
    created_after: datetime | None = None
    created_before: datetime | None = None
    processor: str | None = Field(default=None, max_length=100)
    availability: str | None = Field(default=None, max_length=40)
    language: Literal["pt", "en", "simple"] | None = None

    @field_validator("metadata")
    @classmethod
    def indexed_fields(cls, value):
        if set(value) - {"category", "document_type", "declared_language"}:
            raise ValueError("Only indexed metadata filter fields are supported")
        return value


class SearchBody(Model):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["lexical", "phrase", "exact", "fuzzy", "proximity"] = "lexical"
    distance: int = Field(default=5, ge=1, le=20)
    include_facets: bool = False
    language: Literal["pt", "en", "simple", "auto"] = "pt"
    filters: Filters = Field(default_factory=Filters)
    page_size: int = Field(default=10, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)


class GrantBody(Model):
    caller_binding: UUID
    audience: str = Field(min_length=1, max_length=100)
    source_version_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    namespace_id: UUID | None = None
    collection_id: UUID | None = None
    allowed_actions: list[str] = Field(min_length=1, max_length=30)
    purpose: str = Field(min_length=1, max_length=200)
    ttl_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def scoped(self):
        from wks_core.domain.policies import validate_delegation

        validate_delegation(self.model_dump(mode="json"))
        return self


class RefBody(Model):
    ref: str = Field(min_length=1, max_length=300)


class MetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata: dict[str, JsonValue] | None = None


class ContainerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    state: Literal["active", "archived"] | None = None
    policy: dict[str, JsonValue] | None = None


class ClientBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    role: Literal["client", "executor"] = "client"
    audience: str = Field(default="wks", min_length=1, max_length=100)


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(default="integration credential", min_length=1, max_length=200)
    ttl_seconds: int = Field(default=86400, ge=1, le=31536000)


class ExportBody(Model):
    format: Literal[
        "markdown-single", "markdown-bundle", "structured-json", "plain-text", "source-original"
    ] = "markdown-single"


class BulkProcessBody(Model):
    dry_run: bool = True
    processor_version: str | None = Field(default=None, max_length=200)
    incomplete_only: bool = False
    missing_index_only: bool = False
    page_size: int = Field(default=25, ge=1, le=25)
    cursor: str | None = Field(default=None, max_length=4096)


class S3ImportBody(Model):
    namespace_id: UUID
    bucket: str = Field(min_length=3, max_length=63)
    object_key: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=500)
    external_ref: str | None = Field(default=None, max_length=250)
