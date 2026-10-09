"""Standalone worker runtime; installed and deployed without the API."""

import argparse
import json
import os
import time

from wks_core.bootstrap import build

from wks_worker.capabilities import available_queues, validate_queues
from wks_worker.worker import Worker


def main(profile=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queues",
        default=profile or os.environ.get("WKS_WORKER_QUEUES") or ",".join(available_queues()),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    if args.version:
        from wks_core.version import build_info

        print(json.dumps(build_info()))
        return
    queues = validate_queues(tuple(args.queues.split(",")))
    if profile and queues != (profile,):
        parser.error("This executable consumes only its own queue")
    service, engine = build()
    try:
        worker = Worker(service, queues=queues)
        if args.reconcile:
            print(worker.reconcile())
            return
        reconciled = 0
        while True:
            if time.monotonic() - reconciled >= 60:
                worker.reconcile()
                reconciled = time.monotonic()
            worked = worker.run_once()
            if args.once:
                break
            if not worked:
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
