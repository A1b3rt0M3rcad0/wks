"""Asynchronous immutable full exports, published through the same blob journal."""

import json
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import and_, or_, select, text
from wks_core.application.utilities import digest, file_digest
from wks_core.domain.models import Error, new_id, not_found
from wks_core.domain.rendering import render_block
from wks_core.storage.database import Asset, Blob, Representation, Source, SourceVersion, now
from wks_core.storage.export_lease import ExportLease
from wks_core.storage.stabilization import ExportArtifact, RepresentationBlock

FORMATS = {
    "markdown-single": "text/markdown",
    "markdown-bundle": "application/zip",
    "structured-json": "application/json",
    "plain-text": "text/plain",
    "source-original": "application/octet-stream",
}


class ExportService:
    def export_create(self, p, key, rid, format="markdown-single"):
        if format not in FORMATS:
            raise Error("contract.invalid", "Unknown export format")

        def op(db):
            representation = self.representation(db, p, rid, action="export:create")
            if format in {"markdown-bundle", "source-original"}:
                version = db.get(SourceVersion, representation.source_version_id)
                self.source(db, p, version.source_id, action="asset_read", version_id=version.id)
            artifact = db.scalar(
                select(ExportArtifact).where(
                    ExportArtifact.representation_id == rid,
                    ExportArtifact.format == format,
                    ExportArtifact.options_digest == digest({}),
                )
            )
            if not artifact:
                artifact = ExportArtifact(
                    namespace_id=representation.namespace_id,
                    representation_id=rid,
                    format=format,
                    options_digest=digest({}),
                    warnings=representation.warnings,
                )
                db.add(artifact)
                db.flush()
            return self.export_json(db, artifact)

        return self.mutate(p, key, {"op": "export", "representation_id": rid, "format": format}, op)

    def export_json(self, db, artifact):
        blob = db.get(Blob, artifact.blob_id) if artifact.blob_id else None
        return {
            "id": artifact.id,
            "representation_id": artifact.representation_id,
            "format": artifact.format,
            "status": artifact.status,
            "sha256": blob.sha256 if blob else None,
            "byte_size": blob.size if blob else None,
            "content_path": f"/v1/exports/{artifact.id}/content"
            if artifact.status == "ready"
            else None,
            "warnings": artifact.warnings,
        }

    def export_get(self, p, identity):
        with self.sessions() as db:
            artifact = db.get(ExportArtifact, identity)
            if not artifact:
                raise not_found()
            self.representation(db, p, artifact.representation_id, action="export:read")
            if artifact.format in {"markdown-bundle", "source-original"}:
                representation = db.get(Representation, artifact.representation_id)
                version = db.get(SourceVersion, representation.source_version_id)
                self.source(db, p, version.source_id, action="asset_read", version_id=version.id)
            return self.export_json(db, artifact)

    def export_binary(self, p, identity):
        self.export_get(p, identity)
        with self.sessions() as db:
            artifact = db.get(ExportArtifact, identity)
            if not artifact or artifact.status != "ready" or not artifact.blob_id:
                raise Error("asset.media_not_ready", "Export is not ready", 409)
            blob = db.get(Blob, artifact.blob_id)
            self.validate_blob_provider(blob)
            if not self.store.exists(blob.storage_key):
                raise Error("asset.media_not_ready", "Export object unavailable", 503, True)
            representation = db.get(Representation, artifact.representation_id)
            version = db.get(SourceVersion, representation.source_version_id)
            return {
                "source_id": version.source_id,
                "source_version_id": version.id,
                "authorization_action": "export:read",
                "also_asset_read": artifact.format in {"markdown-bundle", "source-original"},
                "key": blob.storage_key,
                "mime": blob.mime,
                "size": blob.size,
                "sha256": blob.sha256,
            }

    def export_run_once(self):
        instant = now()
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            artifact = db.scalar(
                select(ExportArtifact)
                .outerjoin(ExportLease)
                .where(
                    or_(
                        ExportArtifact.status == "pending",
                        and_(ExportArtifact.status == "running", ExportLease.expires_at < instant),
                    )
                )
                .order_by(ExportArtifact.created_at)
                .with_for_update(of=ExportArtifact, skip_locked=True)
                .limit(1)
            )
            if not artifact:
                return False
            lease = db.get(ExportLease, artifact.id)
            token = new_id()
            if not lease:
                lease = ExportLease(export_id=artifact.id, generation=0)
                db.add(lease)
            lease.token, lease.generation, lease.expires_at = (
                token,
                lease.generation + 1,
                instant + timedelta(seconds=self.settings.processing_timeout_seconds),
            )
            artifact.status = "running"
            identity, rid, namespace_id, format = (
                artifact.id,
                artifact.representation_id,
                artifact.namespace_id,
                artifact.format,
            )
        try:
            with TemporaryDirectory() as directory:
                path = Path(directory) / "artifact"
                mime = self.build_export(path, rid, format)
                staged = self.stage_blob(namespace_id, path, mime, "export", identity)
            with self.sessions.begin() as db:
                db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
                artifact = db.get(ExportArtifact, identity, with_for_update=True)
                lease = db.get(ExportLease, identity)
                if not artifact or not lease or lease.token != token or lease.expires_at <= now():
                    return True
                representation = db.get(Representation, rid)
                version = db.get(SourceVersion, representation.source_version_id)
                source = db.get(Source, version.source_id, with_for_update=True)
                if source.state in {"deleted", "revoked"}:
                    artifact.status = "cancelled"
                    return True
                blob = self.attach_blob(db, staged, namespace_id)
                artifact.blob_id, artifact.status = blob.id, "ready"
                self.event(
                    db, source, "export.published", export_id=identity, representation_id=rid
                )
        except Exception:
            with self.sessions.begin() as db:
                artifact = db.get(ExportArtifact, identity, with_for_update=True)
                lease = db.get(ExportLease, identity)
                if artifact and lease and lease.token == token:
                    artifact.status = "failed"
                    artifact.warnings = [*artifact.warnings, "export.build_failed"]
        return True

    def build_export(self, path, rid, format):
        with self.sessions() as db:
            representation = db.get(Representation, rid)
            version = db.get(SourceVersion, representation.source_version_id)
            source = db.get(Source, version.source_id)
            if source.state in {"deleted", "revoked"}:
                raise not_found()
            if format == "source-original":
                blob = db.get(Blob, version.original_blob_id)
                if not blob:
                    raise not_found()
                self.store.materialize(blob.storage_key, path)
                if not self.store.verify_checksum(blob.storage_key, blob.sha256, blob.size):
                    raise Error("asset.checksum_mismatch", "Original integrity failed")
                return blob.mime
            assets = (
                db.execute(
                    select(Asset, Blob)
                    .join(Blob, Blob.id == Asset.blob_id)
                    .where(Asset.representation_id == rid)
                    .order_by(Asset.id)
                    .limit(5001)
                ).all()
                if format == "markdown-bundle"
                else []
            )
            if len(assets) > 5000:
                raise Error(
                    "export.asset_limit", "Bundle asset count exceeds configured envelope", 413
                )
            asset_paths = {f"wks://assets/{a.id}": f"assets/{a.id}" for a, _ in assets}
            blocks = db.scalars(
                select(RepresentationBlock.value)
                .where(RepresentationBlock.representation_id == rid)
                .order_by(RepresentationBlock.ordinal)
                .execution_options(yield_per=50)
            )
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                if format == "structured-json":
                    stream.write(
                        '{"representation_id":'
                        + json.dumps(rid)
                        + ',"representation_schema_version":'
                        + json.dumps(representation.schema_version)
                        + ',"blocks":['
                    )
                first = True
                for block in blocks:
                    if format == "structured-json":
                        if not first:
                            stream.write(",")
                        json.dump(block, stream, ensure_ascii=False, sort_keys=True)
                    elif format == "plain-text":
                        stream.write(("\n\n" if not first else "") + block.get("text", ""))
                    else:
                        stream.write(
                            ("\n\n" if not first else "") + render_block(block, asset_paths)
                        )
                    first = False
                if format == "structured-json":
                    stream.write(
                        '],"coverage":'
                        + json.dumps(representation.coverage, sort_keys=True)
                        + ',"warnings":'
                        + json.dumps(representation.warnings)
                        + "}"
                    )
            if format != "markdown-bundle":
                return FORMATS[format]
            markdown_path = path.with_suffix(".md")
            path.rename(markdown_path)
            manifest = {
                "representation_id": rid,
                "source_version_id": version.id,
                "schema_version": representation.schema_version,
                "warnings": representation.warnings,
                "files": [
                    {
                        "path": "document.md",
                        "sha256": file_digest(markdown_path),
                        "byte_size": markdown_path.stat().st_size,
                    }
                ],
            }
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                self.zip_file(archive, "document.md", markdown_path)
                for asset, blob in assets:
                    asset_path = path.parent / asset.id
                    self.store.materialize(blob.storage_key, asset_path)
                    if (
                        file_digest(asset_path) != blob.sha256
                        or asset_path.stat().st_size != blob.size
                    ):
                        raise Error("asset.checksum_mismatch", "Bundle asset integrity failed")
                    relative = asset_paths[f"wks://assets/{asset.id}"]
                    self.zip_file(archive, relative, asset_path)
                    manifest["files"].append(
                        {
                            "path": relative,
                            "asset_ref": f"wks://assets/{asset.id}",
                            "media_type": blob.mime,
                            "sha256": blob.sha256,
                            "byte_size": blob.size,
                        }
                    )
                    asset_path.unlink()
                manifest_path = path.parent / "manifest.json"
                manifest_path.write_text(
                    json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8"
                )
                self.zip_file(archive, "manifest.json", manifest_path)
            return FORMATS[format]

    @staticmethod
    def zip_file(archive, relative, source):
        info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        with archive.open(info, "w") as target, source.open("rb") as stream:
            shutil.copyfileobj(stream, target, 1024 * 1024)
