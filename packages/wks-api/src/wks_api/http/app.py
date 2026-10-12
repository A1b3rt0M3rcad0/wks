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
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text
from wks_core.domain.models import Error
from wks_core.storage.schema import LATEST_SCHEMA

from wks_api.authentication.session import install_sessions, resolve
from wks_api.contracts import responses as contracts
from wks_api.contracts.schemas import (
    BulkProcessBody,
    ClientBody,
    CollectionBody,
    ContainerPatch,
    CredentialBody,
    ExportBody,
    GrantBody,
    MetadataPatch,
    NamespaceBody,
    ProcessBody,
    RefBody,
    S3ImportBody,
    SearchBody,
    SourceBody,
    VersionBody,
)
from wks_api.mcp.server import build_mcp
from wks_api.server.bootstrap import build
from wks_api.version import VERSION, build_info
from wks_api.web import install_workspace

REQUESTS = Counter("wks_http_requests_total", "HTTP outcomes", ["method", "status"])
LATENCY = Histogram("wks_http_seconds", "HTTP request time", ["method"])
BLOB_BYTES = Gauge("wks_recorded_blob_bytes", "Bytes in non-retired blob records")
JOBS = Gauge("wks_jobs", "Processing jobs by bounded queue and status", ["queue", "status"])
OLDEST_JOB = Gauge("wks_oldest_queued_job_seconds", "Age of oldest waiting job by queue", ["queue"])
INTEGRITY_FAILURES = Gauge(
    "wks_critical_original_failures", "Known missing or corrupt active original objects"
)


