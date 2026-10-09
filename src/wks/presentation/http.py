import hashlib
import json
import time
from contextlib import asynccontextmanager
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from anyio import to_thread
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from wks.bootstrap import build
from wks.domain.models import Error
from wks.presentation.mcp import build_mcp
from wks.presentation.schemas import (
    CollectionBody,
    GrantBody,
    NamespaceBody,
    ProcessBody,
    RefBody,
    SearchBody,
    SourceBody,
    VersionBody,
)

REQUESTS = Counter("wks_http_requests_total", "HTTP outcomes", ["method", "status"])
LATENCY = Histogram("wks_http_seconds", "HTTP request time", ["method"])


def create_app(service=None, engine=None):
    if service is None:
        service, engine = build()
    mcp = build_mcp(service)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Woobe Knowledge Service", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def boundary(request, call_next):
        request_id, started = str(uuid4()), time.monotonic()
        request.state.request_id = request_id
        try:
            if request.url.path.startswith("/mcp"):
                await to_thread.run_sync(
                    lambda: service.authenticate(
                        request.headers.get("authorization", "")[7:]
                        if request.headers.get("authorization", "").startswith("Bearer ")
                        else None,
                        request.headers.get("x-wks-grant"),
                    )
                )
            if request.url.path.startswith("/v1") and "/uploads/" not in request.url.path:
                # Bound JSON bodies before Pydantic parsing; uploads have separate streaming bounds.
                limit = min(service.settings.upload_max_bytes, 2 * 1024 * 1024)
                if int(request.headers.get("content-length", 0)) > limit:
                    raise Error("upload.too_large", "Request exceeds limit", 413)
                buffered = bytearray()
                async for chunk in request.stream():
                    buffered.extend(chunk)
                    if len(buffered) > limit:
                        raise Error(
                            "upload.too_large", "JSON exceeds limit; use streaming upload", 413
                        )
                request._body = bytes(buffered)
            response = await call_next(request)
        except Error as exc:
            response = JSONResponse(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "request_id": request_id,
                },
                status_code=exc.status,
            )
        except Exception:
            response = JSONResponse(
                {
                    "code": "operation.infrastructure_unavailable",
                    "message": "Service temporarily unavailable",
                    "retryable": True,
                    "request_id": request_id,
                },
                status_code=503,
            )
        response.headers.update(
            {
                "X-Request-ID": request_id,
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            }
        )
        REQUESTS.labels(request.method, response.status_code).inc()
        LATENCY.labels(request.method).observe(time.monotonic() - started)
        return response

    @app.exception_handler(Error)
    async def error_handler(request, exc):
        return JSONResponse(
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
                "retryable": exc.retryable,
            },
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Input values can contain credentials/content: do not echo them in errors.
        return JSONResponse(
            {
                "code": "contract.invalid",
                "message": "Invalid request",
                "request_id": request.state.request_id,
                "retryable": False,
                "details": [{"loc": e["loc"], "type": e["type"]} for e in exc.errors()],
            },
            status_code=422,
        )

    def auth(
        authorization: str | None = Header(default=None),
        x_wks_grant: str | None = Header(default=None),
    ):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        return service.authenticate(token, x_wks_grant)

    def key(idempotency_key: str = Header(default="")):
        return idempotency_key

    def dump(body):
        return body.model_dump(mode="json", exclude_none=True)

    @app.get("/health/live", tags=["operations"])
    def live():
        return {"status": "live"}

    @app.get("/health/ready", tags=["operations"])
    def ready():
        try:
            with service.sessions() as db:
                migration = db.scalar(text("SELECT version_num FROM alembic_version"))
                db.execute(text("SELECT to_tsvector('wks_portuguese', 'saúde')"))
            if migration != "0004" or not service.store.ready():
                raise RuntimeError()
            return {
                "status": "ready",
                "migration": migration,
                "index_kind": "lexical_postgresql_fts",
            }
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/metrics", tags=["operations"])
    def metrics(p=Depends(auth)):
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/namespaces")
    def namespace_create(body: NamespaceBody, p=Depends(auth), k=Depends(key)):
        return service.namespace_create(p, k, dump(body))

    @app.get("/v1/namespaces/{id}")
    def namespace_get(id: UUID, p=Depends(auth)):
        return service.namespace_get(p, str(id))

    @app.post("/v1/collections")
    def collection_create(body: CollectionBody, p=Depends(auth), k=Depends(key)):
        return service.collection_create(p, k, dump(body))

    @app.put("/v1/collections/{cid}/sources/{sid}")
    def collection_link(cid: UUID, sid: UUID, p=Depends(auth), k=Depends(key)):
        return service.collection_link(p, k, str(cid), str(sid))

    @app.delete("/v1/collections/{cid}/sources/{sid}")
    def collection_unlink(cid: UUID, sid: UUID, p=Depends(auth), k=Depends(key)):
        return service.collection_link(p, k, str(cid), str(sid), unlink=True)

    @app.post("/v1/sources", status_code=201)
    def register(body: SourceBody, p=Depends(auth), k=Depends(key)):
        return service.register(p, k, dump(body))

    @app.post("/v1/sources/{sid}/versions", status_code=201)
    def version_create(sid: UUID, body: VersionBody, p=Depends(auth), k=Depends(key)):
        return service.new_version(p, k, str(sid), dump(body))

    @app.put("/v1/uploads/{uid}/content")
    async def upload(uid: UUID, request: Request, p=Depends(auth)):
        info = await to_thread.run_sync(lambda: service.upload_info(p, str(uid)))
        size = 0
        with NamedTemporaryFile() as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > min(info["size"], service.settings.upload_max_bytes):
                    raise Error("upload.too_large", "Upload exceeds declared size", 413)
                await to_thread.run_sync(lambda: f.write(chunk))
            f.flush()
            return await to_thread.run_sync(lambda: service.receive(p, str(uid), f.name))

    @app.post("/v1/uploads/{uid}/commit")
    def commit(uid: UUID, p=Depends(auth), k=Depends(key)):
        return service.commit(p, k, str(uid))

    @app.post("/v1/sources/{sid}/process")
    def process(sid: UUID, body: ProcessBody, p=Depends(auth), k=Depends(key)):
        return service.process(p, k, str(sid), dump(body))

    @app.get("/v1/sources/{sid}")
    def source(sid: UUID, p=Depends(auth)):
        return service.source_get(p, str(sid))

    @app.get("/v1/sources")
    def sources(
        page_size: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
        collection_id: UUID | None = None,
        p=Depends(auth),
    ):
        filters = {"collection_ids": [str(collection_id)]} if collection_id else {}
        return service.list_sources(p, filters, page_size, cursor)

    @app.post("/v1/search")
    def search(body: SearchBody, p=Depends(auth)):
        return service.search(p, dump(body))

    @app.get("/v1/sources/{sid}/outline")
    def outline(
        sid: UUID,
        representation_id: UUID | None = None,
        source_version_id: UUID | None = None,
        page_size: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
        p=Depends(auth),
    ):
        return service.outline(
            p,
            str(sid),
            str(representation_id) if representation_id else None,
            page_size,
            cursor,
            str(source_version_id) if source_version_id else None,
        )

    @app.get("/v1/representations/{rid}/read")
    def read(
        rid: UUID,
        page_size: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
        p=Depends(auth),
    ):
        return service.read(p, str(rid), page_size, cursor)

    @app.get("/v1/assets/{aid}/metadata")
    def asset(aid: UUID, p=Depends(auth)):
        return service.asset_metadata(p, str(aid))

    def binary_response(request, p, **identifiers):
        blob = service.binary(p, **identifiers)
        start, end, status = 0, blob["size"] - 1, 200
        headers = {
            "Content-Disposition": 'attachment; filename="source"',
            "Accept-Ranges": "bytes",
            "ETag": '"' + blob["sha256"] + '"',
            "Content-Type": blob["mime"],
        }
        byte_range = request.headers.get("range")
        if byte_range:
            try:
                import re

                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
                if not match or not any(match.groups()):
                    raise ValueError()
                lo, hi = match.groups()
                if not lo:
                    length = int(hi)
                    if length <= 0:
                        raise ValueError()
                    start = max(0, blob["size"] - length)
                else:
                    start, end = int(lo), min(int(hi), end) if hi else end
                if start > end or start < 0 or start >= blob["size"]:
                    raise ValueError()
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{blob['size']}"
            except (ValueError, TypeError):
                raise Error("asset.range_invalid", "Invalid byte range", 416) from None
        headers["Content-Length"] = str(max(0, end - start + 1))

        def chunks():
            # Recheck ACL between chunks so revocation stops in-flight downloads.
            for chunk in service.store.iter_bytes(blob["key"], start, end):
                service.binary(p, **identifiers)
                yield chunk

        return StreamingResponse(chunks(), status_code=status, headers=headers)

    @app.get("/v1/assets/{aid}/content")
    def content(aid: UUID, request: Request, p=Depends(auth)):
        return binary_response(request, p, aid=str(aid))

    @app.get("/v1/sources/{sid}/versions/{vid}/original")
    def original(sid: UUID, vid: UUID, request: Request, p=Depends(auth)):
        return binary_response(request, p, sid=str(sid), vid=str(vid))

    @app.get("/v1/operations/{oid}")
    def operation(oid: UUID, request: Request, p=Depends(auth)):
        body = service.status(p, str(oid))
        etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(body, headers={"ETag": etag})

    @app.post("/v1/grants")
    def grant(body: GrantBody, p=Depends(auth), k=Depends(key)):
        return service.grant_create(p, k, dump(body))

    @app.post("/v1/grants/{gid}/revoke")
    def grant_revoke(gid: UUID, p=Depends(auth), k=Depends(key)):
        return service.grant_revoke(p, k, str(gid))

    @app.post("/v1/sources/{sid}/revoke")
    def revoke(sid: UUID, p=Depends(auth), k=Depends(key)):
        return service.revoke(p, k, str(sid))

    @app.delete("/v1/sources/{sid}")
    def delete_source(sid: UUID, p=Depends(auth), k=Depends(key)):
        return service.revoke(p, k, str(sid), purge=True)

    @app.post("/v1/references/verify")
    def verify_ref(body: RefBody, p=Depends(auth)):
        return service.validate_ref(p, body.ref)

    @app.post("/v1/sources/{sid}/capture")
    def capture(sid: UUID, p=Depends(auth), k=Depends(key)):
        from wks.infrastructure.capture import capture_source

        return capture_source(service, p, k, str(sid))

    if service.settings.mcp_enabled:
        app.mount("/", mcp_app)
    return app
