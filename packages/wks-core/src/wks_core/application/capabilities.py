"""Live, bounded capability discovery; static format acceptance is not worker health."""

from datetime import timedelta

from sqlalchemy import select
from wks_core.domain.canonical import SCHEMA_VERSION
from wks_core.domain.formats import FormatRegistry
from wks_core.domain.policies import GRANT_ACTIONS
from wks_core.storage.database import now
from wks_core.storage.stabilization import WorkerHeartbeat
from wks_core.version import VERSION


class CapabilitiesService:
    def capabilities(self, p):
        with self.sessions() as db:
            self.grant(db, p)
            rows = db.scalars(
                select(WorkerHeartbeat)
                .where(WorkerHeartbeat.updated_at > now() - timedelta(seconds=90))
                .limit(100)
            ).all()
        queues = sorted(
            {
                queue
                for row in rows
                for queue in row.capabilities.get("ready_queues", row.queues)
                if row.capabilities.get("ready", False)
            }
        )
        limits = {
            field: getattr(self.settings, field)
            for field in (
                "upload_max_bytes",
                "namespace_max_bytes",
                "process_max_pages",
                "process_max_table_rows",
                "search_max_page_size",
                "search_max_response_chars",
            )
        }
        return {
            "version": VERSION,
            "representation_schema_version": SCHEMA_VERSION,
            "formats": FormatRegistry().public(
                queues,
                limits,
                optional_enabled={
                    "wav": any(row.capabilities.get("asr_enabled") for row in rows),
                    "mp4": any(row.capabilities.get("video_enabled") for row in rows),
                },
                producer_versions={
                    queue: row.capabilities.get("profiles", {}).get(queue)
                    for row in rows
                    for queue in row.queues
                },
            ),
            "limits": limits,
            "search_modes": ["lexical", "phrase", "exact", "fuzzy", "proximity"],
            "worker_queues": queues,
            "actions": sorted(GRANT_ACTIONS),
            "storage": self.store.capabilities(),
            "stream_revalidation": {
                "max_bytes": self.settings.stream_revalidation_bytes,
                "max_seconds": self.settings.stream_revalidation_seconds,
            },
            "upload_protocol": {
                "name": "offset-sha256-v1",
                "min_part_bytes": self.settings.upload_min_part_bytes,
                "max_part_bytes": self.settings.upload_max_part_bytes,
            },
            "worker_health": [
                {
                    "queues": row.queues,
                    "capabilities": row.capabilities,
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ],
            "qualification": "functional_fixtures; document_accuracy_not_measured",
        }
