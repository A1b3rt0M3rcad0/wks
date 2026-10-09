import hashlib
import json
import os
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

from sqlalchemy import and_, func, or_, select, text

from wks.application.service import digest, file_digest, markdown
from wks.domain.models import Block, Error, ExtractedAsset, ExtractedRepresentation, new_id
from wks.infrastructure.database import (
    Asset,
    Blob,
    ProcessingRun,
    Representation,
    Segment,
    Source,
    SourceVersion,
    Stage,
    now,
)


class Worker:
    def __init__(self, service, owner=None):
        self.service, self.owner = service, owner or new_id()

    def claim(self):
        with self.service.sessions.begin() as db:
            eligible = or_(
                ProcessingRun.status == "queued",
                and_(ProcessingRun.status == "retry_wait", ProcessingRun.next_retry_at <= now()),
                and_(ProcessingRun.status == "running", ProcessingRun.deadline < now()),
            )
            run = db.scalar(
                select(ProcessingRun)
                .where(eligible)
                .order_by(ProcessingRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not run:
                return None
            if run.attempt >= self.service.settings.job_max_attempts:
                run.status, run.error_code = "failed", "processing.retry_exhausted"
                return None
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
                "version_id": run.source_version_id,
                "attempt": run.attempt,
            }

    def heartbeat(self, lease):
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
            blocks = [asdict(b) for b in result.blocks]
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
            if original:
                original.representation_id = rid
                db.add(original)
            asset_size = sum(len(a.content) for a in result.assets)
            self.service.quota(db, src.namespace_id, asset_size)
            for a in result.assets:
                with TemporaryDirectory() as temp:
                    path = Path(temp) / "asset"
                    path.write_bytes(a.content)
                    blob = self.service.save_blob(db, src.namespace_id, path, a.media_type)
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
            declared = src.meta.get("declared_language", "pt")
            language = (
                "en"
                if declared.startswith("en")
                else "pt"
                if declared.startswith("pt")
                else "simple"
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
                                    unicodedata.normalize("NFC", src.title),
                                ),
                                text("'A'::\"char\""),
                            ).op("||")(
                                func.to_tsvector(
                                    text(f"'{config}'::regconfig"),
                                    unicodedata.normalize("NFC", value),
                                )
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
                    now() + timedelta(seconds=2**run.attempt),
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
        lease = self.claim()
        if not lease:
            return False
        stop = threading.Event()

        def heartbeats():
            while not stop.wait(max(0.2, self.service.settings.job_lease_seconds / 3)):
                if not self.heartbeat(lease):
                    break

        thread = threading.Thread(target=heartbeats, daemon=True)
        thread.start()
        started = time.monotonic()
        state = "succeeded"
        try:
            if lease["config"].get("docling_artifact_manifest_sha256"):
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
            if lease["config"].get("asr_model_sha256"):
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
                                    "processing_timeout_seconds": self.service.settings.processing_timeout_seconds,
                                    "process_max_memory_mb": self.service.settings.process_max_memory_mb,
                                }
                            )
                        )
                        p = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "wks.infrastructure.processing",
                                str(path),
                                blob.mime,
                                str(config_path),
                                temp,
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
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
        from wks.infrastructure.enrichment import HTTPMediaEnricher

        try:
            enricher = HTTPMediaEnricher(self.service.settings)
        except ValueError:
            result.warnings.append("enrichment_unavailable")
            return
        enriched = 0
        for asset in result.assets[: self.service.settings.enrichment_max_assets]:
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
            "state": "partial" if enriched < len(result.assets) else "described",
            "assets_identified": len(result.assets),
            "assets_enriched": enriched,
        }

    def reconcile(self, orphan_age_seconds=3600):
        # Recover logical deletions, expired leases are reclaimed through claim().
        with self.service.sessions() as db:
            deleted = db.scalars(select(Source.id).where(Source.deleted_at.is_not(None))).all()
            referenced = set(db.scalars(select(Blob.storage_key)).all())
        for sid in deleted:
            self.service.purge_source(sid)
        # Store keys have no age on the port: do not delete unknown live staging writes.
        # Reconciliation reports orphans; explicit maintenance handles a quiesced store.
        return {
            "deleted_sources_reconciled": len(deleted),
            "orphan_keys": sorted(set(self.service.store.keys()) - referenced),
        }
