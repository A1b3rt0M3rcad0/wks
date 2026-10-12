"""Offset/SHA-256 upload protocol; every part is durable before offset advancement."""

import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select, text
from wks_core.application.utilities import file_digest
from wks_core.domain.models import Error, not_found
from wks_core.storage.database import Blob, SourceVersion, Upload, now
from wks_core.storage.stabilization import BlobOperation, UploadPart


class TransferService:
    def upload_record(self, db, p, uid, action):
        record = db.get(Upload, uid)
        if not record:
            raise not_found()
        version = db.get(SourceVersion, record.source_version_id)
        db.info["wks_action"] = action
        self.source(db, p, version.source_id, action=action, writing=action != "upload:read")
        return record, version

    @staticmethod
    def upload_json(record):
        return {
            "upload_id": record.id,
            "status": record.status,
            "offset": record.received_offset,
            "expected_size": record.expected_size,
            "checksum_sha256": record.expected_sha,
            "expires_at": record.expires_at.isoformat(),
            "protocol": record.protocol,
        }

    def upload_status(self, p, uid):
        with self.sessions() as db:
            record, _ = self.upload_record(db, p, uid, "upload:read")
            return self.upload_json(record)

    def receive_part(self, p, uid, path, offset, checksum_sha256):
        if offset < 0 or not re.fullmatch(r"[a-f0-9]{64}", checksum_sha256):
            raise Error("contract.invalid", "Part offset and SHA-256 are required")
        path = Path(path)
        size = path.stat().st_size
        if size < 1 or size > self.settings.upload_max_part_bytes:
            raise Error("upload.part_size_invalid", "Part size exceeds protocol bounds", 413)
        if file_digest(path) != checksum_sha256:
            raise Error("upload.checksum_mismatch", "Part checksum does not match")
        with self.sessions() as db:
            record, version = self.upload_record(db, p, uid, "upload:write")
            if record.status in {"aborted", "expired"} or record.expires_at <= now():
                raise Error("upload.expired", "Upload session unavailable", 410)
            previous = db.get(UploadPart, (uid, offset))
            if previous:
                if previous.sha256 != checksum_sha256 or previous.size != size:
                    raise Error("upload.part_conflict", "Offset already contains another part", 409)
                return self.upload_json(record)
            if record.status in {"received", "committed"} or offset != record.received_offset:
                raise Error("upload.offset_conflict", "Use the confirmed server offset", 409)
            if offset + size > record.expected_size:
                raise Error("upload.too_large", "Part exceeds declared source size", 413)
            if size < self.settings.upload_min_part_bytes and offset + size != record.expected_size:
                raise Error("upload.part_size_invalid", "Nonfinal part is below the minimum size")
            namespace_id = version.namespace_id
        identity = self.stage_blob(
            namespace_id, path, "application/octet-stream", "part", uid + ":" + str(offset)
        )
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            record = db.get(Upload, uid, with_for_update=True)
            self.upload_record(db, p, uid, "upload:write")
            if record.status in {"aborted", "expired"} or record.expires_at <= now():
                raise Error("upload.expired", "Upload session unavailable", 410)
            previous = db.get(UploadPart, (uid, offset))
            if previous:
                if previous.sha256 != checksum_sha256 or previous.size != size:
                    raise Error("upload.part_conflict", "Offset already contains another part", 409)
                return self.upload_json(record)
            if record.received_offset != offset or record.status != "pending":
                raise Error("upload.offset_conflict", "Use the confirmed server offset", 409)
            self.rate_limit(db, p, "upload:write")
            db.add(
                UploadPart(
                    upload_id=uid,
                    offset=offset,
                    size=size,
                    sha256=checksum_sha256,
                    journal_id=identity,
                )
            )
            record.received_offset += size
            record.protocol = "offset-sha256-v1"
            return self.upload_json(record)

    def prepare_commit(self, p, uid):
        with self.sessions() as db:
            record, version = self.upload_record(db, p, uid, "upload:commit")
            if record.status == "committed":
                return
            if record.status in {"aborted", "expired"} or record.expires_at <= now():
                raise Error("upload.expired", "Upload session unavailable", 410)
            namespace_id, expected_size, expected_sha, mime = (
                version.namespace_id,
                record.expected_size,
                record.expected_sha,
                version.declared_media_type,
            )
            blob = db.get(Blob, record.staged_blob_id) if record.staged_blob_id else None
            if record.status == "received" and blob:
                journal = db.scalar(
                    select(BlobOperation).where(
                        BlobOperation.blob_id == blob.id, BlobOperation.state == "attached"
                    )
                )
                if journal:
                    return
                legacy = (
                    blob.id,
                    blob.storage_key,
                    blob.sha256,
                    blob.size,
                    blob.mime,
                    blob.provider,
                )
            else:
                legacy = None
                if record.received_offset != expected_size:
                    raise Error(
                        "upload.incomplete",
                        "No validated upload available; declared bytes are incomplete",
                        409,
                    )
            parts = (
                db.execute(
                    select(
                        UploadPart.offset,
                        UploadPart.size,
                        UploadPart.sha256,
                        BlobOperation.storage_key,
                    )
                    .join(BlobOperation, BlobOperation.id == UploadPart.journal_id)
                    .where(UploadPart.upload_id == uid)
                    .order_by(UploadPart.offset)
                ).all()
                if not legacy
                else []
            )
        if legacy:
            bid, key, sha, size, mime, provider = legacy
            if not self.store.verify_checksum(key, sha, size):
                raise Error(
                    "upload.checksum_mismatch", "Legacy uploaded object failed verification"
                )
            with self.sessions.begin() as db:
                self.upload_record(db, p, uid, "upload:commit")
                if not db.scalar(select(BlobOperation.id).where(BlobOperation.storage_key == key)):
                    db.add(
                        BlobOperation(
                            namespace_id=namespace_id,
                            kind="legacy-upload",
                            resource_id=uid,
                            storage_key=key,
                            sha256=sha,
                            size=size,
                            mime=mime,
                            provider=provider,
                            state="attached",
                            blob_id=bid,
                        )
                    )
            return
        with TemporaryDirectory() as directory:
            path = Path(directory) / "assembled"
            digest, position = hashlib.sha256(), 0
            with path.open("wb") as stream:
                for offset, size, sha, key in parts:
                    if offset != position:
                        raise Error("upload.incomplete", "Part sequence is incomplete", 409)
                    part_digest, part_size = hashlib.sha256(), 0
                    for chunk in self.store.iter_bytes(key):
                        stream.write(chunk)
                        digest.update(chunk)
                        part_digest.update(chunk)
                        part_size += len(chunk)
                    if part_size != size or part_digest.hexdigest() != sha:
                        raise Error("upload.checksum_mismatch", "Durable upload part is corrupt")
                    position += size
            if position != expected_size or digest.hexdigest() != expected_sha:
                raise Error("upload.checksum_mismatch", "Complete source checksum does not match")
            from wks_core.storage.content import detect_mime

            identity = self.stage_blob(namespace_id, path, detect_mime(path, mime), "upload", uid)
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            record = db.get(Upload, uid, with_for_update=True)
            self.upload_record(db, p, uid, "upload:commit")
            if record.status in {"expired", "aborted"} or record.expires_at <= now():
                raise Error("upload.expired", "Upload session unavailable", 410)
            blob = self.attach_blob(db, identity, namespace_id)
            record.staged_blob_id, record.status = "" + blob.id, "received"
            record.received_offset = expected_size

    def upload_abort(self, p, key, uid):
        def op(db):
            record, _ = self.upload_record(db, p, uid, "upload:abort")
            db.refresh(record, with_for_update=True)
            if record.status == "committed":
                raise Error("upload.already_committed", "Committed original cannot be aborted", 409)
            record.status = "aborted"
            record.expires_at = now()
            return self.upload_json(record)

        return self.mutate(p, key, {"op": "upload_abort", "upload_id": uid}, op)

    def cleanup_upload_parts(self, uid):
        with self.sessions() as db:
            record = db.get(Upload, uid)
            if not record or record.status not in {"committed", "aborted", "expired"}:
                return
            rows = db.execute(
                select(BlobOperation.id, BlobOperation.storage_key)
                .join(UploadPart, UploadPart.journal_id == BlobOperation.id)
                .where(UploadPart.upload_id == uid, BlobOperation.state != "cleaned")
                .order_by(BlobOperation.id)
                .limit(100)
            ).all()
        for identity, key in rows:
            self.store.delete(key)
            with self.sessions.begin() as db:
                row = db.get(BlobOperation, identity)
                if row:
                    row.state = "cleaned"
                    row.updated_at = now()
