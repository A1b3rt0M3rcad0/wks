"""A container-local identity pointer; liveness comes from that exact DB heartbeat."""

import json
import os
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from wks_core.application.worker_status import HEARTBEAT_TTL_SECONDS
from wks_core.storage.database import now
from wks_core.storage.stabilization import WorkerHeartbeat


def write_identity(path, owner, queues):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump({"worker_id": owner, "queues": list(queues)}, file)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def probe(service, path, queues):
    try:
        identity = json.loads(Path(path).read_text())
        if identity["queues"] != list(queues):
            return {"liveness": "unavailable", "reason": "profile_mismatch"}
        with service.sessions() as db:
            row = db.get(WorkerHeartbeat, identity["worker_id"])
            if not row or row.queues != list(queues):
                return {"liveness": "unavailable", "reason": "worker_unknown"}
            alive = row.updated_at > now() - timedelta(seconds=HEARTBEAT_TTL_SECONDS)
            return {
                "liveness": "alive" if alive else "stale",
                "worker_id": row.owner,
                "queues": row.queues,
                "heartbeat_at": row.updated_at.isoformat(),
                "ready_queues": row.capabilities.get("ready_queues", []) if alive else [],
            }
    except Exception:
        # A probe must never log credentials, model paths or SQL connection details.
        return {"liveness": "unavailable", "reason": "probe_unavailable"}
