"""Bounded operational diagnostics, with job details restricted to the caller's scope."""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, text
from wks_core.processing import QUEUES
from wks_core.storage.database import ProcessingRun, Representation, Source, SourceVersion, now
from wks_core.storage.stabilization import WorkerHeartbeat

HEARTBEAT_TTL_SECONDS = 90


def coverage_states(coverage):
    allowed = {
        "complete",
        "partial",
        "pending",
        "failed",
        "unavailable",
        "unsupported",
        "not_interpreted",
        "not_applicable",
        "described",
        "not_measured",
        "empty",
        "sampled",
        "declared",
        "inferred",
        "no_speech_detected",
        "bookmark_only",
        "active",
    }
    return {
        key: value["state"] if value.get("state") in allowed else "unknown"
        for key, value in coverage.items()
        if key
        in {
            "text",
            "visual",
            "ocr",
            "audio",
            "video",
            "transcription",
            "audio_semantics",
            "language",
            "quality",
            "sandbox",
        }
        and isinstance(value, dict)
    }


class WorkerStatusService:
    def worker_status(self, p):
        instant = now()
        stale_after = instant - timedelta(seconds=HEARTBEAT_TTL_SECONDS)
        stalled_after = instant - timedelta(seconds=self.settings.worker_stall_seconds)
        with self.sessions() as db:
            db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            clauses, _ = self.visible(db, p, "operation:read")
            scoped = (
                select(
                    ProcessingRun.id,
                    ProcessingRun.source_version_id,
                    ProcessingRun.owner,
                    ProcessingRun.queue,
                    ProcessingRun.status,
                    ProcessingRun.updated_at,
                    ProcessingRun.deadline,
                    ProcessingRun.attempt,
                    ProcessingRun.next_retry_at,
                    ProcessingRun.error_code,
                    ProcessingRun.coverage,
                    ProcessingRun.published_representation_id,
                )
                .join(SourceVersion, SourceVersion.id == ProcessingRun.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(*clauses)
                .subquery()
            )
            capacity = {}
            for queue in QUEUES:
                live = WorkerHeartbeat.updated_at > stale_after
                count = db.scalar(
                    select(func.count())
                    .select_from(WorkerHeartbeat)
                    .where(live, WorkerHeartbeat.queues.contains([queue]))
                )
                ready = db.scalar(
                    select(func.count())
                    .select_from(WorkerHeartbeat)
                    .where(live, WorkerHeartbeat.capabilities["ready_queues"].contains([queue]))
                )
                busy = db.scalar(
                    select(func.count(func.distinct(ProcessingRun.owner)))
                    .select_from(ProcessingRun)
                    .where(
                        ProcessingRun.queue == queue,
                        ProcessingRun.status == "running",
                        ProcessingRun.deadline > instant,
                        ProcessingRun.owner.in_(
                            select(WorkerHeartbeat.owner).where(
                                live,
                                WorkerHeartbeat.capabilities["ready_queues"].contains([queue]),
                            )
                        ),
                    )
                )
                capacity[queue] = (count, ready, busy >= ready if ready else False)
            workers = db.scalars(
                select(WorkerHeartbeat)
                .order_by(WorkerHeartbeat.updated_at.desc(), WorkerHeartbeat.owner)
                .limit(100)
            ).all()
            queue_counts = defaultdict(dict)
            for queue, state, count in db.execute(
                select(scoped.c.queue, scoped.c.status, func.count()).group_by(
                    scoped.c.queue, scoped.c.status
                )
            ):
                queue_counts[queue][state] = count
            eligible = or_(
                scoped.c.status == "queued",
                and_(scoped.c.status == "retry_wait", scoped.c.next_retry_at <= instant),
                and_(scoped.c.status == "running", scoped.c.deadline <= instant),
            )
            queue_progress = {
                queue: {
                    "eligible": count,
                    "oldest_pending_at": oldest.isoformat() if oldest else None,
                    "stalled_pending": stalled,
                }
                for queue, count, oldest, stalled in db.execute(
                    select(
                        scoped.c.queue,
                        func.count(),
                        func.min(scoped.c.updated_at),
                        func.count().filter(scoped.c.updated_at < stalled_after),
                    )
                    .where(eligible)
                    .group_by(scoped.c.queue)
                )
            }
            active = dict(
                db.execute(
                    select(scoped.c.queue, func.count())
                    .where(
                        scoped.c.status == "running",
                        scoped.c.deadline > instant,
                        scoped.c.owner.in_(
                            select(WorkerHeartbeat.owner).where(
                                WorkerHeartbeat.updated_at > stale_after
                            )
                        ),
                    )
                    .group_by(scoped.c.queue)
                ).all()
            )
            recovery = {
                queue: (count, deadline)
                for queue, count, deadline in db.execute(
                    select(scoped.c.queue, func.count(), func.min(scoped.c.deadline))
                    .where(
                        scoped.c.status == "running",
                        scoped.c.deadline > instant,
                        scoped.c.owner.not_in(
                            select(WorkerHeartbeat.owner).where(
                                WorkerHeartbeat.updated_at > stale_after
                            )
                        ),
                    )
                    .group_by(scoped.c.queue)
                )
            }
            successes = dict(
                db.execute(
                    select(scoped.c.owner, func.max(scoped.c.updated_at))
                    .where(scoped.c.status.in_(("succeeded", "partial")))
                    .group_by(scoped.c.owner)
                ).all()
            )
            ranked = select(
                scoped,
                func.row_number()
                .over(
                    partition_by=scoped.c.owner,
                    order_by=(scoped.c.updated_at.desc(), scoped.c.id.desc()),
                )
                .label("rank"),
            ).subquery()
            latest = {
                row.owner: row
                for row in db.execute(
                    select(ranked).where(ranked.c.rank == 1, ranked.c.owner.is_not(None))
                )
            }
            # Only producer identifiers, never document content or paths.
            producers = dict(
                db.execute(
                    select(Representation.id, Representation.producer["name"].astext).where(
                        Representation.id.in_(
                            [
                                row.published_representation_id
                                for row in latest.values()
                                if row.published_representation_id
                            ]
                        )
                    )
                ).all()
            )
        worker_items = []
        for worker in workers:
            alive = worker.updated_at > stale_after
            ready = worker.capabilities.get("ready_queues", []) if alive else []
            last = latest.get(worker.owner)
            worker_items.append(
                {
                    "worker_id": worker.owner,
                    "queues": worker.queues,
                    "liveness": "alive" if alive else "stale",
                    "heartbeat_at": worker.updated_at.isoformat(),
                    "ready_queues": ready,
                    "capabilities": worker.capabilities,
                    "last_success_at": successes[worker.owner].isoformat()
                    if worker.owner in successes
                    else None,
                    "last_operation": {
                        "operation_id": last.id,
                        "source_version_id": last.source_version_id,
                        "published_representation_id": last.published_representation_id,
                        "state": last.status,
                        "attempt": last.attempt,
                        "error_code": last.error_code,
                        "next_retry_at": last.next_retry_at.isoformat()
                        if last.next_retry_at
                        else None,
                        "parser": producers.get(last.published_representation_id),
                        "coverage": coverage_states(last.coverage),
                    }
                    if last
                    else None,
                }
            )
        queues = []
        for queue in QUEUES:
            live_count, ready_count, capacity_busy = capacity[queue]
            alive, ready = bool(live_count), bool(ready_count)
            progress = queue_progress.get(
                queue, {"eligible": 0, "oldest_pending_at": None, "stalled_pending": 0}
            )
            if not alive:
                state = "worker_unavailable"
            elif not ready:
                state = "capability_absent"
            elif active.get(queue, 0):
                state = "processing"
            elif progress["eligible"] and capacity_busy:
                state = "waiting_capacity"
            elif progress["stalled_pending"]:
                state = "stalled"
            elif progress["eligible"]:
                state = "queued"
            elif queue in recovery:
                state = "lease_recovery_wait"
            elif queue_counts[queue].get("retry_wait", 0):
                state = "retry_wait"
            else:
                state = "idle"
            queues.append(
                {
                    "queue": queue,
                    "state": state,
                    "process_alive": alive,
                    "capability_ready": ready,
                    "counts": queue_counts[queue],
                    "active": active.get(queue, 0),
                    "awaiting_lease_expiry": recovery.get(queue, (0, None))[0],
                    "next_lease_expiry_at": recovery[queue][1].isoformat()
                    if queue in recovery
                    else None,
                    **progress,
                }
            )
        return {
            "observed_at": instant.isoformat(),
            "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
            "stall_seconds": self.settings.worker_stall_seconds,
            "scope": "authorized_source_versions",
            "workers": worker_items,
            "queues": queues,
            "truncated": len(workers) == 100,
        }
