"""Standalone typed HTTP SDK; retries never repeat unconfirmed mutations."""

from __future__ import annotations

import hashlib
import http.client
import json
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass
class WksError(Exception):
    code: str
    message: str
    status: int = 0
    retryable: bool = False
    write_outcome: str = "rejected"
    operation_key: str | None = None
    request_id: str | None = None

    def __str__(self):
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class Receipt:
    source_id: str | None = None
    source_version_id: str | None = None
    upload_id: str | None = None
    operation_id: str | None = None
    write_outcome: str | None = None
    status: str | None = None

    @classmethod
    def from_json(cls, value):
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})


@dataclass(frozen=True)
class Operation:
    operation_id: str
    processing_state: str
    published_representation_id: str | None = None
    error_code: str | None = None
    coverage: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value):
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def operation_key():
    return "wks-sdk-" + secrets.token_hex(16)


@dataclass
class Client:
    endpoint: str
    token: str = field(repr=False)
    grant: str | None = None
    timeout: float = 30
    max_response_bytes: int = 8 * 1024 * 1024
    _opener: Any = field(init=False, repr=False)

    def __post_init__(self):
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid API endpoint")
        self.endpoint = self.endpoint.rstrip("/")
        self._opener = build_opener(_NoRedirect())

    def request(
        self,
        method: str,
        path: str,
        body: bytes | BinaryIO | None = None,
        *,
        key: str | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Invalid API path")
        private = {"Authorization": "Bearer " + self.token, "Content-Type": content_type}
        if self.grant:
            private["X-WKS-Grant"] = self.grant
        if key:
            private["Idempotency-Key"] = key
        private.update(headers or {})
        request = Request(self.endpoint + path, data=body, headers=private, method=method)
        try:
            return self._opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            try:
                failure = json.loads(exc.read(65536))
            except (ValueError, OSError):
                failure = {}
            finally:
                exc.close()
            raise WksError(
                code=failure.get("code", "http.error"),
                message=failure.get("message", "HTTP operation failed"),
                status=exc.code,
                retryable=bool(failure.get("retryable", False)),
                write_outcome=failure.get(
                    "write_outcome",
                    "unknown" if exc.code >= 500 and method not in {"GET", "HEAD"} else "rejected",
                ),
                operation_key=key,
                request_id=failure.get("request_id"),
            ) from None
        except (OSError, URLError, TimeoutError):
            raise WksError(
                "transport.unavailable",
                "Request outcome could not be confirmed",
                retryable=True,
                write_outcome="unknown" if method not in {"GET", "HEAD"} else "rejected",
                operation_key=key,
            ) from None

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raw = (
            json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            if body is not None
            else None
        )
        try:
            with self.request(method, path, raw, key=key, headers=headers) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise ValueError()
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except (OSError, ValueError, http.client.HTTPException):
            raise WksError(
                "transport.response_invalid",
                "Response could not be confirmed",
                retryable=True,
                write_outcome="unknown" if method not in {"GET", "HEAD"} else "rejected",
                operation_key=key,
            ) from None

    def create_text(self, namespace_id: str, title: str, text: str, *, key: str) -> Receipt:
        return Receipt.from_json(
            self.call(
                "POST",
                "/v1/sources",
                {"namespace_id": namespace_id, "kind": "text", "title": title, "text": text},
                key=key,
            )
        )

    def source(self, identity: str) -> dict[str, Any]:
        return self.call("GET", "/v1/sources/" + quote(identity, safe=""))

    def receipt(self, key: str) -> Receipt:
        return Receipt.from_json(self.call("GET", "/v1/receipts/" + quote(key, safe="")))

    def operation(self, identity: str) -> Operation:
        return Operation.from_json(self.call("GET", "/v1/operations/" + quote(identity, safe="")))

    def wait_operation(self, identity: str, *, deadline_seconds: float = 300) -> Operation:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            result = self.operation(identity)
            if result.processing_state in {"succeeded", "partial"}:
                return result
            if result.processing_state in {"failed", "cancelled"}:
                raise WksError(
                    result.error_code or "processing.failed", "Processing did not complete"
                )
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise WksError(
            "operation.timeout", "Polling timed out", retryable=True, write_outcome="unknown"
        )

    def pages(self, path: str, *, body: dict | None = None) -> Iterator[dict]:
        cursor, seen = None, set()
        while True:
            if body is None:
                separator = "&" if "?" in path else "?"
                page = self.call(
                    "GET", path + (separator + urlencode({"cursor": cursor}) if cursor else "")
                )
            else:
                page = self.call("POST", path, {**body, **({"cursor": cursor} if cursor else {})})
            yield page
            cursor = page.get("next_cursor")
            if not cursor:
                return
            if cursor in seen:
                raise WksError("contract.cursor_cycle", "Server repeated a pagination cursor")
            seen.add(cursor)

    def read(self, identity: str) -> Iterator[dict]:
        return self.pages("/v1/representations/" + quote(identity, safe="") + "/read")

    def search(
        self, query: str, *, mode: str = "lexical", filters: dict | None = None
    ) -> Iterator[dict]:
        return self.pages(
            "/v1/search", body={"query": query, "mode": mode, "filters": filters or {}}
        )

    def download(self, path: str, destination: BinaryIO, *, sha256: str | None = None) -> int:
        digest, size = hashlib.sha256(), 0
        with self.request("GET", path, content_type="") as response:
            while chunk := response.read(1024 * 1024):
                destination.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if sha256 and digest.hexdigest() != sha256:
            raise WksError(
                "asset.checksum_mismatch", "Downloaded content failed integrity validation"
            )
        return size

    def resume_upload(
        self, identity: str, source: BinaryIO, *, chunk_bytes: int = 4 * 1024 * 1024, progress=None
    ) -> dict:
        path = "/v1/uploads/" + quote(identity, safe="")
        status = self.call("GET", path)
        if not 0 <= status["offset"] <= status["expected_size"]:
            raise WksError("contract.invalid_offset", "Invalid server upload offset")
        source.seek(status["offset"])
        while status["offset"] < status["expected_size"]:
            chunk = source.read(min(chunk_bytes, status["expected_size"] - status["offset"]))
            if not chunk:
                raise WksError("upload.size_mismatch", "Source ended before its declared size")
            part = (
                path
                + "/parts?"
                + urlencode(
                    {
                        "offset": status["offset"],
                        "checksum_sha256": hashlib.sha256(chunk).hexdigest(),
                    }
                )
            )
            with self.request(
                "PUT", part, chunk, content_type="application/octet-stream"
            ) as response:
                status = json.loads(response.read(65536))
            if progress:
                progress(status["offset"], status["expected_size"])
        return status
