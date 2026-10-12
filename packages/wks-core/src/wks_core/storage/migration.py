"""Explicit, resumable operator migration; never changes global provider silently."""

from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select
from wks_core.application.utilities import file_digest
from wks_core.storage.database import Blob


def migrate(service, destination, page_size=100, cursor=None):
    size = min(max(page_size, 1), 1000)
    with service.sessions() as db:
        query = select(Blob)
        if cursor:
            query = query.where(Blob.id > cursor)
        rows = db.scalars(query.order_by(Blob.id).limit(size + 1)).all()
        values = [
            (row.id, row.storage_key, row.sha256, row.size, row.provider) for row in rows[:size]
        ]
    transferred = 0
    for identity, key, sha, byte_size, provider in values:
        target = destination.location()
        with service.sessions() as db:
            record = db.get(Blob, identity)
            if record.location == target and record.provider == target["provider"]:
                continue
        if provider != service.settings.storage_provider:
            raise RuntimeError("Resume with the original provider until all objects have migrated")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "original"
            service.store.materialize(key, path)
            if file_digest(path) != sha or path.stat().st_size != byte_size:
                raise RuntimeError("Migration refused corrupt original")
            if not destination.exists(key):
                destination.put_file(key, path)
            if not destination.verify_checksum(key, sha, byte_size):
                raise RuntimeError("Destination integrity failed")
        with service.sessions.begin() as db:
            record = db.get(Blob, identity, with_for_update=True)
            record.location = target
            record.provider = target["provider"]
        transferred += 1
    return {
        "objects_migrated": transferred,
        "next_cursor": values[-1][0] if len(rows) > size else None,
        "switch_configuration_after_complete": len(rows) <= size,
        "original_objects_removed": False,
    }
