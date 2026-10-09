from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    metadata: dict[str, str | int | bool] = Field(default_factory=dict, max_length=30)
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


class SearchBody(Model):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["lexical", "phrase"] = "lexical"
    language: Literal["pt", "en", "simple"] = "pt"
    filters: Filters = Field(default_factory=Filters)
    page_size: int = Field(default=10, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)


class GrantBody(Model):
    caller_binding: UUID
    audience: str = Field(min_length=1, max_length=100)
    source_version_ids: list[UUID] = Field(min_length=1, max_length=1000)
    allowed_actions: list[Literal["read", "search", "asset_read"]] = Field(
        min_length=1, max_length=3
    )
    purpose: str = Field(min_length=1, max_length=200)
    ttl_seconds: int = Field(default=300, ge=1, le=3600)


class RefBody(Model):
    ref: str = Field(min_length=1, max_length=300)