def create_app(service=None, engine=None):
    if service is None:
        service, engine = build()
    mcp = build_mcp(service)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Woobe Knowledge Service",
        version=VERSION,
        lifespan=lifespan,
        responses={
            400: {"model": contracts.ErrorResponse},
            401: {"model": contracts.ErrorResponse},
            403: {"model": contracts.ErrorResponse},
            404: {"model": contracts.ErrorResponse},
            409: {"model": contracts.ErrorResponse},
            412: {"model": contracts.ErrorResponse},
            422: {"model": contracts.ErrorResponse},
            428: {"model": contracts.ErrorResponse},
            429: {"model": contracts.ErrorResponse},
            503: {"model": contracts.ErrorResponse},
        },
    )
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
            if (
                (request.url.path.startswith("/v1") and "/uploads/" not in request.url.path)
                or request.url.path.startswith("/mcp")
            ) or request.url.path in {
                "/app/session",
                "/app/setup",
                "/app/recover",
                "/app/recovery-token",
            }:
                # Bound JSON bodies before Pydantic parsing; uploads have separate streaming bounds.
                limit = (
                    8192
                    if request.url.path
                    in {"/app/session", "/app/setup", "/app/recover", "/app/recovery-token"}
                    else min(service.settings.upload_max_bytes, 2 * 1024 * 1024)
                )
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
                    "write_outcome": "reconcile_required"
                    if exc.code == "operation.reconcile_required"
                    else "unknown"
                    if exc.status >= 500 and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                    else "rejected",
                    "request_id": request_id,
                },
                status_code=exc.status,
            )
        except Exception:
            response = JSONResponse(
                {
                    "code": "operation.infrastructure_unavailable",
                    "message": "Service temporarily unavailable",
                    "write_outcome": "unknown"
                    if request.method in {"POST", "PUT", "PATCH", "DELETE"}
                    else "rejected",
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
        metric_method = (
            request.method
            if request.method
            in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
            else "OTHER"
        )
        REQUESTS.labels(metric_method, response.status_code).inc()
        LATENCY.labels(metric_method).observe(time.monotonic() - started)
        return response

    @app.exception_handler(Error)
    async def error_handler(request, exc):
        return JSONResponse(
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
                "retryable": exc.retryable,
                "write_outcome": "reconcile_required"
                if exc.code == "operation.reconcile_required"
                else "unknown"
                if exc.status >= 500 and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                else "rejected",
            },
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Input values can contain credentials/content: do not echo them in errors.
        return JSONResponse(
            {
                "code": "contract.invalid",
                "write_outcome": "rejected",
                "message": "Invalid request",
                "request_id": request.state.request_id,
                "retryable": False,
                "details": [{"loc": e["loc"], "type": e["type"]} for e in exc.errors()],
            },
            status_code=422,
        )

    def auth(
        request: Request,
        authorization: str | None = Header(default=None),
        x_wks_grant: str | None = Header(default=None),
    ):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if token:
            return service.bind_context(
                service.authenticate(token, x_wks_grant), request.headers.get("x-wks-namespace")
            )
        return service.bind_context(
            resolve(
                service, request, mutation=request.method in {"POST", "PUT", "PATCH", "DELETE"}
            ),
            request.headers.get("x-wks-namespace"),
        )

    def key(idempotency_key: str = Header(default="")):
        return idempotency_key

    def dump(body):
        return body.model_dump(mode="json", exclude_none=True)

    @app.get(
        "/health/live",
        tags=["operations"],
        response_model=contracts.HealthResponse,
        response_model_exclude_unset=True,
    )
    def live():
        return {"status": "live", **build_info()}

    @app.get(
        "/health/ready",
        tags=["operations"],
        response_model=contracts.HealthResponse,
        response_model_exclude_unset=True,
    )
    def ready():
        try:
            with service.sessions() as db:
                migration = db.scalar(text("SELECT version_num FROM alembic_version"))
                db.execute(text("SELECT to_tsvector('wks_portuguese', 'saúde')"))
                from wks_core.storage.integrity import known_critical_failures

                if known_critical_failures(db):
                    raise RuntimeError("Critical original integrity unavailable")
            if migration != LATEST_SCHEMA or not service.store.ready():
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
        with service.sessions() as db:
            service.operator(db, p)
            BLOB_BYTES.set(
                db.scalar(
                    text(
                        "SELECT coalesce(sum(b.size),0) FROM blob_objects b WHERE NOT EXISTS(SELECT 1 FROM blob_retirements d WHERE d.blob_id=b.id AND d.state='deleted')"
                    )
                )
            )
            for queue in ("native", "docling", "media"):
                for status in (
                    "queued",
                    "running",
                    "retry_wait",
                    "succeeded",
                    "failed",
                    "cancelled",
                ):
                    JOBS.labels(queue, status).set(
                        db.scalar(
                            text(
                                "SELECT count(*) FROM processing_runs WHERE queue=:queue AND status=:status"
                            ),
                            {"queue": queue, "status": status},
                        )
                    )
                OLDEST_JOB.labels(queue).set(
                    db.scalar(
                        text(
                            "SELECT coalesce(extract(epoch FROM now()-min(created_at)),0) FROM processing_runs WHERE queue=:queue AND status IN ('queued','retry_wait')"
                        ),
                        {"queue": queue},
                    )
                )
            from wks_core.storage.integrity import known_critical_failures

            INTEGRITY_FAILURES.set(known_critical_failures(db))
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post(
        "/v1/namespaces",
        response_model=contracts.NamespaceResponse,
        response_model_exclude_unset=True,
    )
    def namespace_create(body: NamespaceBody, p=Depends(auth), k=Depends(key)):
        return service.namespace_create(p, k, dump(body))

    @app.get(
        "/v1/namespaces/{id}",
        response_model=contracts.NamespaceResponse,
        response_model_exclude_unset=True,
    )
    def namespace_get(id: UUID, response: Response, p=Depends(auth)):
        result = service.namespace_get(p, str(id))
        response.headers["ETag"] = f'"{result["revision"]}"'
        return result

    @app.get(
        "/v1/namespaces",
        response_model=contracts.Page[contracts.NamespaceResponse],
        response_model_exclude_unset=True,
    )
    def namespaces(
        p=Depends(auth),
        page_size: int = Query(default=100, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
    ):
        return service.namespace_list(p, page_size, cursor)

    @app.get(
        "/v1/namespaces/{id}/summary",
        response_model=contracts.SummaryResponse,
        response_model_exclude_unset=True,
    )
    def namespace_summary(id: UUID, p=Depends(auth)):
        return service.namespace_summary(p, str(id))

    @app.get(
        "/v1/namespaces/{id}/collections",
        response_model=contracts.Page[contracts.CollectionResponse],
        response_model_exclude_unset=True,
    )
    def collections(
        id: UUID,
        p=Depends(auth),
        page_size: int = Query(default=100, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
    ):
        return service.collection_list(p, str(id), page_size, cursor)

    @app.post(
        "/v1/collections",
        response_model=contracts.CollectionResponse,
        response_model_exclude_unset=True,
    )
    def collection_create(body: CollectionBody, p=Depends(auth), k=Depends(key)):
        return service.collection_create(p, k, dump(body))

    @app.put(
        "/v1/collections/{cid}/sources/{sid}",
        response_model=contracts.LinkResponse,
        response_model_exclude_unset=True,
    )
    def collection_link(cid: UUID, sid: UUID, p=Depends(auth), k=Depends(key)):
        return service.collection_link(p, k, str(cid), str(sid))

    @app.delete(
        "/v1/collections/{cid}/sources/{sid}",
        response_model=contracts.LinkResponse,
        response_model_exclude_unset=True,
    )
    def collection_unlink(cid: UUID, sid: UUID, p=Depends(auth), k=Depends(key)):
        return service.collection_link(p, k, str(cid), str(sid), unlink=True)

    @app.post(
        "/v1/sources",
        status_code=201,
        response_model=contracts.ReceiptResponse,
        response_model_exclude_unset=True,
    )
    def register(body: SourceBody, p=Depends(auth), k=Depends(key)):
        return service.register(p, k, dump(body))

    @app.post(
        "/v1/sources/{sid}/versions",
        status_code=201,
        response_model=contracts.ReceiptResponse,
        response_model_exclude_unset=True,
    )
    def version_create(sid: UUID, body: VersionBody, p=Depends(auth), k=Depends(key)):
        return service.new_version(p, k, str(sid), dump(body))

    @app.put(
        "/v1/uploads/{uid}/content",
        response_model=contracts.UploadResponse,
        response_model_exclude_unset=True,
    )
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

    @app.post(
        "/v1/uploads/{uid}/commit",
        response_model=contracts.ReceiptResponse,
        response_model_exclude_unset=True,
    )
    def commit(uid: UUID, p=Depends(auth), k=Depends(key)):
        return service.commit(p, k, str(uid))

    @app.post(
        "/v1/sources/{sid}/process",
        response_model=contracts.ReceiptResponse,
        response_model_exclude_unset=True,
    )
    def process(sid: UUID, body: ProcessBody, p=Depends(auth), k=Depends(key)):
        return service.process(p, k, str(sid), dump(body))

    @app.get(
        "/v1/sources/{sid}",
        response_model=contracts.SourceResponse,
        response_model_exclude_unset=True,
    )
    def source(sid: UUID, response: Response, p=Depends(auth)):
        result = service.source_get(p, str(sid))
        response.headers["ETag"] = f'"{result["revision"]}"'
        return result

    @app.get(
        "/v1/sources",
        response_model=contracts.Page[contracts.SourceResponse],
        response_model_exclude_unset=True,
    )
    def sources(
        page_size: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
        collection_id: UUID | None = None,
        namespace_id: UUID | None = None,
        availability: str | None = Query(default=None, max_length=40),
        p=Depends(auth),
    ):
        filters = {"collection_ids": [str(collection_id)]} if collection_id else {}
        if namespace_id:
            filters["namespace_id"] = str(namespace_id)
        if availability:
            filters["availability"] = availability
        return service.list_sources(p, filters, page_size, cursor)

    @app.post(
        "/v1/search", response_model=contracts.SearchResponse, response_model_exclude_unset=True
    )
    def search(body: SearchBody, p=Depends(auth)):
        return service.search(p, dump(body))

    @app.get(
        "/v1/sources/{sid}/outline",
        response_model=contracts.ReadResponse,
        response_model_exclude_unset=True,
    )
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

    @app.get(
        "/v1/representations/{rid}/read",
        response_model=contracts.ReadResponse,
        response_model_exclude_unset=True,
    )
    def read(
        rid: UUID,
        page_size: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
        p=Depends(auth),
    ):
        return service.read(p, str(rid), page_size, cursor)

    @app.get(
        "/v1/assets/{aid}/metadata",
        response_model=contracts.AssetResponse,
        response_model_exclude_unset=True,
    )
    def asset(aid: UUID, p=Depends(auth)):
        return service.asset_metadata(p, str(aid))

    def binary_response(request, p, **identifiers):
        def resolve_binary():
            return (
                service.export_binary(p, identifiers["eid"])
                if "eid" in identifiers
                else service.binary(p, **identifiers)
            )

        blob = resolve_binary()
        start, end, status = 0, blob["size"] - 1, 200
        headers = {
            "Content-Disposition": 'attachment; filename="source"',
            "Accept-Ranges": "bytes",
            "ETag": '"' + blob["sha256"] + '"',
            "Content-Type": blob["mime"],
            "X-WKS-Object-SHA256": blob["sha256"],
        }
        if request.headers.get("if-none-match") in {headers["ETag"], "*"}:
            return Response(status_code=304, headers=headers)
        byte_range = request.headers.get("range")
        if byte_range and request.headers.get("if-range") not in {None, headers["ETag"]}:
            byte_range = None
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

        if request.method == "HEAD":
            return Response(status_code=status, headers=headers)

        def chunks():
            checked = time.monotonic()
            since = 0
            for chunk in service.store.iter_bytes(blob["key"], start, end):
                instant = time.monotonic()
                if (
                    since + len(chunk) > service.settings.stream_revalidation_bytes
                    or instant - checked >= service.settings.stream_revalidation_seconds
                ):
                    if not request.headers.get("authorization", "").startswith("Bearer "):
                        resolve(service, request)
                    service.binary_gate(p, blob)
                    checked = instant
                    since = 0
                since += len(chunk)
                yield chunk

        return StreamingResponse(chunks(), status_code=status, headers=headers)

    @app.head("/v1/assets/{aid}/content")
    @app.get("/v1/assets/{aid}/content")
    def content(aid: UUID, request: Request, p=Depends(auth)):
        return binary_response(request, p, aid=str(aid))

    @app.head("/v1/sources/{sid}/versions/{vid}/original")
    @app.get("/v1/sources/{sid}/versions/{vid}/original")
    def original(sid: UUID, vid: UUID, request: Request, p=Depends(auth)):
        return binary_response(request, p, sid=str(sid), vid=str(vid))

    @app.get(
        "/v1/operations/{oid}",
        response_model=contracts.OperationResponse,
        response_model_exclude_unset=True,
    )
    def operation(oid: UUID, request: Request, p=Depends(auth)):
        body = service.status(p, str(oid))
        etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(body, headers={"ETag": etag})

    @app.post(
        "/v1/grants", response_model=contracts.GrantResponse, response_model_exclude_unset=True
    )
    def grant(body: GrantBody, p=Depends(auth), k=Depends(key)):
        return service.grant_create(p, k, dump(body))

    @app.post(
        "/v1/grants/{gid}/revoke",
        response_model=contracts.GrantResponse,
        response_model_exclude_unset=True,
    )
    def grant_revoke(gid: UUID, p=Depends(auth), k=Depends(key)):
        return service.grant_revoke(p, k, str(gid))

    @app.post(
        "/v1/sources/{sid}/revoke",
        response_model=contracts.LifecycleResponse,
        response_model_exclude_unset=True,
    )
    def revoke(
        sid: UUID,
        p=Depends(auth),
        k=Depends(key),
        x_wks_confirmation: str | None = Header(default=None),
    ):
        return service.revoke(p, k, str(sid), confirmation_token=x_wks_confirmation)

    @app.delete(
        "/v1/sources/{sid}",
        response_model=contracts.LifecycleResponse,
        response_model_exclude_unset=True,
    )
    def delete_source(
        sid: UUID,
        p=Depends(auth),
        k=Depends(key),
        x_wks_confirmation: str | None = Header(default=None),
    ):
        return service.revoke(p, k, str(sid), purge=True, confirmation_token=x_wks_confirmation)

    @app.post(
        "/v1/references/verify",
        response_model=contracts.ReferenceResponse,
        response_model_exclude_unset=True,
    )
    def verify_ref(body: RefBody, p=Depends(auth)):
        return service.validate_ref(p, body.ref)

    @app.post(
        "/v1/sources/{sid}/capture",
        response_model=contracts.ReceiptResponse,
        response_model_exclude_unset=True,
    )
    def capture(sid: UUID, p=Depends(auth), k=Depends(key)):
        from wks_core.storage.capture import capture_source

        return capture_source(service, p, k, str(sid))

    @app.patch(
        "/v1/sources/{sid}",
        response_model=contracts.MetadataResponse,
        response_model_exclude_unset=True,
    )
    def source_patch(
        sid: UUID,
        body: MetadataPatch,
        p=Depends(auth),
        k=Depends(key),
        if_match: str | None = Header(default=None),
    ):
        return service.metadata_patch(p, k, "source", str(sid), dump(body), if_match)

    @app.patch(
        "/v1/namespaces/{id}",
        response_model=contracts.MetadataResponse,
        response_model_exclude_unset=True,
    )
    def namespace_patch(
        id: UUID,
        body: ContainerPatch,
        p=Depends(auth),
        k=Depends(key),
        if_match: str | None = Header(default=None),
    ):
        return service.metadata_patch(p, k, "namespace", str(id), dump(body), if_match)

    @app.patch(
        "/v1/collections/{id}",
        response_model=contracts.MetadataResponse,
        response_model_exclude_unset=True,
    )
    def collection_patch(
        id: UUID,
        body: ContainerPatch,
        p=Depends(auth),
        k=Depends(key),
        if_match: str | None = Header(default=None),
    ):
        return service.metadata_patch(p, k, "collection", str(id), dump(body), if_match)

    @app.get(
        "/v1/collections/{id}",
        response_model=contracts.CollectionResponse,
        response_model_exclude_unset=True,
    )
    def collection_get(id: UUID, p=Depends(auth)):

        return service.collection_get(p, str(id))

    @app.get("/v1/sources/{sid}/history", response_model=contracts.HistoryResponse)
    def history(
        sid: UUID,
        page_size: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
        p=Depends(auth),
    ):
        return service.source_history(p, str(sid), page_size, cursor)

    @app.get("/v1/sources/{sid}/versions", response_model=contracts.Page[contracts.VersionResponse])
    def versions(
        sid: UUID,
        page_size: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
        p=Depends(auth),
    ):
        return service.versions_list(p, str(sid), page_size, cursor)

    @app.post("/v1/sources/{sid}/preflight", response_model=contracts.PreflightResponse)
    def preflight(
        sid: UUID,
        action: str = Query(default="delete", pattern="^(delete|revoke)$"),
        p=Depends(auth),
    ):
        return service.preflight(p, str(sid), action)

    @app.get("/v1/representations/{rid}/blocks", response_model=contracts.ReadResponse)
    def blocks(
        rid: UUID,
        page_size: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
        p=Depends(auth),
    ):
        return service.read(p, str(rid), page_size, cursor)

    @app.get("/v1/representations/{rid}/blocks/{bid}", response_model=contracts.BlockPageResponse)
    def block(
        rid: UUID,
        bid: UUID,
        offset: int = Query(default=0, ge=0),
        page_chars: int | None = Query(default=None, ge=1, le=16000),
        p=Depends(auth),
    ):
        return service.block(p, str(rid), str(bid), offset, page_chars)

    @app.get(
        "/v1/receipts/{operation_key}",
        response_model=contracts.ReceiptResponse,
        response_model_exclude_unset=True,
    )
    def receipt(operation_key: str, p=Depends(auth)):
        from wks_core.domain.models import not_found

        result = service.receipt(p, operation_key)
        if result is None:
            raise not_found()
        return {**result, "write_outcome": "confirmed"}

    @app.get("/v1/grants", response_model=contracts.Page[contracts.GrantResponse])
    def grants(
        p=Depends(auth),
        page_size: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
    ):
        return service.grants_list(p, page_size, cursor)

    @app.get("/v1/namespaces/{id}/events", response_model=contracts.Page[contracts.EventResponse])
    def events(
        id: UUID,
        p=Depends(auth),
        page_size: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=4096),
    ):
        return service.events_list(p, str(id), page_size, cursor)

    @app.post("/v1/clients", response_model=contracts.ClientResponse)
    def client(body: ClientBody, p=Depends(auth), k=Depends(key)):
        return service.client_create(p, k, dump(body))

    @app.post("/v1/clients/{id}/credentials", response_model=contracts.CredentialResponse)
    def credential(id: UUID, body: CredentialBody, p=Depends(auth), k=Depends(key)):
        return service.credential_create(p, k, str(id), body.description, body.ttl_seconds)

    @app.post("/v1/credentials/{id}/revoke", response_model=contracts.CredentialResponse)
    def credential_revoke(id: UUID, p=Depends(auth), k=Depends(key)):
        return service.credential_revoke(p, k, str(id))

    @app.post("/v1/representations/{rid}/exports", response_model=contracts.ExportResponse)
    def export_create(rid: UUID, body: ExportBody, p=Depends(auth), k=Depends(key)):
        return service.export_create(p, k, str(rid), body.format)

    @app.get("/v1/exports/{id}", response_model=contracts.ExportResponse)
    def export_status(id: UUID, p=Depends(auth)):
        return service.export_get(p, str(id))

    @app.head("/v1/exports/{id}/content")
    @app.get("/v1/exports/{id}/content")
    def export_content(id: UUID, request: Request, p=Depends(auth)):
        return binary_response(request, p, eid=str(id))

    @app.get("/v1/capabilities", response_model=contracts.CapabilityResponse)
    def capabilities(p=Depends(auth)):
        return service.capabilities(p)

    @app.post(
        "/v1/namespaces/{id}/reprocess", response_model=contracts.Page[contracts.ReceiptResponse]
    )
    def bulk_process(id: UUID, body: BulkProcessBody, p=Depends(auth), k=Depends(key)):
        return service.bulk_process(p, k, str(id), dump(body))

    @app.post("/v1/search/suggestions", response_model=contracts.SuggestionsResponse)
    def suggestions(body: SearchBody, p=Depends(auth)):
        return service.suggestions(p, dump(body))

    @app.get("/v1/uploads/{uid}", response_model=contracts.UploadResponse)
    def upload_status(uid: UUID, p=Depends(auth)):
        return service.upload_status(p, str(uid))

    @app.put("/v1/uploads/{uid}/parts", response_model=contracts.UploadResponse)
    async def upload_part(
        uid: UUID,
        request: Request,
        offset: int = Query(ge=0),
        checksum_sha256: str = Query(pattern=r"^[a-f0-9]{64}$"),
        p=Depends(auth),
    ):
        await to_thread.run_sync(lambda: service.upload_info(p, str(uid)))
        count = 0
        with NamedTemporaryFile() as file:
            async for chunk in request.stream():
                count += len(chunk)
                if count > service.settings.upload_max_part_bytes:
                    raise Error("upload.too_large", "Upload part exceeds limit", 413)
                await to_thread.run_sync(lambda: file.write(chunk))
            file.flush()
            return await to_thread.run_sync(
                lambda: service.receive_part(p, str(uid), file.name, offset, checksum_sha256)
            )

    @app.post("/v1/uploads/{uid}/abort", response_model=contracts.UploadResponse)
    def abort_upload(uid: UUID, p=Depends(auth), k=Depends(key)):
        return service.upload_abort(p, k, str(uid))

    @app.get("/v1/clients/me", response_model=contracts.ClientResponse)
    def current_client(p=Depends(auth)):
        return service.current_client(p)

    @app.get("/v1/grants/{id}", response_model=contracts.GrantResponse)
    def grant_get(id: UUID, p=Depends(auth)):
        return service.grant_get(p, str(id))

    @app.get(
        "/v1/clients/{id}/credentials", response_model=contracts.Page[contracts.CredentialResponse]
    )
    def credentials(id: UUID, p=Depends(auth)):
        return service.credentials_list(p, str(id))

    @app.get("/v1/sources/{id}/deletion", response_model=contracts.LifecycleResponse)
    def deletion_status(id: UUID, p=Depends(auth)):
        return service.deletion_status(p, str(id))

    @app.get("/v1/representations/{id}", response_model=contracts.RepresentationResponse)
    def representation_metadata(id: UUID, p=Depends(auth)):
        return service.representation_metadata(p, str(id))

    @app.post("/v1/namespaces/{id}/preflight", response_model=contracts.ContainerPreflightResponse)
    def namespace_preflight(id: UUID, p=Depends(auth)):
        return service.container_preflight(p, "namespace", str(id))

    @app.post("/v1/collections/{id}/preflight", response_model=contracts.ContainerPreflightResponse)
    def collection_preflight(id: UUID, p=Depends(auth)):
        return service.container_preflight(p, "collection", str(id))

    @app.delete("/v1/namespaces/{id}", response_model=contracts.ContainerLifecycleResponse)
    def namespace_delete(
        id: UUID, p=Depends(auth), k=Depends(key), x_wks_confirmation: str = Header()
    ):
        return service.container_delete(p, k, "namespace", str(id), x_wks_confirmation)

    @app.delete("/v1/collections/{id}", response_model=contracts.ContainerLifecycleResponse)
    def collection_delete(
        id: UUID, p=Depends(auth), k=Depends(key), x_wks_confirmation: str = Header()
    ):
        return service.container_delete(p, k, "collection", str(id), x_wks_confirmation)

    @app.post("/v1/imports/s3", response_model=contracts.ReceiptResponse)
    def import_s3(body: S3ImportBody, p=Depends(auth), k=Depends(key)):
        from wks_core.storage.importer import import_s3

        return import_s3(
            service,
            p,
            k,
            str(body.namespace_id),
            body.bucket,
            body.object_key,
            body.title,
            body.external_ref,
        )

    install_sessions(app, service)
    install_workspace(app)
    from fastapi.routing import APIRoute

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path.endswith(("/content", "/original")):
            route.response_class = Response
            binary = {
                "description": "Authorized bytes; X-WKS-SHA256 is the content checksum",
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
                "headers": {
                    name: {"schema": {"type": "string"}}
                    for name in (
                        "ETag",
                        "Content-Length",
                        "Content-Type",
                        "X-WKS-SHA256",
                        "Accept-Ranges",
                        "Content-Range",
                    )
                },
            }
            route.responses[200] = binary
            route.responses[206] = binary | {"description": "Authorized byte range"}
            route.responses[304] = {"description": "Authorized unchanged content"}
        elif route.path == "/metrics":
            route.response_class = Response
            route.responses[200] = {
                "description": "Operator-only Prometheus exposition",
                "content": {"text/plain": {"schema": {"type": "string"}}},
            }
    if service.settings.mcp_enabled:
        app.mount("/", mcp_app)
    return app
