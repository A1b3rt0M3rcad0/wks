"""Low-cardinality worker measurements; no source, tenant or credential labels."""

from prometheus_client import Counter, Histogram

OUTCOMES = Counter(
    "wks_worker_outcomes_total", "Final worker attempt outcomes", ["queue", "status"]
)
DURATION = Histogram("wks_worker_attempt_seconds", "Worker attempt time", ["queue"])


def observe(queue, status, seconds):
    OUTCOMES.labels(queue, status).inc()
    DURATION.labels(queue).observe(seconds)
