"""Portable domain types. No transport, ORM or converter types belong here."""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Principal:
    client_id: str
    role: str
    audience: str
    grant_id: str | None = None


@dataclass
class Block:
    type: str
    text: str = ""
    locator: dict[str, Any] = field(default_factory=dict)
    origin_kind: str = "native_text"
    id: str = field(default_factory=new_id)
    parent_id: str | None = None
    asset_refs: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedAsset:
    content: bytes
    media_type: str
    kind: str
    locator: dict[str, Any]
    id: str = field(default_factory=new_id)
    caption: str | None = None


@dataclass
class ExtractedRepresentation:
    blocks: list[Block] = field(default_factory=list)
    assets: list[ExtractedAsset] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    availability: str = "text_ready"
    processor_name: str = "text"
    processor_version: str = "1"


class Error(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        super().__init__(message)


def not_found() -> Error:
    return Error("source.not_found", "Resource unavailable", 404)
