"""Signed privacy ledger retained independently from SQL snapshots and blob backups."""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from wks_core.domain.models import new_id
from wks_core.storage.database import now


class PrivacyLedger:
    def __init__(self, service, directory=None):
        self.service = service
        configured = directory or getattr(service.settings, "privacy_ledger_path", None)
        self.root = Path(
            configured or (service.settings.storage_path.parent / "privacy-ledger")
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = self.root / "signing-key"
        if not key.exists():
            with NamedTemporaryFile(dir=self.root, delete=False) as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            try:
                os.link(temporary, key)
            except FileExistsError:
                pass
            finally:
                temporary.unlink()
        key.chmod(0o600)
        self.key = key.read_bytes()

    def append(self, kind, resource_id, payload=None):
        import fcntl

        with (self.root / "append-lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._append(kind, resource_id, payload)

    def _append(self, kind, resource_id, payload=None):
        value = {
            "id": f"{max(time.time_ns(), int((self.watermark() or '0').split('-')[0]) + 1):020d}-{new_id()}",
            "kind": kind,
            "resource_id": resource_id,
            "occurred_at": now().isoformat(),
            "payload": payload or {},
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        signed = {
            "event": value,
            "hmac_sha256": hmac.new(self.key, raw, hashlib.sha256).hexdigest(),
        }
        target = self.root / (value["id"] + ".json")
        with NamedTemporaryFile(dir=self.root, delete=False) as stream:
            stream.write(json.dumps(signed, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        try:
            os.link(temporary, target)
        finally:
            temporary.unlink()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return value["id"]

    def events(self):
        for path in sorted(self.root.glob("*.json")):
            if path.is_symlink():
                raise RuntimeError("Invalid privacy ledger path")
            value = json.loads(path.read_text())
            raw = json.dumps(value["event"], sort_keys=True, separators=(",", ":")).encode()
            if not hmac.compare_digest(
                value["hmac_sha256"], hmac.new(self.key, raw, hashlib.sha256).hexdigest()
            ):
                raise RuntimeError("Privacy ledger signature mismatch")
            yield value["event"]

    def watermark(self):
        return max((path.stem for path in self.root.glob("*.json")), default=None)

    def replay(self, service, after=None):
        from sqlalchemy import text

        applied = 0
        for event in self.events():
            # Replay all signed privacy decisions: clock skew must never resurrect data.
            kind, resource, payload = event["kind"], event["resource_id"], event["payload"]
            with service.sessions.begin() as db:
                if kind in {"source_revoked", "source_deleted"}:
                    db.execute(
                        text(
                            "UPDATE sources SET state='revoked', deleted_at=CASE WHEN :purge THEN now() ELSE deleted_at END WHERE id=:id"
                        ),
                        {"id": resource, "purge": kind == "source_deleted"},
                    )
                    db.execute(
                        text(
                            "UPDATE processing_runs SET status='cancelled',lease_token=NULL WHERE source_version_id IN (SELECT id FROM source_versions WHERE source_id=:id) AND status IN ('queued','running','retry_wait')"
                        ),
                        {"id": resource},
                    )
                elif kind == "namespace_deleted":
                    db.execute(
                        text(
                            "UPDATE namespaces SET state='deleted',title='[deleted]',external_ref=NULL,policy='{}'::jsonb WHERE id=:id"
                        ),
                        {"id": resource},
                    )
                elif kind == "collection_deleted":
                    db.execute(
                        text("DELETE FROM collection_sources WHERE collection_id=:id"),
                        {"id": resource},
                    )
                    db.execute(
                        text(
                            "UPDATE collections SET state='deleted',title='[deleted]' WHERE id=:id"
                        ),
                        {"id": resource},
                    )
                elif kind == "grant_revoked":
                    db.execute(
                        text("UPDATE scope_grants SET revoked_at=now() WHERE id=:id"),
                        {"id": resource},
                    )
                elif kind == "client_revoked":
                    db.execute(
                        text("UPDATE clients SET active=false WHERE id=:id"), {"id": resource}
                    )
                elif kind == "credential_revoked":
                    db.execute(
                        text(
                            "UPDATE client_credentials SET revoked_at=now() WHERE client_id=:id AND token_hash=:hash"
                        ),
                        {"id": resource, "hash": payload["token_hash"]},
                    )
                    db.execute(
                        text(
                            "UPDATE clients SET token_hash=:unusable WHERE id=:id AND token_hash=:hash"
                        ),
                        {
                            "id": resource,
                            "hash": payload["token_hash"],
                            "unusable": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                        },
                    )
                elif kind in {"account_recovered", "recovery_rotated"}:
                    db.execute(
                        text(
                            "UPDATE web_accounts SET password_hash=COALESCE(:password,password_hash),recovery_token_hash=:recovery WHERE client_id=:id"
                        ),
                        {
                            "id": resource,
                            "password": payload.get("password_hash"),
                            "recovery": payload["recovery_token_hash"],
                        },
                    )
                    db.execute(
                        text("DELETE FROM web_sessions WHERE client_id=:id"), {"id": resource}
                    )
                    if kind == "account_recovered":
                        db.execute(
                            text(
                                "UPDATE client_credentials SET revoked_at=now() WHERE client_id=:id AND created_at<=CAST(:before AS timestamptz)"
                            ),
                            {"id": resource, "before": event["occurred_at"]},
                        )
                        db.execute(
                            text(
                                "UPDATE scope_grants SET revoked_at=now() WHERE issuer=:id AND created_at<=CAST(:before AS timestamptz)"
                            ),
                            {"id": resource, "before": event["occurred_at"]},
                        )
                        db.execute(
                            text(
                                "UPDATE clients SET token_hash=:hash,auth_generation=auth_generation+1 WHERE id=:id"
                            ),
                            {
                                "id": resource,
                                "hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                            },
                        )
            if kind == "source_deleted":
                service.purge_source(resource)
            applied += 1
        return applied
