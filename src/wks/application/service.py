import hashlib
import hmac
import json
import os
import secrets
import unicodedata
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import (
    BigInteger,
    Double,
    and_,
    cast,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from wks.domain.models import Error, Principal, new_id, not_found
from wks.infrastructure.database import (
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
    Receipt,
    Representation,
    Segment,
    Source,
    SourceVersion,
    Upload,
    now,
)


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def markdown(blocks):
    parts = []
    for b in blocks:
        value = b["text"]
        if b["type"] == "heading":
            value = "#" * min(b.get("data", {}).get("level", 2), 6) + " " + value
        if b["type"] == "picture_ref":
            value += "\n" + "\n".join(f"![source asset]({ref})" for ref in b["asset_refs"])
        parts.append(value)
    return "\n\n".join(parts)


class Service:
    """Shared transaction and policy boundary used by HTTP, MCP and workers."""

    def __init__(self, settings, sessions, store):
        self.settings, self.sessions, self.store = settings, sessions, store
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

    def authenticate(self, token, grant_id=None):
        if not token:
            raise Error("auth.unauthenticated", "Authentication required", 401)
        with self.sessions() as db:
            caller = db.scalar(
                select(Client).where(
                    Client.token_hash == hashlib.sha256(token.encode()).hexdigest(), Client.active
                )
            )
            if not caller:
                raise Error("auth.unauthenticated", "Authentication required", 401)
            p = Principal(caller.id, caller.role, caller.audience, grant_id)
            self.grant(db, p)
            return p

    def grant(self, db, p, action=None):
        caller = db.get(Client, p.client_id)
        if (
            not caller
            or not caller.active
            or caller.role != p.role
            or caller.audience != p.audience
        ):
            raise Error("auth.forbidden", "Caller unavailable", 403)
        if p.role == "executor" and not p.grant_id:
            raise Error("auth.forbidden", "Delegation required", 403)
        if not p.grant_id:
            return None
        g = db.get(Grant, p.grant_id)
        if not g or g.caller_binding != p.client_id or g.audience != p.audience or g.revoked_at:
            raise Error("auth.forbidden", "Delegation unavailable", 403)
        if g.expires_at <= now():
            raise Error("auth.grant_expired", "Delegation expired", 403)
        if action and action not in g.allowed_actions:
            raise Error("auth.forbidden", "Action unavailable", 403)
        return g

    def write(self, db, p, namespace_id):
        self.grant(db, p)
        if p.grant_id or p.role != "client":
            raise Error("auth.forbidden", "Write unavailable", 403)
        ns = db.get(Namespace, namespace_id)
        if not ns or ns.client_id != p.client_id:
            raise not_found()
        return ns

    def source(self, db, p, source_id, action="read", version_id=None, writing=False):
        src = db.get(Source, source_id)
        if not src or src.state in {"revoked", "deleted"}:
            raise not_found()
        if writing:
            self.write(db, p, src.namespace_id)
        else:
            g = self.grant(db, p, action)
            if g:
                candidates = [version_id] if version_id else g.source_version_ids
                if version_id and version_id not in g.source_version_ids:
                    raise not_found()
                if not db.scalar(
                    select(
                        exists().where(
                            SourceVersion.source_id == src.id, SourceVersion.id.in_(candidates)
                        )
                    )
                ):
                    raise not_found()
            elif db.get(Namespace, src.namespace_id).client_id != p.client_id:
                raise not_found()
        return src

    def visible(self, db, p, action):
        g = self.grant(db, p, action)
        clauses = [Source.state == "stored", Source.deleted_at.is_(None)]
        if g:
            clauses.append(SourceVersion.id.in_(g.source_version_ids))
        else:
            clauses.append(
                Source.namespace_id.in_(
                    select(Namespace.id).where(Namespace.client_id == p.client_id)
                )
            )
        return clauses, g

    def mutate(self, p, key, command, callback):
        if not key or len(key) > 180:
            raise Error("operation.idempotency_required", "Idempotency-Key required (max 180)")
        if p.role != "client" or p.grant_id:
            raise Error("auth.forbidden", "Write unavailable", 403)
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            # Serializes re-delivery before reading receipt; also bounds tenant quota races.
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:client, 0))"),
                {"client": p.client_id},
            )
            self.grant(db, p)
            payload_hash = digest(command)
            receipt = db.get(Receipt, (p.client_id, key))
            if receipt:
                if receipt.payload_hash != payload_hash:
                    raise Error(
                        "operation.idempotency_conflict",
                        "Key already used for a different payload",
                        409,
                    )
                return receipt.result
            result = callback(db)
            db.add(
                Receipt(
                    client_id=p.client_id,
                    operation_key=key,
                    payload_hash=payload_hash,
                    result=result,
                )
            )
            return result

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
            return {"id": ns.id, "title": ns.title, "external_ref": ns.external_ref}

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

    def save_blob(self, db, namespace_id, path, mime):
        blob = Blob(
            namespace_id=namespace_id,
            storage_key=f"{namespace_id}/{new_id()}",
            sha256=file_digest(path),
            size=Path(path).stat().st_size,
            mime=mime,
            provider=self.settings.storage_provider,
        )
        self.store.put_file(blob.storage_key, Path(path))
        db.add(blob)
        db.flush()
        return blob

    def add_version(self, db, src, body):
        revision = (
            db.scalar(
                select(func.max(SourceVersion.revision_no)).where(SourceVersion.source_id == src.id)
            )
            or 0
        ) + 1
        upload = body.get("upload")
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
                self.quota(db, src.namespace_id, len(raw))
                with NamedTemporaryFile() as f:
                    f.write(raw)
                    f.flush()
                    blob = self.save_blob(db, src.namespace_id, f.name, "text/plain")
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
             UNION SELECT u.staged_blob_id FROM upload_sessions u JOIN source_versions v ON v.id=u.source_version_id
               JOIN sources s ON s.id=v.source_id WHERE s.state<>'deleted' AND u.staged_blob_id IS NOT NULL
          )"""),
            {"ns": namespace_id},
        )
        if used + size > self.settings.namespace_max_bytes:
            raise Error("upload.quota_exceeded", "Namespace storage limit reached", 413)

    def register(self, p, key, body):
        def op(db):
            self.write(db, p, body["namespace_id"])
            src = Source(
                namespace_id=body["namespace_id"],
                kind=body["kind"],
                title=body["title"],
                description=body.get("description", ""),
                tags=body.get("tags", []),
                meta=body.get("metadata", {}),
                external_uri=body.get("external_uri"),
            )
            db.add(src)
            db.flush()
            return self.add_version(db, src, body)

        return self.mutate(p, key, {"op": "register", **body}, op)

    def new_version(self, p, key, sid, body):
        def op(db):
            src = self.source(db, p, sid, writing=True)
            if body.get("upload") is None and body.get("text") is None:
                raise Error(
                    "upload.content_invalid",
                    "New versions require text or upload; use capture for bookmarks",
                )
            db.refresh(src, with_for_update=True)
            return self.add_version(db, src, body)

        return self.mutate(p, key, {"op": "version", "source_id": sid, **body}, op)

    def upload_info(self, p, uid):
        with self.sessions() as db:
            u = db.get(Upload, uid)
            if not u:
                raise not_found()
            v = db.get(SourceVersion, u.source_version_id)
            self.source(db, p, v.source_id, writing=True)
            if u.expires_at <= now():
                raise Error("upload.expired", "Upload session expired", 410)
            return {"size": u.expected_size, "sha": u.expected_sha}

    def receive(self, p, uid, path):
        checksum, size = file_digest(path), Path(path).stat().st_size
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            u = db.scalar(select(Upload).where(Upload.id == uid).with_for_update())
            if not u:
                raise not_found()
            v = db.get(SourceVersion, u.source_version_id)
            self.source(db, p, v.source_id, writing=True)
            if size != u.expected_size:
                raise Error("upload.incomplete", "Byte length does not match")
            if checksum != u.expected_sha:
                raise Error("upload.checksum_mismatch", "Checksum does not match")
            if u.status in {"received", "committed"}:
                return {"upload_id": uid, "status": u.status, "sha256": checksum}
            if u.expires_at <= now():
                raise Error("upload.expired", "Upload session expired", 410)
            self.quota(db, v.namespace_id, size)
            from wks.infrastructure.processing import detect_mime

            mime = detect_mime(Path(path), v.declared_media_type)
            blob = self.save_blob(db, v.namespace_id, path, mime)
            u.staged_blob_id, u.status = blob.id, "received"
            return {"upload_id": uid, "status": "received", "sha256": checksum}

    def commit(self, p, key, uid):
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
            if not self.store.exists(blob.storage_key):
                raise Error("operation.reconcile_required", "Stored object unavailable", 503, True)
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as tmp:
                actual = Path(tmp) / "original"
                self.store.materialize(blob.storage_key, actual)
                if (
                    actual.stat().st_size != u.expected_size
                    or file_digest(actual) != u.expected_sha
                ):
                    raise Error("upload.checksum_mismatch", "Stored object integrity failed")
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

        return self.mutate(p, key, {"op": "commit", "upload_id": uid}, op)

    def enqueue(self, db, src, v, intent):
        config = {
            k: getattr(self.settings, k)
            for k in (
                "extraction_profile",
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
        if self.settings.asr_enabled and self.settings.asr_model_path:
            model = Path(self.settings.asr_model_path) / "model.bin"
            if model.is_file():
                config["asr_model_sha256"] = file_digest(model)
        if self.settings.docling_artifacts_path:
            manifest = Path(self.settings.docling_artifacts_path) / "wks-model-manifest.json"
            if manifest.is_file():
                config["docling_artifact_manifest_sha256"] = file_digest(manifest)
        cd, pd = digest(config), digest({"wks": "0.1.0", "processor_protocol": 1})
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
        run = ProcessingRun(
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
        versions = db.scalars(
            select(SourceVersion)
            .where(SourceVersion.source_id == src.id)
            .order_by(SourceVersion.revision_no)
        ).all()
        if g:
            versions = [v for v in versions if v.id in g.source_version_ids]
        current = next((v for v in versions if v.id == src.current_version_id), None)
        selected = current or (versions[-1] if versions else None)
        representation = (
            db.get(Representation, selected.current_representation_id)
            if selected and selected.current_representation_id
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
            "state": src.state,
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
            clauses, _ = self.visible(db, p, "read")
            clauses[0] = Source.state.in_(["registered", "stored"])
            stmt = (
                select(Source)
                .join(SourceVersion, SourceVersion.source_id == Source.id)
                .where(*clauses)
                .distinct()
            )
            stmt = self.filters(stmt, filters)
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
        if filters.get("media_types"):
            stmt = stmt.where(SourceVersion.media_type.in_(filters["media_types"]))
        if filters.get("source_version_ids"):
            stmt = stmt.where(SourceVersion.id.in_(filters["source_version_ids"]))
        if filters.get("created_after"):
            stmt = stmt.where(SourceVersion.created_at >= filters["created_after"])
        if filters.get("created_before"):
            stmt = stmt.where(SourceVersion.created_at <= filters["created_before"])
        return stmt

    def search(self, p, body):
        query = unicodedata.normalize("NFC", body["query"]).strip()
        if (
            not query
            or len(query) > 1000
            or body.get("mode", "lexical") not in {"lexical", "phrase"}
        ):
            raise Error(
                "search.query_invalid", "Use a nonempty lexical query (max 1000 characters)"
            )
        filters = body.get("filters") or {}
        size = min(body.get("page_size", 10), self.settings.search_max_page_size)
        context = {"op": "search", **{k: v for k, v in body.items() if k != "cursor"}}
        state = self.cursor(p, context, body.get("cursor"))
        with self.sessions() as db:
            clauses, g = self.visible(db, p, "search")
            language = {"en": "wks_english", "simple": "wks_simple"}.get(
                body.get("language"), "wks_portuguese"
            )
            f = func.phraseto_tsquery if body.get("mode") == "phrase" else func.websearch_to_tsquery
            tsq = f(text(f"'{language}'::regconfig"), query)
            if db.scalar(select(func.numnode(tsq))) == 0:
                return {
                    "query": query,
                    "items": [],
                    "next_cursor": None,
                    "warning": "query_not_indexable",
                    "index_kind": "lexical_postgresql_fts",
                }
            # PostgreSQL ranks are float4. Promote before serialization/comparison so
            # keyset equality uses exactly the same value across JSON round trips.
            rank = cast(func.ts_rank_cd(Segment.search_vector, tsq), Double).label("rank")
            stmt = (
                select(Segment, Source, SourceVersion, rank)
                .join(Representation, Segment.representation_id == Representation.id)
                .join(SourceVersion, Representation.source_version_id == SourceVersion.id)
                .join(Source, SourceVersion.source_id == Source.id)
                .where(
                    *clauses,
                    Segment.namespace_id == Source.namespace_id,
                    Segment.search_vector.op("@@")(tsq),
                    Representation.published_at.is_not(None),
                )
            )
            stmt = self.filters(stmt, filters)
            if filters.get("processor"):
                stmt = stmt.where(Representation.producer["name"].astext == filters["processor"])
            if filters.get("availability"):
                stmt = stmt.where(Representation.availability == filters["availability"])
            if filters.get("language"):
                stmt = stmt.where(Segment.locale == filters["language"])
            scope_watermark = (
                select(Source.namespace_id, func.max(Representation.index_generation))
                .join(SourceVersion, Representation.source_version_id == SourceVersion.id)
                .join(Source, SourceVersion.source_id == Source.id)
                .where(*clauses)
                .group_by(Source.namespace_id)
            )
            watermark = (
                state["watermark"]
                if state["watermark"] is not None
                else dict(db.execute(scope_watermark).all())
            )
            per_namespace_limit = cast(
                literal(watermark, type_=JSONB)[Representation.namespace_id].astext, BigInteger
            )
            stmt = stmt.where(Representation.index_generation <= per_namespace_limit)
            if filters.get("representation", "current_published") == "current_published":
                from sqlalchemy.orm import aliased

                newer = aliased(Representation)
                stmt = stmt.where(
                    ~exists(
                        select(newer.id).where(
                            newer.source_version_id == SourceVersion.id,
                            newer.index_generation <= per_namespace_limit,
                            newer.index_generation > Representation.index_generation,
                        )
                    )
                )
                if not g:
                    nv, nr = aliased(SourceVersion), aliased(Representation)
                    stmt = stmt.where(
                        ~exists(
                            select(nv.id)
                            .join(nr, nr.source_version_id == nv.id)
                            .where(
                                nv.source_id == Source.id,
                                nv.revision_no > SourceVersion.revision_no,
                                nr.index_generation <= per_namespace_limit,
                            )
                        )
                    )
            if state["position"]:
                last_rank, last_id = state["position"]
                stmt = stmt.where(
                    or_(rank < last_rank, and_(rank == last_rank, Segment.id > last_id))
                )
            rows = db.execute(stmt.order_by(rank.desc(), Segment.id).limit(size + 1)).all()
            items, chars = [], 0
            for seg, src, ver, score in rows[:size]:
                snippet = seg.text[:600]
                if items and chars + len(snippet) > self.settings.search_max_response_chars:
                    break
                chars += len(snippet)
                items.append(
                    {
                        "source_id": src.id,
                        "source_version_id": ver.id,
                        "representation_id": seg.representation_id,
                        "segment_id": seg.id,
                        "segment_ref": f"wks://representations/{seg.representation_id}/segments/{seg.id}",
                        "title": src.title,
                        "snippet": snippet,
                        "rank": float(score),
                        "locator": seg.locator,
                        "origin_kind": seg.origin_kind,
                        "asset_refs": seg.asset_refs,
                    }
                )
            nxt = None
            if items and len(rows) > len(items):
                last = rows[len(items) - 1]
                nxt = self.cursor(
                    p, context, None, position=[float(last[3]), last[0].id], watermark=watermark
                )
            return {
                "query": query,
                "scope": "resolved_server_side",
                "index_kind": "lexical_postgresql_fts",
                "items": items,
                "next_cursor": nxt,
                "index_generations": watermark,
                "content_trust": "untrusted_source_data",
            }

    def representation(self, db, p, rid):
        r = db.get(Representation, rid)
        if not r or not r.published_at:
            raise not_found()
        v = db.get(SourceVersion, r.source_version_id)
        self.source(db, p, v.source_id, version_id=v.id)
        return r

    def read(self, p, rid, page_size=20, cursor=None, outline=False):
        size = min(max(page_size, 1), self.settings.search_max_page_size)
        context = {"op": "outline" if outline else "read", "rid": rid, "page_size": size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            r = self.representation(db, p, rid)
            blocks = r.structure
            if outline:
                blocks = [
                    {k: b[k] for k in ("id", "type", "locator", "parent_id", "asset_refs")}
                    | {"text": b["text"][:120]}
                    for b in blocks
                ]
            offset, count, output = state["position"] or 0, 0, []
            for b in blocks[offset : offset + size]:
                length = len(json.dumps(b, ensure_ascii=False))
                if length > self.settings.search_max_response_chars:
                    # Preserve the full canonical block; expose a bounded preview and explicit limit.
                    b = {
                        **b,
                        "text": b["text"][:2000],
                        "data": {
                            "preview_only": True,
                            "full_block_ref": f"wks://representations/{rid}/blocks/{b['id']}",
                        },
                    }
                    length = len(json.dumps(b, ensure_ascii=False))
                if output and count + length > self.settings.search_max_response_chars:
                    break
                output.append(b)
                count += length
            position = offset + len(output)
            nxt = (
                self.cursor(p, context, None, position=position) if position < len(blocks) else None
            )
            db.add(
                Audit(
                    actor=p.client_id,
                    action="outline" if outline else "read",
                    resource=rid,
                    outcome="allowed",
                )
            )
            db.commit()
            return {
                "representation_id": rid,
                "source_version_id": r.source_version_id,
                "index_generation": r.index_generation,
                "blocks": output,
                "markdown": markdown(output),
                "coverage": r.coverage,
                "warnings": r.warnings,
                "next_cursor": nxt,
                "content_trust": "untrusted_source_data",
            }

    def outline(self, p, sid, rid=None, page_size=20, cursor=None, source_version_id=None):
        with self.sessions() as db:
            src = self.source(db, p, sid, version_id=source_version_id)
            vid = source_version_id or src.current_version_id
            g = self.grant(db, p, "read")
            if g and vid not in g.source_version_ids:
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
            if not self.store.exists(blob.storage_key):
                raise Error("asset.media_not_ready", "Object unavailable", 503, True)
            return {
                "key": blob.storage_key,
                "mime": blob.mime,
                "size": blob.size,
                "sha256": blob.sha256,
            }

    def status(self, p, oid):
        with self.sessions() as db:
            run = db.get(ProcessingRun, oid)
            if not run:
                raise not_found()
            v = db.get(SourceVersion, run.source_version_id)
            src = self.source(db, p, v.source_id, version_id=v.id)
            return {
                "operation_id": run.id,
                "source_id": src.id,
                "source_version_id": v.id,
                "processing_state": run.status,
                "current_stage": "extract_publish" if run.status == "running" else run.status,
                "availability": src.availability,
                "coverage": run.coverage,
                "published_representation_id": run.published_representation_id,
                "attempt": run.attempt,
                "next_retry_at": run.next_retry_at.isoformat() if run.next_retry_at else None,
                "updated_at": run.updated_at.isoformat(),
                "error_code": run.error_code,
            }

    def grant_create(self, p, key, body):
        def op(db):
            caller = db.get(Client, body["caller_binding"])
            if not caller or caller.role != "executor" or caller.audience != body["audience"]:
                raise Error("auth.scope_mismatch", "Unknown caller or audience")
            for vid in body["source_version_ids"]:
                v = db.get(SourceVersion, vid)
                if not v or not v.committed:
                    raise not_found()
                self.source(db, p, v.source_id, writing=True)
            g = Grant(
                issuer=p.client_id,
                caller_binding=caller.id,
                audience=body["audience"],
                source_version_ids=body["source_version_ids"],
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
        def op(db):
            g = db.get(Grant, gid)
            if not g or g.issuer != p.client_id:
                raise not_found()
            g.revoked_at = now()
            return {"id": gid, "revoked": True}

        return self.mutate(p, key, {"op": "grant_revoke", "id": gid}, op)

    def revoke(self, p, key, sid, purge=False):
        def op(db):
            src = db.get(Source, sid)
            if not src:
                raise not_found()
            self.write(db, p, src.namespace_id)
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

        result = self.mutate(p, key, {"op": "delete" if purge else "revoke", "sid": sid}, op)
        if purge:
            self.purge_source(sid)
        return result

    def purge_source(self, sid):
        # Tombstones survive, bytes and derived text do not. Retriable after any interruption.
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
            blob_ids = {a.blob_id for a in assets} | {
                v.original_blob_id for v in versions if v.original_blob_id
            }
            # Include validated but uncommitted uploads when deleting a source.
            from wks.infrastructure.database import Upload

            uploads = db.scalars(select(Upload).where(Upload.source_version_id.in_(vids))).all()
            blob_ids |= {u.staged_blob_id for u in uploads if u.staged_blob_id}
            for bid in blob_ids:
                blob = db.get(Blob, bid)
                if blob:
                    self.store.delete(blob.storage_key)
            db.execute(delete(Segment).where(Segment.representation_id.in_(rids)))
            db.execute(delete(Asset).where(Asset.representation_id.in_(rids)))
            db.execute(delete(Representation).where(Representation.id.in_(rids)))
            src.title, src.description, src.meta, src.tags, src.external_uri = (
                "[deleted]",
                "",
                {},
                [],
                None,
            )
            for v in versions:
                v.original_filename, v.external_uri = None, None
            src.state = "deleted"
            if not already_deleted:
                self.event(db, src, "source.deleted")

    def validate_ref(self, p, ref):
        """Citation verification is authoritative, never a syntax-only acceptance."""
        from urllib.parse import urlparse

        u = urlparse(ref)
        parts = u.path.strip("/").split("/")
        if u.scheme != "wks":
            raise not_found()
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
                    b = next((b for b in r.structure if b["id"] == parts[2]), None)
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
