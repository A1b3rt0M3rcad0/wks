"""Explicit public HTTP capture with pinned DNS, TLS verification and per-hop policy."""

import http.client
import ipaddress
import socket
import ssl
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select
from wks_core.domain.models import Error
from wks_core.storage.content import detect_mime
from wks_core.storage.database import SourceVersion, now


def destination(uri):
    try:
        u = urlsplit(uri)
        if (
            u.scheme not in {"http", "https"}
            or not u.hostname
            or u.username
            or u.password
            or u.fragment
        ):
            raise ValueError()
        port = u.port or (443 if u.scheme == "https" else 80)
        if port != (443 if u.scheme == "https" else 80):
            raise ValueError()
        addresses = socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError()
        return u, ips[0], port
    except (ValueError, OSError):
        raise Error("capture.destination_blocked", "Public HTTP(S) destination required") from None


def fetch_public(uri, path, settings):
    import time

    deadline = time.monotonic() + settings.capture_timeout_seconds
    current = uri
    for hop in range(settings.capture_max_redirects + 1):
        u, ip, port = destination(current)
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise Error("capture.timeout", "Capture time limit exceeded", 504)
        if u.scheme == "https":
            conn = http.client.HTTPSConnection(
                u.hostname, port, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            conn = http.client.HTTPConnection(u.hostname, port, timeout=timeout)
        # HTTPConnection uses this function exactly once; TLS still verifies the original hostname.
        # No second DNS lookup can redirect the socket to an internal address.
        conn._create_connection = lambda address, timeout, source_address=None: (
            socket.create_connection((ip, port), timeout, source_address)
        )
        try:
            target = u.path or "/"
            if u.query:
                target += "?" + u.query
            conn.request(
                "GET",
                target,
                headers={"User-Agent": "WKS/0.1 explicit-capture", "Accept-Encoding": "identity"},
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or hop == settings.capture_max_redirects:
                    raise Error("capture.redirect_limit", "Redirect limit exceeded")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise Error("capture.remote_failure", "Public origin did not return content", 502)
            mime = (
                response.getheader("Content-Type", "application/octet-stream").split(";")[0].strip()
            )
            allowed = {
                "text/html",
                "text/plain",
                "application/pdf",
                "text/markdown",
                "application/json",
                "image/png",
                "image/jpeg",
            }
            if mime not in allowed:
                raise Error("capture.unsupported_type", "Capture content type is not enabled")
            length, size = response.getheader("Content-Length"), 0
            if length and int(length) > settings.upload_max_bytes:
                raise Error("upload.too_large", "Captured content exceeds limit", 413)
            with path.open("wb") as f:
                while chunk := response.read(65536):
                    size += len(chunk)
                    if time.monotonic() > deadline:
                        raise Error("capture.timeout", "Capture time limit exceeded", 504)
                    if size > settings.upload_max_bytes:
                        raise Error("upload.too_large", "Captured content exceeds limit", 413)
                    f.write(chunk)
            return {
                "effective_uri": current,
                "declared_mime": mime,
                "media_type": detect_mime(path, mime),
                "status": response.status,
            }
        except (OSError, http.client.HTTPException, ValueError):
            raise Error("capture.remote_failure", "Capture failed", 502, True) from None
        finally:
            conn.close()
    raise Error("capture.redirect_limit", "Redirect limit exceeded")


def capture_source(service, p, key, sid):
    if not service.settings.web_capture_enabled:
        raise Error("capture.disabled", "Explicit capture is disabled", 409)

    def op(db):
        src = service.source(db, p, sid, writing=True)
        if not src.external_uri:
            raise Error("capture.source_invalid", "Source has no bookmark URI")
        with TemporaryDirectory() as temp:
            path = Path(temp) / "capture"
            facts = fetch_public(src.external_uri, path, service.settings)
            service.quota(db, src.namespace_id, path.stat().st_size)
            blob = service.save_blob(db, src.namespace_id, path, facts["media_type"])
        versions = db.scalars(select(SourceVersion).where(SourceVersion.source_id == sid)).all()
        v = SourceVersion(
            source_id=sid,
            namespace_id=src.namespace_id,
            revision_no=max(v.revision_no for v in versions) + 1,
            original_blob_id=blob.id,
            checksum=blob.sha256,
            byte_size=blob.size,
            media_type=blob.mime,
            declared_media_type=facts["declared_mime"],
            external_uri=facts["effective_uri"],
            captured_at=now(),
            committed=True,
        )
        db.add(v)
        db.flush()
        src.current_version_id, src.state = v.id, "stored"
        run = service.enqueue(db, src, v, key)
        service.event(db, src, "source.version_committed", source_version_id=v.id)
        return {
            "source_id": sid,
            "source_version_id": v.id,
            "operation_id": run.id,
            "checksum": v.checksum,
            "captured_at": v.captured_at.isoformat(),
        }

    return service.mutate(p, key, {"op": "capture", "sid": sid}, op)
