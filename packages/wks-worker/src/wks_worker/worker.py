import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import Text, and_, cast, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from wks_core.application.utilities import digest, file_digest, markdown
from wks_core.domain.models import Block, Error, ExtractedAsset, ExtractedRepresentation, new_id
from wks_core.storage.database import (
    Asset,
    Blob,
    Namespace,
    ProcessingRun,
    Representation,
    Segment,
    Source,
    SourceVersion,
    Stage,
    now,
)


def heartbeat_interval(lease_seconds):
    return min(15.0, max(0.2, lease_seconds / 3))


class Worker:
    def __init__(self, service, owner=None, queues=None):
        from wks_worker.capabilities import available_queues, validate_queues

        self.service, self.owner = service, owner or new_id()
        self.queues = validate_queues(queues or available_queues())
        self.last_announce = 0
        self.announce()

    def announce(self):
        from importlib.metadata import version
        from importlib.util import find_spec
        from pathlib import Path
        from shutil import which

        from wks_core.storage.stabilization import WorkerHeartbeat

        if time.monotonic() - self.last_announce < 30:
            return
        from wks_worker.sandbox import availability

        isolation = availability()
        settings = self.service.settings
        decoder_ready = isolation["network"] and (
            settings.decoder_sandbox != "strict" or isolation["filesystem"]
        )
        asr_ready = bool(
            settings.asr_enabled
            and settings.asr_model_path
            and (Path(settings.asr_model_path) / "model.bin").is_file()
            and find_spec("faster_whisper")
        )
        video_ready = bool(settings.video_enabled and which("ffmpeg"))
        docling_ready = bool(
            settings.docling_artifacts_path
            and (Path(settings.docling_artifacts_path) / "wks-model-manifest.json").is_file()
            and find_spec("docling")
        )
        ready_queues = [
            queue
            for queue in self.queues
            if decoder_ready
            and (
                queue == "native"
                or queue == "media"
                and (asr_ready or video_ready)
                or queue == "docling"
                and docling_ready
            )
        ]
        with self.service.sessions.begin() as db:
            row = db.get(WorkerHeartbeat, self.owner)
            if not row:
                row = WorkerHeartbeat(owner=self.owner)
                db.add(row)
            row.queues = list(self.queues)
            row.capabilities = {
                "ready": bool(ready_queues),
                "ready_queues": ready_queues,
                "sandbox": isolation,
                "runtime_version": version("wks-worker"),
                "profiles": {queue: version("wks-worker-" + queue) for queue in self.queues},
                "ocr_enabled": settings.ocr_enabled and bool(which("tesseract")),
                "asr_enabled": asr_ready,
                "docling_enabled": docling_ready,
                "video_enabled": video_ready,
            }
            row.updated_at = now()
        self.last_announce = time.monotonic()

    def claim(self):
        from sqlalchemy.orm import aliased

        eligible = or_(
            ProcessingRun.status == "queued",
            and_(ProcessingRun.status == "retry_wait", ProcessingRun.next_retry_at <= now()),
            and_(ProcessingRun.status == "running", ProcessingRun.deadline < now()),
        )
        peer = aliased(ProcessingRun)
        recent = (
            select(func.max(peer.heartbeat))
            .where(
                peer.namespace_id == ProcessingRun.namespace_id, peer.queue == ProcessingRun.queue
            )
            .correlate(ProcessingRun)
            .scalar_subquery()
        )
        with self.service.sessions() as db:
            candidates = db.scalars(
                select(ProcessingRun.id)
                .where(eligible, ProcessingRun.queue.in_(self.queues))
                .order_by(recent.asc().nullsfirst(), ProcessingRun.created_at, ProcessingRun.id)
                .limit(16)
            ).all()
        # Lock one candidate at a time. Locking the whole candidate page prevents
        # other workers from claiming distinct jobs in the same namespace.
        for identity in candidates:
            with self.service.sessions.begin() as db:
                run = db.scalar(
                    select(ProcessingRun)
                    .where(
                        ProcessingRun.id == identity, eligible, ProcessingRun.queue.in_(self.queues)
                    )
                    .with_for_update(skip_locked=True)
                )
                if not run:
                    continue
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:ns,72))"),
                    {"ns": run.namespace_id},
                )
                namespace = db.get(Namespace, run.namespace_id)
                limit = namespace.policy.get(
                    "max_concurrent_jobs", self.service.settings.namespace_max_concurrent_jobs
                )
                count = db.scalar(
                    select(func.count(ProcessingRun.id)).where(
                        ProcessingRun.namespace_id == run.namespace_id,
                        ProcessingRun.queue == run.queue,
                        ProcessingRun.status == "running",
                        ProcessingRun.deadline > now(),
                        ProcessingRun.id != run.id,
                    )
                )
                if count >= limit:
                    continue
                if run.attempt >= self.service.settings.job_max_attempts:
                    run.status, run.error_code = "failed", "processing.retry_exhausted"
                    continue
                run.lease_generation += 1
                run.lease_token, run.owner = new_id(), self.owner
                run.deadline = now() + timedelta(seconds=self.service.settings.job_lease_seconds)
                run.heartbeat, run.updated_at, run.status = now(), now(), "running"
                run.attempt += 1
                return {
                    "id": run.id,
                    "token": run.lease_token,
                    "generation": run.lease_generation,
                    "config": run.config,
                    "queue": run.queue,
                    "version_id": run.source_version_id,
                    "namespace_id": run.namespace_id,
                    "attempt": run.attempt,
                }
        return None

    def heartbeat(self, lease):
        self.announce()
        with self.service.sessions.begin() as db:
            run = db.scalar(
                select(ProcessingRun).where(ProcessingRun.id == lease["id"]).with_for_update()
            )
            if not self.valid(run, lease):
                return False
            run.heartbeat, run.deadline = (
                now(),
                now() + timedelta(seconds=self.service.settings.job_lease_seconds),
            )
            return True

    def valid(self, run, lease):
        return (
            run
            and run.status == "running"
            and run.lease_token == lease["token"]
            and run.lease_generation == lease["generation"]
            and run.deadline > now()
        )

    def publish(self, lease, result):
        namespace_id = lease.get("namespace_id")
        if not namespace_id:
            with self.service.sessions() as session:
                namespace_id = session.get(ProcessingRun, lease["id"]).namespace_id
        staged_assets = {}
        for asset in result.assets:
            with TemporaryDirectory() as temp:
                path = Path(temp) / "asset"
                path.write_bytes(asset.content)
                staged_assets[asset.id] = self.service.stage_blob(
                    namespace_id, path, asset.media_type, "asset", asset.id
                )
        with self.service.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            run = db.scalar(
                select(ProcessingRun).where(ProcessingRun.id == lease["id"]).with_for_update()
            )
            if not self.valid(run, lease):
                raise Error("operation.lease_lost", "Worker lease is no longer current", 409)
            v = db.get(SourceVersion, run.source_version_id)
            src = db.scalar(select(Source).where(Source.id == v.source_id).with_for_update())
            if src.state in {"deleted", "revoked"}:
                run.status = "cancelled"
                return None
            # Include the original media as an authorized Asset without duplicating bytes.
            if v.original_blob_id and v.media_type.startswith(("audio/", "video/")):
                original = Asset(
                    namespace_id=src.namespace_id,
                    representation_id="",
                    blob_id=v.original_blob_id,
                    kind="original",
                    locator={"kind": "original"},
                )
                original.id = new_id()
                original_ref = f"wks://assets/{original.id}"
                for b in result.blocks:
                    b.asset_refs.append(original_ref)
            else:
                original = None
            # Metadata is searchable even when no modality processor is available.
            meta_text = "\n".join([src.title, src.description, " ".join(src.tags)])
            result.blocks.insert(
                0,
                Block(
                    "heading",
                    meta_text[:2000],
                    {"kind": "metadata"},
                    origin_kind="metadata",
                    data={"level": 1},
                ),
            )
            from wks_core.domain.canonical import validate_block
            from wks_core.storage.stabilization import RepresentationBlock

            blocks = [validate_block(asdict(b), ordinal) for ordinal, b in enumerate(result.blocks)]
            rid = new_id()
            generation = db.scalar(
                text(
                    "UPDATE namespaces SET index_generation=index_generation+1 WHERE id=:id RETURNING index_generation"
                ),
                {"id": src.namespace_id},
            )
            producer = {
                "name": result.processor_name,
                "version": result.processor_version,
                "config_digest": run.config_digest,
                "processor_digest": run.processor_digest,
            }
            from wks_core.domain.searching import language_hint

            language, language_origin = language_hint(
                " ".join(block["text"] for block in blocks)[:8000],
                src.meta.get("declared_language"),
            )
            result.coverage["language"] = {
                "value": language,
                "origin": language_origin,
                "state": "declared" if language_origin == "declared" else "inferred",
            }
            result.coverage.setdefault("quality", {"state": "not_measured"})
            from importlib.metadata import version

            producer["runtime_version"] = version("wks-worker")
            producer["profile_version"] = version("wks-worker-" + lease.get("queue", "native"))
            rep = Representation(
                id=rid,
                namespace_id=src.namespace_id,
                source_version_id=v.id,
                processing_run_id=run.id,
                structure=blocks,
                markdown=markdown(blocks),
                coverage=result.coverage,
                warnings=result.warnings,
                availability=result.availability,
                producer=producer,
                artifact_digest=digest(blocks),
                index_generation=generation,
            )
            db.add(rep)
            db.flush()
            for block in blocks:
                db.add(
                    RepresentationBlock(
                        representation_id=rid,
                        ordinal=block["ordinal"],
                        block_id=block["id"],
                        kind=block["type"],
                        parent_id=block.get("parent_id"),
                        value=block,
                        checksum=func.encode(
                            func.sha256(
                                func.convert_to(cast(literal(block, type_=JSONB), Text), "UTF8")
                            ),
                            "hex",
                        ),
                    )
                )
            if original:
                original.representation_id = rid
                db.add(original)
            for a in result.assets:
                blob = self.service.attach_blob(db, staged_assets[a.id], src.namespace_id)
                db.add(
                    Asset(
                        id=a.id,
                        namespace_id=src.namespace_id,
                        representation_id=rid,
                        blob_id=blob.id,
                        locator=a.locator,
                        kind=a.kind,
                        caption=a.caption,
                    )
                )
            config = {"en": "wks_english", "pt": "wks_portuguese", "simple": "wks_simple"}[language]
            for ordinal, b in enumerate(blocks):
                if not b["text"].strip():
                    continue
                for part_no, offset in enumerate(range(0, len(b["text"]), 2000)):
                    value = b["text"][offset : offset + 2000]
                    # Weighted native lexical vector. All arguments are bound parameters.
                    vector = db.scalar(
                        select(
                            func.setweight(
                                func.to_tsvector(
                                    text(f"'{config}'::regconfig"),
                                    unicodedata.normalize("NFC", value),
                                ),
                                text(
                                    "'A'::\"char\""
                                    if b["origin_kind"] == "metadata"
                                    else "'D'::\"char\""
                                ),
                            )
                        )
                    )
                    db.add(
                        Segment(
                            namespace_id=src.namespace_id,
                            representation_id=rid,
                            ordinal=ordinal * 100000 + part_no,
                            block_refs=[b["id"]],
                            asset_refs=b["asset_refs"],
                            text=value,
                            origin_kind=b["origin_kind"],
                            locale=language,
                            locator=b["locator"],
                            checksum=hashlib.sha256(value.encode()).hexdigest(),
                            search_vector=vector,
                        )
                    )
            db.flush()
            v.current_representation_id = rid
            if src.current_version_id == v.id:
                src.availability, src.updated_at = result.availability, now()
            run.status = "partial" if result.availability == "text_partial" else "succeeded"
            run.error_code = None
            run.coverage, run.published_representation_id, run.updated_at = (
                result.coverage,
                rid,
                now(),
            )
            self.service.event(
                db,
                src,
                "representation.published",
                source_version_id=v.id,
                representation_id=rid,
                operation_id=run.id,
                index_generation=generation,
            )
            return rid

    def fail(self, lease, code, retryable=False):
        with self.service.sessions.begin() as db:
            run = db.get(ProcessingRun, lease["id"], with_for_update=True)
            if not self.valid(run, lease):
                return
            run.error_code, run.updated_at = code, now()
            if retryable and run.attempt < self.service.settings.job_max_attempts:
                run.status, run.next_retry_at = (
                    "retry_wait",
                    now() + timedelta(seconds=2**run.attempt + secrets.randbelow(1000) / 1000),
                )
            else:
                run.status = "failed"
            v = db.get(SourceVersion, run.source_version_id)
            src = db.get(Source, v.source_id)
            self.service.event(
                db,
                src,
                "processing.failed",
                operation_id=run.id,
                source_version_id=v.id,
                error_code=code,
            )

    def run_once(self):
        self.announce()
        lease = self.claim()
        if not lease:
            return self.service.export_run_once() if "native" in self.queues else False
        stop = threading.Event()

        def heartbeats():
            while not stop.wait(heartbeat_interval(self.service.settings.job_lease_seconds)):
                if not self.heartbeat(lease):
                    break

        thread = threading.Thread(target=heartbeats, daemon=True)
        thread.start()
        started = time.monotonic()
        state = "succeeded"
        try:
            if lease["queue"] == "docling" and lease["config"].get(
                "docling_artifact_manifest_sha256"
            ):
                root = Path(lease["config"]["docling_artifacts_path"])
                manifest = root / "wks-model-manifest.json"
                if file_digest(manifest) != lease["config"]["docling_artifact_manifest_sha256"]:
                    raise Error(
                        "processing.model_changed", "Pinned Docling manifest integrity failed"
                    )
                for relative, expected in json.loads(manifest.read_text())["files"].items():
                    path = (root / relative).resolve()
                    if not path.is_relative_to(root.resolve()) or file_digest(path) != expected:
                        raise Error(
                            "processing.model_changed", "Pinned Docling model integrity failed"
                        )
            if lease["queue"] == "media" and lease["config"].get("asr_model_sha256"):
                model = Path(lease["config"]["asr_model_path"]) / "model.bin"
                if file_digest(model) != lease["config"]["asr_model_sha256"]:
                    raise Error("processing.model_changed", "Pinned ASR model integrity failed")
            with self.service.sessions() as db:
                v = db.get(SourceVersion, lease["version_id"])
                blob = db.get(Blob, v.original_blob_id) if v.original_blob_id else None
            if not blob:
                result = ExtractedRepresentation(
                    processor_name="bookmark",
                    availability="metadata_only",
                    coverage={"text": {"state": "bookmark_only"}},
                )
            else:
                with TemporaryDirectory() as temp:
                    path, config_path = Path(temp) / "input", Path(temp) / "config.json"
                    self.service.store.materialize(blob.storage_key, path)
                    if file_digest(path) != v.checksum:
                        raise Error("processing.checksum_mismatch", "Original integrity failed")
                    config_path.write_text(
                        json.dumps(
                            lease["config"]
                            | {
                                field: str(Path(lease["config"][field]).resolve())
                                for field in ("asr_model_path", "docling_artifacts_path")
                                if lease["config"].get(field)
                            }
                            | {
                                "processing_timeout_seconds": self.service.settings.processing_timeout_seconds,
                                "process_max_memory_mb": self.service.settings.process_max_memory_mb,
                                "decoder_sandbox": self.service.settings.decoder_sandbox,
                            }
                        )
                    )
                    p = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "wks_worker.processing",
                            str(path),
                            blob.mime,
                            str(config_path),
                            temp,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        cwd=temp,
                        env={
                            key: value
                            for key, value in os.environ.items()
                            if key
                            in {
                                "PATH",
                                "LANG",
                                "LC_ALL",
                                "LD_LIBRARY_PATH",
                                "OMP_NUM_THREADS",
                                "OPENBLAS_NUM_THREADS",
                                "MKL_NUM_THREADS",
                                "PYTHONPATH",
                            }
                        }
                        | {
                            "TMPDIR": temp,
                            "HF_HOME": str(Path(temp) / "hf-cache"),
                            "HF_HUB_OFFLINE": "1",
                            "TRANSFORMERS_OFFLINE": "1",
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                    )
                    try:
                        p.wait(timeout=self.service.settings.processing_timeout_seconds)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.wait()
                        raise
                    if p.returncode:
                        error = Path(temp) / "error.json"
                        code = (
                            json.loads(error.read_text())["code"]
                            if error.exists()
                            else "processing.worker_crashed"
                        )
                        raise Error(code, "Extraction failed")
                    data = json.loads((Path(temp) / "result.json").read_text())
                    data["blocks"] = [Block(**b) for b in data["blocks"]]
                    data["assets"] = [
                        ExtractedAsset(content=(Path(temp) / a["id"]).read_bytes(), **a)
                        for a in data["assets"]
                    ]
                    result = ExtractedRepresentation(**data)
            if lease["config"].get("enrichment_provider") == "http":
                self.enrich(lease, result)
            self.publish(lease, result)
        except subprocess.TimeoutExpired:
            state = "failed"
            self.fail(lease, "processing.timeout", retryable=True)
        except Error as exc:
            state = "failed"
            self.fail(lease, exc.code, retryable=exc.retryable)
        except Exception:
            state = "failed"
            self.fail(lease, "processing.infrastructure_unavailable", retryable=True)
        finally:
            from wks_worker.telemetry import observe

            observe(lease["queue"], state, time.monotonic() - started)
            stop.set()
            thread.join(timeout=2)
            with self.service.sessions.begin() as db:
                db.add(
                    Stage(
                        run_id=lease["id"],
                        name="extract_publish",
                        attempt=lease["attempt"],
                        state=state,
                        duration_seconds=time.monotonic() - started,
                        facts={},
                    )
                )
        return True

    def enrich(self, lease, result):
        from wks_core.storage.enrichment import HTTPMediaEnricher

        try:
            enricher = HTTPMediaEnricher(self.service.settings)
        except ValueError:
            result.warnings.append("enrichment_unavailable")
            return
        visual_assets = [asset for asset in result.assets if asset.media_type.startswith("image/")]
        enriched = 0
        for asset in visual_assets[: self.service.settings.enrichment_max_assets]:
            if not asset.media_type.startswith("image/"):
                continue
            operation = digest(
                {
                    "version": lease["version_id"],
                    "checksum": hashlib.sha256(asset.content).hexdigest(),
                    "locator": asset.locator,
                    "config": lease["config"],
                }
            )
            try:
                response = enricher.enrich(asset.content, asset.media_type, operation)
                result.blocks.append(
                    Block(
                        "paragraph",
                        response["description"],
                        asset.locator,
                        origin_kind="ai_description",
                        asset_refs=[f"wks://assets/{asset.id}"],
                        data={"producer": response["producer"], "operation_id": operation},
                    )
                )
                enriched += 1
            except Error as exc:
                result.warnings.append(exc.code)
        result.coverage["visual"] = {
            "state": "not_applicable"
            if not visual_assets
            else "pending"
            if not enriched
            else "partial"
            if enriched < len(visual_assets)
            else "described",
            "semantic_completeness": "not_measured",
            "assets_identified": len(visual_assets),
            "assets_enriched": enriched,
        }

    def reconcile(self, orphan_age_seconds=3600):
        from wks_core.storage.stabilization import RetentionSweep

        with self.service.sessions() as db:
            checkpoint = db.get(RetentionSweep, "deleted-sources")
            statement = select(Source.id).where(Source.deleted_at.is_not(None))
            if checkpoint and checkpoint.cursor:
                statement = statement.where(Source.id > checkpoint.cursor)
            deleted = db.scalars(statement.order_by(Source.id).limit(101)).all()
        for sid in deleted[:100]:
            self.service.purge_source(sid)
        with self.service.sessions.begin() as db:
            checkpoint = db.get(RetentionSweep, "deleted-sources")
            if not checkpoint:
                checkpoint = RetentionSweep(id="deleted-sources")
                db.add(checkpoint)
            checkpoint.cursor = deleted[99] if len(deleted) > 100 else None
            checkpoint.updated_at = now()
        from wks_core.storage.journal import reconcile

        journal = reconcile(self.service, grace_seconds=orphan_age_seconds)
        return {
            "deleted_sources_reconciled": len(deleted[:100]),
            "next_cursor": checkpoint.cursor,
            "journal": journal,
            "orphan_scan": "use_paged_garbage_collect",
        }
