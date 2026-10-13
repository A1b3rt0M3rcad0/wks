import hashlib
import hmac
import os
import secrets
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import (
    delete,
    func,
    select,
    text,
)
from wks_core.application.authorization import AccessControlService
from wks_core.application.budgets import BudgetService
from wks_core.application.capabilities import CapabilitiesService
from wks_core.application.exports import ExportService
from wks_core.application.management import ManagementService
from wks_core.application.reading import ReadingService
from wks_core.application.search import SearchService
from wks_core.application.transfers import TransferService
from wks_core.application.utilities import digest, file_digest
from wks_core.application.worker_status import WorkerStatusService
from wks_core.domain.models import Error, new_id, not_found
from wks_core.storage.database import (
    Asset,
    Audit,
    Blob,
    Client,
    Collection,
    CollectionSource,
    CursorSnapshot,
    Event,
    Grant,
    Namespace,
    ProcessingRun,
    Representation,
    Segment,
    Source,
    SourceVersion,
    Upload,
    now,
)


class Service(
    BudgetService,
    TransferService,
    SearchService,
    CapabilitiesService,
    WorkerStatusService,
    ExportService,
    ReadingService,
    ManagementService,
    AccessControlService,
):
    """Shared transaction and policy boundary used by HTTP, MCP and workers."""

    def __init__(self, settings, sessions, store):
        self.settings, self.sessions, self.store = settings, sessions, store
        from wks_core.storage.privacy import PrivacyLedger

        self.privacy_ledger = PrivacyLedger(self)
        marker = settings.storage_path.parent / (
            "restore-blocked-"
            + hashlib.sha256(settings.database_url.rsplit("/", 1)[-1].encode()).hexdigest()[:16]
        )
        self.restore_blocked = marker.exists()
        self.restore_marker = marker
        self.cursor_key = settings.cursor_secret
        if not self.cursor_key:
            if settings.env == "production":
                raise RuntimeError("WKS_CURSOR_SECRET is required in production")
            root = Path(".local")
            root.mkdir(exist_ok=True)
            path = root / "cursor-key"
            if not path.exists():
                with NamedTemporaryFile(mode="w", dir=root) as f:
                    f.write(secrets.token_hex(32))
                    f.flush()
                    os.fsync(f.fileno())
                    try:
                        os.link(f.name, path)
                    except FileExistsError:
                        pass
            self.cursor_key = path.read_text().strip()

    def checkpoint(self, name):
        hook = getattr(self, "fault_injector", None)
        if hook:
            hook(name)

    def event(self, db, src, event_type, **fields):
        payload = {
            "source_id": src.id,
            "namespace_id": src.namespace_id,
            "schema_version": 1,
            **fields,
        }
        db.add(Event(namespace_id=src.namespace_id, type=f"wks.{event_type}.v1", payload=payload))

    def namespace_create(self, p, key, body):
        def op(db):
            existing = (
                db.scalar(
                    select(Namespace).where(
                        Namespace.client_id == p.client_id,
                        Namespace.external_ref == body.get("external_ref"),
                    )
                )
                if body.get("external_ref")
                else None
            )
            ns = existing or Namespace(
                client_id=p.client_id, title=body["title"], external_ref=body.get("external_ref")
            )
            db.add(ns)
            db.flush()
            return {"id": ns.id, "title": ns.title}

        return self.mutate(p, key, {"op": "namespace", **body}, op)

    def namespace_get(self, p, id):
        with self.sessions() as db:
            ns = self.write(db, p, id)
            return {
                "id": ns.id,
                "title": ns.title,
                "external_ref": ns.external_ref,
                "revision": ns.revision,
                "state": ns.state,
            }

    def namespace_list(self, p, page_size=100, cursor=None):
        size = min(max(page_size, 1), 100)
        context = {"op": "namespaces", "page_size": size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            g = self.grant(db, p, "namespace:read")
            if p.role != "client" and not g:
                raise Error("auth.scope_mismatch", "Client context required", 403)
            query = (
                select(Namespace).where(Namespace.id == g.namespace_id)
                if g
                else select(Namespace).where(Namespace.client_id == p.client_id)
            )
            if p.context_namespace_id:
                query = query.where(Namespace.id == p.context_namespace_id)
            if state["position"]:
                query = query.where(Namespace.id > state["position"])
            rows = db.scalars(query.order_by(Namespace.id).limit(size + 1)).all()
            return {
                "items": [{"id": n.id, "title": n.title} for n in rows[:size]],
                "next_cursor": self.cursor(p, context, None, position=rows[size - 1].id)
                if len(rows) > size
                else None,
            }

    def collection_list(self, p, namespace_id, page_size=100, cursor=None):
        size = min(max(page_size, 1), 100)
        context = {"op": "collections", "namespace_id": namespace_id, "page_size": size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            self.write(db, p, namespace_id, action="collection:read")
            query = select(Collection).where(Collection.namespace_id == namespace_id)
            g = self.grant(db, p)
            if g and g.collection_id:
                query = query.where(Collection.id == g.collection_id)
            if state["position"]:
                query = query.where(Collection.id > state["position"])
            rows = db.scalars(query.order_by(Collection.id).limit(size + 1)).all()
            return {
                "items": [{"id": c.id, "title": c.title} for c in rows[:size]],
                "next_cursor": self.cursor(p, context, None, position=rows[size - 1].id)
                if len(rows) > size
                else None,
            }

    def namespace_summary(self, p, namespace_id):
        with self.sessions() as db:
            ns = self.write(db, p, namespace_id)
            grant = self.grant(db, p)
            scope = (
                Source.id.in_(
                    select(CollectionSource.source_id).where(
                        CollectionSource.collection_id == grant.collection_id
                    )
                )
                if grant and grant.collection_id
                else text("true")
            )
            counts = dict(
                db.execute(
                    select(Source.availability, func.count(Source.id))
                    .where(
                        Source.namespace_id == namespace_id,
                        scope,
                        Source.state.in_(["registered", "stored"]),
                        Source.deleted_at.is_(None),
                    )
                    .group_by(Source.availability)
                ).all()
            )
            pending = db.scalar(
                select(func.count(ProcessingRun.id))
                .join(SourceVersion, SourceVersion.id == ProcessingRun.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(
                    Source.namespace_id == namespace_id,
                    scope,
                    Source.state.in_(["registered", "stored"]),
                    Source.deleted_at.is_(None),
                    ProcessingRun.status.in_(["queued", "running", "retry_wait"]),
                )
            )
            return {
                "namespace_id": ns.id,
                "title": ns.title,
                "total_sources": sum(counts.values()),
                "availability_counts": counts,
                "pending_jobs": pending,
                "collection_count": db.scalar(
                    select(func.count(Collection.id)).where(
                        Collection.namespace_id == namespace_id,
                        Collection.id == grant.collection_id
                        if grant and grant.collection_id
                        else text("true"),
                    )
                ),
            }

    def collection_create(self, p, key, body):
        def op(db):
            self.write(db, p, body["namespace_id"])
            c = Collection(namespace_id=body["namespace_id"], title=body["title"])
            db.add(c)
            db.flush()
            return {"id": c.id, "namespace_id": c.namespace_id, "title": c.title}

        return self.mutate(p, key, {"op": "collection", **body}, op)

    def collection_link(self, p, key, cid, sid, unlink=False):
        def op(db):
            src = self.source(db, p, sid, writing=True)
            c = db.get(Collection, cid)
            if not c or c.namespace_id != src.namespace_id:
                raise not_found()
            delegation = self.grant(db, p)
            if delegation and delegation.collection_id and delegation.collection_id != cid:
                raise not_found()
            edge = db.get(CollectionSource, (cid, sid))
            if unlink and edge:
                db.delete(edge)
            elif not edge and not unlink:
                db.add(
                    CollectionSource(
                        collection_id=cid, source_id=sid, namespace_id=src.namespace_id
                    )
                )
            return {"collection_id": cid, "source_id": sid, "linked": not unlink}

        return self.mutate(p, key, {"op": "link", "cid": cid, "sid": sid, "unlink": unlink}, op)

    def stage_blob(self, namespace_id, path, mime, kind="put", resource_id=None):
        from wks_core.storage.journal import stage

        return stage(self, namespace_id, path, mime, kind, resource_id)

    def attach_blob(self, db, journal_id, namespace_id):
        from wks_core.storage.journal import attach

        return attach(self, db, journal_id, namespace_id)

    def save_blob(self, db, namespace_id, path, mime):
        # Compatibility for internal adapters; high-volume paths stage before opening DB transactions.
        identity = self.stage_blob(namespace_id, path, mime)
        return self.attach_blob(db, identity, namespace_id)

    def prepare_text(self, p, key, body, source_id=None):
        if body.get("text") is None:
            return None
        if not key or len(key) > 180:
            raise Error("operation.idempotency_required", "Idempotency-Key required (max 180)")
        raw = body["text"].encode()
        if len(raw) > self.settings.upload_max_bytes:
            raise Error("upload.too_large", "Text exceeds limit", 413)
        with self.sessions() as db:
            db.info["wks_action"] = "source:version" if source_id else "source:create"
            namespace_id = (
                self.source(db, p, source_id, writing=True).namespace_id
                if source_id
                else body["namespace_id"]
            )
            self.write(db, p, namespace_id)
        resource = (
            digest({"namespace": namespace_id, "external_ref": body["external_ref"]})
            if body.get("external_ref") and not source_id
            else digest({"caller": p.client_id, "key": key, "source_id": source_id, "body": body})
        )
        with NamedTemporaryFile() as path:
            path.write(raw)
            path.flush()
            return self.stage_blob(namespace_id, path.name, "text/plain", "text", resource)

    def add_version(self, db, src, body, staged=None):
        revision = (
            db.scalar(
                select(func.max(SourceVersion.revision_no)).where(SourceVersion.source_id == src.id)
            )
            or 0
        ) + 1
        upload = body.get("upload")
        max_input = db.get(Namespace, src.namespace_id).policy.get(
            "max_input_bytes", self.settings.upload_max_bytes
        )
        if (upload["byte_size"] if upload else len(body.get("text", "").encode())) > max_input:
            raise Error("upload.too_large", "Namespace input budget exceeded", 413)
        v = SourceVersion(
            source_id=src.id,
            namespace_id=src.namespace_id,
            revision_no=revision,
            external_uri=src.external_uri,
            original_filename=upload["filename"] if upload else None,
            declared_media_type=upload["declared_media_type"] if upload else "text/plain",
        )
        db.add(v)
        db.flush()
        result = {"source_id": src.id, "source_version_id": v.id}
        if upload:
            if upload["byte_size"] > self.settings.upload_max_bytes:
                raise Error("upload.too_large", "Upload exceeds limit", 413)
            self.quota(db, src.namespace_id, upload["byte_size"])
            u = Upload(
                source_version_id=v.id,
                expected_sha=upload["checksum_sha256"],
                expected_size=upload["byte_size"],
                expires_at=now() + timedelta(hours=24),
            )
            db.add(u)
            db.flush()
            result.update(upload_id=u.id, status="registered", next_action="upload_content")
        else:
            if src.kind == "bookmark":
                v.media_type = "text/uri-list"
            else:
                raw = body.get("text", "").encode()
                if len(raw) > self.settings.upload_max_bytes:
                    raise Error("upload.too_large", "Text exceeds limit", 413)
                if not staged:
                    raise Error(
                        "operation.stage_required",
                        "Text must be staged before attachment",
                        503,
                        True,
                    )
                blob = self.attach_blob(db, staged, src.namespace_id)
                v.original_blob_id, v.checksum, v.byte_size = blob.id, blob.sha256, blob.size
                v.media_type = "text/plain"
            v.committed = True
            src.state, src.current_version_id = "stored", v.id
            run = self.enqueue(db, src, v, "initial")
            result.update(status="stored", operation_id=run.id)
        self.event(db, src, "source.registered", source_version_id=v.id)
        return result

    def quota(self, db, namespace_id, size):
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:ns, 1))"), {"ns": namespace_id}
        )
        used = db.scalar(
            text("""SELECT coalesce(sum(size),0) FROM blob_objects
          WHERE namespace_id=:ns AND id IN (
             SELECT v.original_blob_id FROM source_versions v JOIN sources s ON s.id=v.source_id
               WHERE s.state<>'deleted' AND v.original_blob_id IS NOT NULL
             UNION SELECT a.blob_id FROM assets a JOIN representations r ON a.representation_id=r.id
               JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id WHERE s.state<>'deleted'
             UNION SELECT e.blob_id FROM export_artifacts e JOIN representations r ON e.representation_id=r.id
               JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id WHERE s.state<>'deleted' AND e.blob_id IS NOT NULL
             UNION SELECT u.staged_blob_id FROM upload_sessions u JOIN source_versions v ON v.id=u.source_version_id
               JOIN sources s ON s.id=v.source_id WHERE s.state<>'deleted' AND u.staged_blob_id IS NOT NULL
          )"""),
            {"ns": namespace_id},
        )
        from wks_core.storage.stabilization import BlobOperation

        reserved = db.scalar(
            select(func.coalesce(func.sum(BlobOperation.size), 0)).where(
                BlobOperation.namespace_id == namespace_id,
                BlobOperation.state.in_(["proposed", "verified", "reconcile_required"]),
                BlobOperation.kind.not_in(["part", "upload"]),
            )
        )
        upload_reservations = db.scalar(
            text(
                """SELECT coalesce(sum(u.expected_size),0) FROM upload_sessions u JOIN source_versions v ON v.id=u.source_version_id JOIN sources s ON s.id=v.source_id WHERE v.namespace_id=:ns AND s.deleted_at IS NULL AND u.status='pending' AND u.expires_at>now() AND u.staged_blob_id IS NULL"""
            ),
            {"ns": namespace_id},
        )
        if used + reserved + upload_reservations + size > db.get(
            Namespace, namespace_id
        ).policy.get("max_bytes", self.settings.namespace_max_bytes):
            raise Error("upload.quota_exceeded", "Namespace storage limit reached", 413)

    def register(self, p, key, body):
        from wks_core.domain.canonical import validate_metadata

        try:
            validate_metadata(body.get("metadata", {}))
        except ValueError as exc:
            raise Error("contract.invalid", "Invalid bounded metadata") from exc
        self.checkpoint("source.before_prepare")
        staged = self.prepare_text(p, key, body)
        self.checkpoint("source.after_prepare")

        def op(db):
            self.write(db, p, body["namespace_id"])
            from wks_core.domain.namespace_policy import validate_source_metadata

            try:
                validate_source_metadata(
                    body.get("metadata", {}), db.get(Namespace, body["namespace_id"]).policy
                )
            except ValueError as exc:
                raise Error(
                    "contract.invalid", "Source metadata violates namespace policy"
                ) from exc
            external_ref = body.get("external_ref")
            if external_ref:
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:ref,42))"),
                    {"ref": body["namespace_id"] + ":" + external_ref},
                )
                existing = db.scalar(
                    select(Source).where(
                        Source.namespace_id == body["namespace_id"],
                        Source.external_ref == external_ref,
                    )
                )
                if existing:
                    if existing.state not in {"stored", "registered"}:
                        raise Error(
                            "source.external_ref_conflict", "External reference is tombstoned", 409
                        )
                    version = (
                        db.get(SourceVersion, existing.current_version_id)
                        if existing.current_version_id
                        else None
                    )
                    expected = (
                        body.get("upload", {}).get("checksum_sha256")
                        if body.get("upload")
                        else hashlib.sha256(body.get("text", "").encode()).hexdigest()
                    )
                    if version and version.checksum == expected:
                        run = db.scalar(
                            select(ProcessingRun)
                            .where(ProcessingRun.source_version_id == version.id)
                            .order_by(ProcessingRun.created_at.desc())
                        )
                        return {
                            "source_id": existing.id,
                            "source_version_id": version.id,
                            "operation_id": run.id if run else None,
                            "status": "stored",
                        }
                    raise Error(
                        "source.external_ref_conflict",
                        "External reference requires an explicit new revision",
                        409,
                    )
            src = Source(
                external_ref=external_ref,
                namespace_id=body["namespace_id"],
                kind=body["kind"],
                title=body["title"],
                description=body.get("description", ""),
                tags=body.get("tags", []),
                meta=body.get("metadata", {}),
                external_uri=body.get("external_uri"),
            )
            self.checkpoint("source.before_insert")
            db.add(src)
            db.flush()
            self.checkpoint("source.after_insert")
            from wks_core.storage.stabilization import SourceMetadataRevision

            db.add(
                SourceMetadataRevision(
                    source_id=src.id,
                    revision=src.revision,
                    namespace_id=src.namespace_id,
                    value={
                        "title": src.title,
                        "description": src.description,
                        "tags": src.tags,
                        "metadata": src.meta,
                        "origin": "client",
                    },
                    generation=db.get(Namespace, src.namespace_id).index_generation,
                )
            )
            self.checkpoint("source.before_version")
            result = self.add_version(db, src, body, staged)
            self.checkpoint("source.after_version")
            grant = self.grant(db, p)
            if grant and grant.collection_id:
                db.add(
                    CollectionSource(
                        collection_id=grant.collection_id,
                        source_id=src.id,
                        namespace_id=src.namespace_id,
                    )
                )
            return result

        return self.mutate(p, key, {"op": "register", **body}, op)

    def new_version(self, p, key, sid, body):
        staged = self.prepare_text(p, key, body, sid)

        def op(db):
            src = self.source(db, p, sid, writing=True)
            if body.get("upload") is None and body.get("text") is None:
                raise Error(
                    "upload.content_invalid",
                    "New versions require text or upload; use capture for bookmarks",
                )
            db.refresh(src, with_for_update=True)
            src.revision += 1
            return self.add_version(db, src, body, staged)

        return self.mutate(p, key, {"op": "version", "source_id": sid, **body}, op)

    def upload_info(self, p, uid):
        with self.sessions() as db:
            u = db.get(Upload, uid)
            if not u:
                raise not_found()
            v = db.get(SourceVersion, u.source_version_id)
            db.info["wks_action"] = "upload:write"
            self.source(db, p, v.source_id, writing=True)
            if u.expires_at <= now():
                raise Error("upload.expired", "Upload session expired", 410)
            return {"size": u.expected_size, "sha": u.expected_sha}

    def receive(self, p, uid, path):
        checksum, size = file_digest(path), Path(path).stat().st_size
        with self.sessions() as db:
            db.info["wks_action"] = "upload:write"
            u = db.get(Upload, uid)
            if not u:
                raise not_found()
            v = db.get(SourceVersion, u.source_version_id)
            self.source(db, p, v.source_id, writing=True)
            if u.protocol == "offset-sha256-v1" and u.received_offset > 0:
                raise Error(
                    "upload.protocol_conflict", "Resume the offset protocol for this session", 409
                )
            if size != u.expected_size:
                raise Error("upload.incomplete", "Byte length does not match")
            if checksum != u.expected_sha:
                raise Error("upload.checksum_mismatch", "Checksum does not match")
            if u.status in {"received", "committed"}:
                return {"upload_id": uid, "status": u.status, "sha256": checksum}
            if u.status in {"expired", "aborted"} or u.expires_at <= now():
                raise Error("upload.expired", "Upload session expired", 410)
            namespace_id, declared = v.namespace_id, v.declared_media_type
        from wks_core.storage.content import detect_mime

        mime = detect_mime(Path(path), declared)
        staged = self.stage_blob(namespace_id, path, mime, "upload", uid)
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            db.info["wks_action"] = "upload:write"
            u = db.scalar(select(Upload).where(Upload.id == uid).with_for_update())
            v = db.get(SourceVersion, u.source_version_id)
            self.source(db, p, v.source_id, writing=True)
            if u.status in {"expired", "aborted"} or u.expires_at <= now():
                raise Error("upload.expired", "Upload session expired", 410)
            blob = self.attach_blob(db, staged, namespace_id)
            u.staged_blob_id, u.status, u.received_offset = blob.id, "received", size
            return {"upload_id": uid, "status": u.status, "sha256": checksum, "offset": size}

    def commit(self, p, key, uid):
        previous = self.receipt(p, key, {"op": "commit", "upload_id": uid})
        if previous is not None:
            return previous
        self.prepare_commit(p, uid)
        with self.sessions() as preflight_db:
            preflight_upload, _ = self.upload_record(preflight_db, p, uid, "upload:commit")
            preflight_blob = preflight_db.get(Blob, preflight_upload.staged_blob_id)
            self.validate_blob_provider(preflight_blob)
            verified_blob_id, storage_key = preflight_blob.id, preflight_blob.storage_key
        if not self.store.exists(storage_key):
            raise Error("operation.reconcile_required", "Stored object unavailable", 503, True)

        def op(db):
            u = db.scalar(select(Upload).where(Upload.id == uid).with_for_update())
            if not u:
                raise not_found()
            v = db.get(SourceVersion, u.source_version_id)
            src = self.source(db, p, v.source_id, writing=True)
            if u.status not in {"received", "committed"}:
                raise Error("upload.incomplete", "No validated upload available", 409)
            if not v.committed and u.expires_at <= now():
                raise Error("upload.expired", "Upload session expired", 410)
            blob = db.get(Blob, u.staged_blob_id)
            self.validate_blob_provider(blob)
            if blob.id != verified_blob_id:
                raise Error(
                    "operation.reconcile_required", "Upload changed during verification", 503, True
                )
            from wks_core.storage.stabilization import BlobOperation

            verified = db.scalar(
                select(BlobOperation.id).where(
                    BlobOperation.blob_id == blob.id, BlobOperation.state == "attached"
                )
            )
            if not verified:
                raise Error(
                    "operation.reconcile_required",
                    "Stored object has no verified journal",
                    503,
                    True,
                )
            if not v.committed:
                v.original_blob_id, v.checksum, v.byte_size, v.media_type = (
                    blob.id,
                    blob.sha256,
                    blob.size,
                    blob.mime,
                )
                v.committed, u.status = True, "committed"
                current = (
                    db.get(SourceVersion, src.current_version_id)
                    if src.current_version_id
                    else None
                )
                if not current or v.revision_no > current.revision_no:
                    src.current_version_id, src.state, src.availability = (
                        v.id,
                        "stored",
                        "metadata_only",
                    )
                self.event(db, src, "source.version_committed", source_version_id=v.id)
            run = self.enqueue(db, src, v, "initial")
            return {
                "source_id": src.id,
                "source_version_id": v.id,
                "status": "stored",
                "operation_id": run.id,
            }

        result = self.mutate(p, key, {"op": "commit", "upload_id": uid}, op)
        try:
            self.cleanup_upload_parts(uid)
        except Exception:
            # Logical commit is authoritative; cleanup is resumable maintenance.
            pass
        return result

    def enqueue(self, db, src, v, intent):
        config = {
            k: getattr(self.settings, k)
            for k in (
                "extraction_profile",
                "fallback_policy",
                "process_max_table_rows",
                "docling_artifacts_path",
                "ocr_enabled",
                "ocr_language",
                "asr_enabled",
                "asr_model_path",
                "video_enabled",
                "video_max_frames",
                "process_max_pages",
                "audio_max_seconds",
                "video_max_seconds",
                "enrichment_provider",
            )
        }
        if self.settings.fallback_policy == "native":
            config["extraction_profile"] = "native"
        elif self.settings.fallback_policy == "prefer_docling":
            config["extraction_profile"] = "docling"
        if self.settings.asr_enabled and self.settings.asr_model_path:
            model = Path(self.settings.asr_model_path) / "model.bin"
            if model.is_file():
                config["asr_model_sha256"] = file_digest(model)
        if self.settings.docling_artifacts_path:
            manifest = Path(self.settings.docling_artifacts_path) / "wks-model-manifest.json"
            if manifest.is_file():
                config["docling_artifact_manifest_sha256"] = file_digest(manifest)
        from wks_core.processing import processing_queue
        from wks_core.version import VERSION

        policy = db.get(Namespace, src.namespace_id).policy
        queue = processing_queue(v.media_type, config["extraction_profile"])
        if queue not in policy.get("allowed_profiles", ["native", "docling", "media"]):
            raise Error(
                "processing.profile_forbidden",
                "Processor profile is disabled by namespace policy",
                403,
            )
        config["process_max_pages"] = min(
            config["process_max_pages"], policy.get("max_pages", config["process_max_pages"])
        )
        cd, pd = digest(config), digest({"wks": VERSION, "processor_protocol": 2})
        existing = db.scalar(
            select(ProcessingRun).where(
                ProcessingRun.source_version_id == v.id,
                ProcessingRun.config_digest == cd,
                ProcessingRun.processor_digest == pd,
                ProcessingRun.intent_key == intent,
            )
        )
        if existing:
            return existing
        from wks_core.processing import processing_queue

        run = ProcessingRun(
            queue=processing_queue(v.media_type, config["extraction_profile"]),
            source_version_id=v.id,
            namespace_id=src.namespace_id,
            intent_key=intent,
            config=config,
            config_digest=cd,
            processor_digest=pd,
        )
        db.add(run)
        db.flush()
        self.event(db, src, "processing.queued", operation_id=run.id, source_version_id=v.id)
        return run

    def process(self, p, key, sid, body):
        def op(db):
            src = self.source(db, p, sid, writing=True)
            v = db.get(SourceVersion, body.get("source_version_id") or src.current_version_id)
            if not v or v.source_id != sid or not v.committed:
                raise not_found()
            run = self.enqueue(db, src, v, key)
            return {
                "operation_id": run.id,
                "source_version_id": v.id,
                "processing_state": run.status,
            }

        return self.mutate(p, key, {"op": "process", "source_id": sid, **body}, op)

    def source_json(self, db, src, p):
        g = self.grant(db, p, "read")
        query = select(SourceVersion).where(SourceVersion.source_id == src.id)
        if g and not g.namespace_id:
            query = query.where(SourceVersion.id.in_(g.source_version_ids))
        rows = db.scalars(query.order_by(SourceVersion.revision_no.desc()).limit(101)).all()
        versions = list(reversed(rows[:100]))
        current = next((v for v in versions if v.id == src.current_version_id), None)
        selected = current or (versions[-1] if versions else None)
        representation = (
            db.get(Representation, selected.current_representation_id)
            if selected and selected.current_representation_id
            else None
        )
        latest_run = (
            db.scalar(
                select(ProcessingRun)
                .where(ProcessingRun.source_version_id == selected.id)
                .order_by(ProcessingRun.created_at.desc())
                .limit(1)
            )
            if selected
            else None
        )
        # A grant pinned to an older version never exposes the current or other revisions.
        return {
            "id": src.id,
            "source_id": src.id,
            "namespace_id": src.namespace_id,
            "title": src.title,
            "description": src.description,
            "kind": src.kind,
            "tags": src.tags,
            "metadata": src.meta,
            "revision": src.revision,
            "external_ref": src.external_ref,
            "state": src.state,
            "processing": {
                "operation_id": latest_run.id,
                "state": latest_run.status,
                "error_code": latest_run.error_code,
            }
            if latest_run
            else None,
            "availability": representation.availability if representation else "metadata_only",
            "current_version_id": current.id if current else None,
            "source_ref": f"wks://sources/{src.id}/versions/{selected.id}" if selected else None,
            "versions": [
                {
                    "id": v.id,
                    "revision_number": v.revision_no,
                    "committed": v.committed,
                    "checksum": v.checksum,
                    "byte_size": v.byte_size,
                    "media_type": v.media_type,
                    "representation_id": v.current_representation_id,
                }
                for v in versions
            ],
            "versions_complete": len(rows) <= 100,
            "versions_path": f"/v1/sources/{src.id}/versions",
        }

    def source_get(self, p, sid):
        with self.sessions() as db:
            return self.source_json(db, self.source(db, p, sid), p)

    def cursor(self, p, context, cursor, position=None, watermark=None):
        signature_context = digest({"principal": asdict(p), "context": context})
        if position is not None:
            cid = new_id()
            with self.sessions.begin() as db:
                db.add(
                    CursorSnapshot(
                        id=cid,
                        context_hash=signature_context,
                        payload={"position": position, "watermark": watermark},
                        expires_at=now() + timedelta(seconds=self.settings.cursor_ttl_seconds),
                    )
                )
            sig = hmac.new(self.cursor_key.encode(), cid.encode(), "sha256").hexdigest()
            return cid + "." + sig
        if not cursor:
            return {"position": None, "watermark": None}
        try:
            cid, sig = cursor.split(".")
            expected = hmac.new(self.cursor_key.encode(), cid.encode(), "sha256").hexdigest()
            if not hmac.compare_digest(sig, expected):
                raise ValueError()
            with self.sessions() as db:
                snapshot = db.get(CursorSnapshot, cid)
                if (
                    not snapshot
                    or snapshot.context_hash != signature_context
                    or snapshot.expires_at <= now()
                ):
                    raise ValueError()
                return snapshot.payload
        except (ValueError, KeyError, TypeError):
            raise Error("search.cursor_invalid", "Invalid or expired cursor") from None

    def list_sources(self, p, filters=None, page_size=20, cursor=None):
        filters = filters or {}
        page_size = min(max(page_size, 1), self.settings.search_max_page_size)
        context = {"op": "catalog", "filters": filters, "page_size": page_size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            clauses, delegation = self.visible(db, p, "read")
            clauses[0] = Source.state.in_(["registered", "stored"])
            stmt = (
                select(Source)
                .join(SourceVersion, SourceVersion.source_id == Source.id)
                .where(*clauses)
                .distinct()
            )
            stmt = self.filters(stmt, filters)
            if filters.get("availability"):
                from sqlalchemy import case
                from sqlalchemy.orm import aliased

                version, representation = aliased(SourceVersion), aliased(Representation)
                selected = (
                    select(func.coalesce(representation.availability, "metadata_only"))
                    .select_from(version)
                    .outerjoin(
                        representation, representation.id == version.current_representation_id
                    )
                    .where(version.source_id == Source.id)
                )
                if delegation and not delegation.namespace_id:
                    selected = selected.where(version.id.in_(delegation.source_version_ids))
                selected = (
                    selected.order_by(
                        case((version.id == Source.current_version_id, 0), else_=1),
                        version.revision_no.desc(),
                    )
                    .limit(1)
                    .correlate(Source)
                    .scalar_subquery()
                )
                stmt = stmt.where(selected == filters["availability"])
            if state["position"]:
                stmt = stmt.where(Source.id > state["position"])
            rows = db.scalars(stmt.order_by(Source.id).limit(page_size + 1)).all()
            items = [self.source_json(db, src, p) for src in rows[:page_size]]
            nxt = (
                self.cursor(p, context, None, position=rows[page_size - 1].id)
                if len(rows) > page_size
                else None
            )
            return {"items": items, "next_cursor": nxt, "content_trust": "untrusted_source_data"}

    def filters(self, stmt, filters):
        if filters.get("source_ids"):
            stmt = stmt.where(Source.id.in_(filters["source_ids"]))
        if filters.get("namespace_id"):
            stmt = stmt.where(Source.namespace_id == filters["namespace_id"])
        if filters.get("collection_ids"):
            stmt = stmt.where(
                Source.id.in_(
                    select(CollectionSource.source_id).where(
                        CollectionSource.collection_id.in_(filters["collection_ids"])
                    )
                )
            )
        if filters.get("tags"):
            stmt = stmt.where(Source.tags.contains(filters["tags"]))
        if filters.get("metadata"):
            if set(filters["metadata"]) - {"category", "document_type", "declared_language"}:
                raise Error("search.filter_invalid", "Metadata filter is not indexed")
            stmt = stmt.where(Source.meta.contains(filters["metadata"]))
        if filters.get("media_types"):
            stmt = stmt.where(SourceVersion.media_type.in_(filters["media_types"]))
        if filters.get("source_version_ids"):
            stmt = stmt.where(SourceVersion.id.in_(filters["source_version_ids"]))
        if filters.get("created_after"):
            stmt = stmt.where(SourceVersion.created_at >= filters["created_after"])
        if filters.get("created_before"):
            stmt = stmt.where(SourceVersion.created_at <= filters["created_before"])
        return stmt

    def representation(self, db, p, rid, action="read"):
        r = db.get(Representation, rid)
        if not r or not r.published_at:
            raise not_found()
        v = db.get(SourceVersion, r.source_version_id)
        self.source(db, p, v.source_id, action=action, version_id=v.id)
        return r

    def outline(self, p, sid, rid=None, page_size=20, cursor=None, source_version_id=None):
        with self.sessions() as db:
            src = self.source(db, p, sid, version_id=source_version_id)
            vid = source_version_id or src.current_version_id
            g = self.grant(db, p, "read")
            if g and not g.namespace_id and vid not in g.source_version_ids:
                vid = db.scalar(
                    select(SourceVersion.id)
                    .where(
                        SourceVersion.source_id == sid, SourceVersion.id.in_(g.source_version_ids)
                    )
                    .order_by(SourceVersion.revision_no.desc())
                )
            v = db.get(SourceVersion, vid)
            rid = rid or (v.current_representation_id if v else None)
            r = db.get(Representation, rid) if rid else None
            if not r or db.get(SourceVersion, r.source_version_id).source_id != sid:
                raise not_found()
        return self.read(p, rid, page_size, cursor, outline=True)

    def asset_metadata(self, p, aid):
        with self.sessions() as db:
            a = db.get(Asset, aid)
            if not a:
                raise not_found()
            r = db.get(Representation, a.representation_id)
            v = db.get(SourceVersion, r.source_version_id)
            self.source(db, p, v.source_id, action="asset_read", version_id=v.id)
            b = db.get(Blob, a.blob_id)
            return {
                "id": a.id,
                "asset_ref": f"wks://assets/{a.id}",
                "representation_id": r.id,
                "source_version_id": v.id,
                "media_type": b.mime,
                "byte_size": b.size,
                "checksum": b.sha256,
                "locator": a.locator,
                "caption": a.caption,
                "kind": a.kind,
                "content_path": f"/v1/assets/{a.id}/content",
            }

    def validate_blob_provider(self, blob):
        location = self.store.location()
        if blob.provider != self.settings.storage_provider or any(
            blob.location.get(key) is not None and blob.location.get(key) != location.get(key)
            for key in ("bucket", "endpoint", "region", "store_id")
        ):
            raise Error(
                "storage.migration_required",
                "Blob provider differs; run an explicit verified migration",
                503,
            )

    def binary(self, p, aid=None, sid=None, vid=None):
        with self.sessions() as db:
            if aid:
                self.asset_metadata(p, aid)
                a = db.get(Asset, aid)
                blob = db.get(Blob, a.blob_id)
            else:
                self.source(db, p, sid, action="asset_read", version_id=vid)
                v = db.get(SourceVersion, vid)
                if not v or v.source_id != sid or not v.committed or not v.original_blob_id:
                    raise not_found()
                blob = db.get(Blob, v.original_blob_id)
            self.validate_blob_provider(blob)
            if not self.store.exists(blob.storage_key):
                raise Error("asset.media_not_ready", "Object unavailable", 503, True)
            return {
                "key": blob.storage_key,
                "mime": blob.mime,
                "size": blob.size,
                "sha256": blob.sha256,
                "source_id": v.source_id
                if not aid
                else db.get(
                    SourceVersion, db.get(Representation, a.representation_id).source_version_id
                ).source_id,
                "source_version_id": v.id
                if not aid
                else db.get(Representation, a.representation_id).source_version_id,
                "authorization_action": "asset_read",
            }

    def binary_gate(self, p, blob):
        with self.sessions() as db:
            self.source(
                db,
                p,
                blob["source_id"],
                action=blob["authorization_action"],
                version_id=blob["source_version_id"],
            )
            if blob.get("also_asset_read"):
                self.source(
                    db,
                    p,
                    blob["source_id"],
                    action="asset_read",
                    version_id=blob["source_version_id"],
                )

    def status(self, p, oid):
        with self.sessions() as db:
            run = db.get(ProcessingRun, oid)
            if not run:
                raise not_found()
            v = db.get(SourceVersion, run.source_version_id)
            src = self.source(db, p, v.source_id, action="operation:read", version_id=v.id)
            selected_representation = (
                db.get(Representation, v.current_representation_id)
                if v.current_representation_id
                else None
            )
            return {
                "operation_id": run.id,
                "source_id": src.id,
                "source_version_id": v.id,
                "processing_state": run.status,
                "current_stage": "extract_publish" if run.status == "running" else run.status,
                "availability": selected_representation.availability
                if selected_representation
                else "metadata_only",
                "coverage": run.coverage,
                "published_representation_id": run.published_representation_id,
                "attempt": run.attempt,
                "next_retry_at": run.next_retry_at.isoformat() if run.next_retry_at else None,
                "updated_at": run.updated_at.isoformat(),
                "error_code": run.error_code,
            }

    def grant_create(self, p, key, body):
        from wks_core.domain.policies import validate_delegation

        try:
            validate_delegation(body)
        except ValueError as exc:
            raise Error("auth.scope_invalid", "Invalid delegation policy") from exc

        def op(db):
            caller = db.get(Client, body["caller_binding"])
            if not caller or caller.role != "executor" or caller.audience != body["audience"]:
                raise Error("auth.scope_mismatch", "Unknown caller or audience")
            namespace_id = body.get("namespace_id")
            collection_id = body.get("collection_id")
            if namespace_id:
                self.write(db, p, namespace_id)
                if collection_id:
                    collection = db.get(Collection, collection_id)
                    if not collection or collection.namespace_id != namespace_id:
                        raise not_found()
            for vid in body.get("source_version_ids", []):
                v = db.get(SourceVersion, vid)
                if not v or not v.committed:
                    raise not_found()
                self.source(db, p, v.source_id, writing=True)
            g = Grant(
                issuer=p.client_id,
                caller_binding=caller.id,
                audience=body["audience"],
                source_version_ids=body.get("source_version_ids", []),
                namespace_id=namespace_id,
                collection_id=collection_id,
                allowed_actions=body["allowed_actions"],
                purpose=body["purpose"],
                expires_at=now() + timedelta(seconds=body["ttl_seconds"]),
                scope_hash=digest(body),
            )
            db.add(g)
            db.flush()
            return {"id": g.id, "expires_at": g.expires_at.isoformat(), "scope_hash": g.scope_hash}

        return self.mutate(p, key, {"op": "grant", **body}, op)

    def grant_revoke(self, p, key, gid):
        previous = self.receipt(p, key, {"op": "grant_revoke", "id": gid})
        if previous:
            return previous
        if not key or len(key) > 180:
            raise Error("operation.idempotency_required", "Idempotency-Key required (max 180)")
        with self.sessions() as check:
            self.grant(check, p)
            record = check.get(Grant, gid)
            if not record or record.issuer != p.client_id or p.grant_id:
                raise not_found()

        def op(db):
            g = db.get(Grant, gid)
            if not g or g.issuer != p.client_id:
                raise not_found()
            self.privacy_ledger.append("grant_revoked", gid)
            g.revoked_at = now()
            return {"id": gid, "revoked": True}

        return self.mutate(p, key, {"op": "grant_revoke", "id": gid}, op)

    def revoke(self, p, key, sid, purge=False, confirmation_token=None):
        if not key or len(key) > 180:
            raise Error("operation.idempotency_required", "Idempotency-Key required (max 180)")
        command = {"op": "delete" if purge else "revoke", "sid": sid}
        previous = self.receipt(p, key, command)
        if previous:
            if purge:
                self.purge_source(sid)
            return previous
        with self.sessions() as check:
            check.info["wks_action"] = "source:delete" if purge else "source:revoke"
            source = check.get(Source, sid)
            if not source:
                raise not_found()
            self.write(check, p, source.namespace_id)
            delegation = self.grant(check, p)
            if (
                delegation
                and delegation.collection_id
                and not check.get(CollectionSource, (delegation.collection_id, sid))
            ):
                raise not_found()
            if delegation or confirmation_token:
                self.confirm_preflight(
                    check, p, source, "delete" if purge else "revoke", confirmation_token
                )

        def op(db):
            src = db.get(Source, sid, with_for_update=True)
            if not src:
                raise not_found()
            self.write(db, p, src.namespace_id)
            delegation = self.grant(db, p)
            if (
                delegation
                and delegation.collection_id
                and not db.get(CollectionSource, (delegation.collection_id, sid))
            ):
                raise not_found()
            if delegation or confirmation_token:
                self.confirm_preflight(
                    db, p, src, "delete" if purge else "revoke", confirmation_token
                )
            self.privacy_ledger.append("source_deleted" if purge else "source_revoked", sid)
            src.state = "revoked"
            src.deleted_at = now() if purge else None
            db.execute(
                text(
                    "UPDATE processing_runs SET status='cancelled', lease_token=NULL WHERE source_version_id IN (SELECT id FROM source_versions WHERE source_id=:sid) AND status IN ('queued','running','retry_wait')"
                ),
                {"sid": sid},
            )
            self.event(db, src, "source.deletion_requested" if purge else "source.revoked")
            db.add(
                Audit(
                    actor=p.client_id,
                    action="delete" if purge else "revoke",
                    resource=sid,
                    outcome="allowed",
                )
            )
            return {"source_id": sid, "state": "deleted" if purge else src.state}

        result = self.mutate(p, key, command, op)
        if purge:
            self.purge_source(sid)
        return result

    def purge_source(self, sid):
        # Logical removal commits before IO; snapshot pins retain recoverable bytes.
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            src = db.get(Source, sid)
            if not src or src.deleted_at is None:
                return
            already_deleted = src.state == "deleted"
            versions = db.scalars(select(SourceVersion).where(SourceVersion.source_id == sid)).all()
            vids = [v.id for v in versions]
            reps = db.scalars(
                select(Representation).where(Representation.source_version_id.in_(vids))
            ).all()
            rids = [r.id for r in reps]
            assets = db.scalars(select(Asset).where(Asset.representation_id.in_(rids))).all()
            for v in versions:
                v.current_representation_id = None
            db.flush()
            from wks_core.storage.stabilization import ExportArtifact

            export_blobs = set(
                db.scalars(
                    select(ExportArtifact.blob_id).where(
                        ExportArtifact.representation_id.in_(rids),
                        ExportArtifact.blob_id.is_not(None),
                    )
                ).all()
            )
            blob_ids = (
                export_blobs
                | {a.blob_id for a in assets}
                | {v.original_blob_id for v in versions if v.original_blob_id}
            )
            # Include validated but uncommitted uploads when deleting a source.
            from wks_core.storage.database import Upload

            uploads = db.scalars(select(Upload).where(Upload.source_version_id.in_(vids))).all()
            blob_ids |= {u.staged_blob_id for u in uploads if u.staged_blob_id}
            from wks_core.storage.retirement import retire

            retire(db, sid, blob_ids)
            db.execute(delete(Segment).where(Segment.representation_id.in_(rids)))
            db.execute(delete(Asset).where(Asset.representation_id.in_(rids)))
            db.execute(delete(Representation).where(Representation.id.in_(rids)))
            from wks_core.storage.stabilization import SourceMetadataRevision

            db.execute(
                delete(SourceMetadataRevision).where(SourceMetadataRevision.source_id == sid)
            )
            db.execute(
                text(
                    "UPDATE idempotency_receipts SET result=jsonb_build_object('source_id',:sid,'state','deleted','write_outcome','confirmed') WHERE result->>'id'=:sid OR result->>'source_id'=:sid"
                ),
                {"sid": sid},
            )
            src.external_ref = None
            src.title, src.description, src.meta, src.tags, src.external_uri = (
                "[deleted]",
                "",
                {},
                [],
                None,
            )
            for v in versions:
                v.original_filename, v.external_uri = None, None
            src.state = "deleted" if already_deleted else "revoked"
        from wks_core.storage.retirement import reconcile_source

        if reconcile_source(self, sid):
            with self.sessions.begin() as db:
                db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
                src = db.get(Source, sid, with_for_update=True)
                if src and src.deleted_at and src.state != "deleted":
                    src.state = "deleted"
                    self.event(db, src, "source.deleted")

    def validate_ref(self, p, ref):
        """Citation verification is authoritative, never a syntax-only acceptance."""
        from urllib.parse import urlparse

        u = urlparse(ref)
        parts = u.path.strip("/").split("/")
        if u.scheme != "wks":
            raise not_found()
        if u.netloc == "representations" and len(parts) == 1:
            return self.representation_metadata(p, parts[0]) | {"verified": True}
        if u.netloc == "assets" and len(parts) == 1:
            return self.asset_metadata(p, parts[0])
        if u.netloc == "sources" and len(parts) == 3 and parts[1] == "versions":
            with self.sessions() as db:
                self.source(db, p, parts[0], version_id=parts[2])
                v = db.get(SourceVersion, parts[2])
                if not v or not v.committed or v.source_id != parts[0]:
                    raise not_found()
                return {
                    "ref": ref,
                    "verified": True,
                    "source_version_id": v.id,
                    "checksum": v.checksum,
                }
        if u.netloc == "representations" and len(parts) == 3 and parts[1] in {"segments", "blocks"}:
            with self.sessions() as db:
                r = self.representation(db, p, parts[0])
                if parts[1] == "segments":
                    seg = db.get(Segment, parts[2])
                    if not seg or seg.representation_id != r.id:
                        raise not_found()
                    locator = seg.locator
                else:
                    from wks_core.storage.stabilization import RepresentationBlock

                    b = db.scalar(
                        select(RepresentationBlock.value).where(
                            RepresentationBlock.representation_id == r.id,
                            RepresentationBlock.block_id == parts[2],
                        )
                    )
                    if not b:
                        raise not_found()
                    locator = b["locator"]
                return {
                    "ref": ref,
                    "verified": True,
                    "source_version_id": r.source_version_id,
                    "representation_id": r.id,
                    "locator": locator,
                }
        raise not_found()
