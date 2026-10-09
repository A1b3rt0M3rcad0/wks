import argparse
import hashlib
import os
import secrets
import time
from pathlib import Path

from sqlalchemy import select, text
from wks_core.storage.database import Client
from wks_core.worker import Worker

from wks_api.server.bootstrap import build


def provision(service, name, role, token_file, audience="wks"):
    path = Path(token_file)
    with service.sessions.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
        existing = db.scalar(select(Client).where(Client.name == name))
        if existing:
            if (
                not path.is_file()
                or hashlib.sha256(path.read_text().strip().encode()).hexdigest()
                != existing.token_hash
            ):
                raise RuntimeError(
                    "Client exists; original token file must be retained or explicitly rotate the client"
                )
            return existing.id
        token = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        client = Client(
            name=name,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            role=role,
            audience=audience,
        )
        db.add(client)
        db.flush()
        return client.id


def main():
    parser = argparse.ArgumentParser(prog="wks")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api")
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    sub.add_parser("reconcile")
    sub.add_parser("reindex")
    sub.add_parser("garbage-collect")
    for command in ("backup", "restore"):
        maintenance = sub.add_parser(command)
        maintenance.add_argument("--directory", required=True)
        maintenance.add_argument("--pg-container")
    client = sub.add_parser("provision-client")
    client.add_argument("--name", required=True)
    client.add_argument("--role", choices=["client", "executor"], default="client")
    client.add_argument("--audience", default="wks")
    client.add_argument("--token-file", required=True)
    args = parser.parse_args()
    service, engine = build()
    if args.command == "api":
        import uvicorn

        from wks_api.http.app import create_app

        uvicorn.run(
            create_app(service, engine),
            host=service.settings.http_host,
            port=service.settings.http_port,
            access_log=False,
        )
    elif args.command == "worker":
        w = Worker(service)
        if args.once:
            w.run_once()
        else:
            reconciled_at = 0
            while True:
                if time.monotonic() - reconciled_at > 60:
                    w.reconcile()
                    reconciled_at = time.monotonic()
                if not w.run_once():
                    time.sleep(1)
    elif args.command == "reconcile":
        import json

        print(json.dumps(Worker(service).reconcile()))
    elif args.command == "provision-client":
        print(provision(service, args.name, args.role, args.token_file, args.audience))
    elif args.command in {"backup", "restore", "reindex", "garbage-collect"}:
        import json

        from wks_core.storage import maintenance

        method = getattr(maintenance, args.command.replace("garbage-collect", "garbage_collect"))
        result = (
            method(service)
            if args.command in {"reindex", "garbage-collect"}
            else method(service, args.directory, args.pg_container)
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
