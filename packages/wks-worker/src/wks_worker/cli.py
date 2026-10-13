"""Standalone worker runtime; installed and deployed without the API."""

import argparse
import json
import os
import signal
import threading
import time
from pathlib import Path
from tempfile import gettempdir

from wks_core.bootstrap import build

from wks_worker.capabilities import available_queues, validate_queues
from wks_worker.worker import Worker


def main(profile=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queues",
        default=profile or os.environ.get("WKS_WORKER_QUEUES") or ",".join(available_queues()),
    )
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument(
        "--health-file",
        default=os.environ.get("WKS_WORKER_HEALTH_FILE")
        or str(Path(gettempdir()) / ("wks-worker-" + (profile or "all") + "-health.json")),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=int(os.environ.get("WKS_WORKER_METRICS_PORT", "0")),
        help="Optional Prometheus listener on loopback; 0 disables",
    )
    args = parser.parse_args()
    if args.version:
        from wks_core.version import build_info

        print(json.dumps(build_info()))
        return
    queues = validate_queues(tuple(args.queues.split(",")))
    if profile and queues != (profile,):
        parser.error("This executable consumes only its own queue")
    if args.healthcheck:
        from wks_worker.health import probe

        try:
            service, engine = build()
            try:
                result = probe(service, args.health_file, queues)
            finally:
                engine.dispose()
        except Exception:
            result = {"liveness": "unavailable", "reason": "probe_unavailable"}
        print(json.dumps(result))
        raise SystemExit(0 if result["liveness"] == "alive" else 1)
    service, engine = build()
    metrics_server = None
    try:
        if args.metrics_port:
            if not 1 <= args.metrics_port <= 65535:
                parser.error("metrics-port must be 1..65535 or 0")
            from prometheus_client import start_http_server

            metrics_server, _ = start_http_server(args.metrics_port, addr="127.0.0.1")
        worker = Worker(service, queues=queues)
        from wks_worker.health import write_identity

        write_identity(args.health_file, worker.owner, queues)
        if args.reconcile:
            print(worker.reconcile())
            return
        stopping = threading.Event()
        for event in (signal.SIGTERM, signal.SIGINT):
            signal.signal(event, lambda *_: stopping.set())
        reconciled = 0
        while not stopping.is_set():
            if time.monotonic() - reconciled >= 60:
                worker.reconcile()
                reconciled = time.monotonic()
            worked = worker.run_once()
            if args.once:
                break
            if not worked:
                stopping.wait(1)
    finally:
        if metrics_server:
            metrics_server.shutdown()
            metrics_server.server_close()
        engine.dispose()


if __name__ == "__main__":
    main()
