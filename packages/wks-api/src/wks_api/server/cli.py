import argparse
import hashlib
import os
import secrets
from pathlib import Path

from sqlalchemy import select, text
from wks_core.storage.database import Client

from wks_api.server.bootstrap import build
from wks_api.version import VERSION, build_info


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
        from wks_core.storage.stabilization import ClientCredential

        db.add(
            ClientCredential(
                client_id=client.id,
                token_hash=client.token_hash,
                description="initial provisioned credential",
            )
        )
        return client.id


def main():
    parser = argparse.ArgumentParser(prog="wks")
    parser.add_argument("--version", action="version", version="WKS " + VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api")
    sub.add_parser("version")
    sub.add_parser("reindex")
    gc = sub.add_parser("garbage-collect")
    gc.add_argument("--page-size", type=int, default=100)
    gc.add_argument("--cursor")
    audit = sub.add_parser("audit-integrity")
    audit.add_argument("--page-size", type=int, default=100)
    audit.add_argument("--cursor")
    audit.add_argument("--size-only", action="store_true")
    migrate = sub.add_parser("migrate-storage")
    migrate.add_argument("--destination", choices=["filesystem", "s3"], required=True)
    migrate.add_argument("--directory")
    migrate.add_argument("--bucket")
    migrate.add_argument("--endpoint")
    migrate.add_argument("--region", default="us-east-1")
    migrate.add_argument("--cursor")
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
    if args.command == "version":
        import json

        print(json.dumps(build_info()))
        return
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
    elif args.command == "provision-client":
        print(provision(service, args.name, args.role, args.token_file, args.audience))
    elif args.command == "migrate-storage":
        import json

        from wks_core.storage.migration import migrate
        from wks_core.storage.storage import FilesystemStore, S3Store

        if args.destination == "filesystem":
            if not args.directory:
                parser.error("Filesystem destination requires --directory")
            destination = FilesystemStore(Path(args.directory))
        else:
            if not args.bucket:
                parser.error("S3 destination requires --bucket")
            destination = S3Store(
                service.settings.model_copy(
                    update={
                        "s3_bucket": args.bucket,
                        "s3_endpoint": args.endpoint,
                        "s3_region": args.region,
                    }
                )
            )
        print(json.dumps(migrate(service, destination, cursor=args.cursor)))
    elif args.command == "audit-integrity":
        import json

        from wks_core.storage.integrity import audit

        print(
            json.dumps(
                audit(
                    service, page_size=args.page_size, cursor=args.cursor, verify=not args.size_only
                )
            )
        )
    elif args.command in {"backup", "restore", "reindex", "garbage-collect"}:
        import json

        from wks_core.storage import maintenance

        method = getattr(maintenance, args.command.replace("garbage-collect", "garbage_collect"))
        result = (
            method(service, args.page_size, args.cursor)
            if args.command == "garbage-collect"
            else method(service)
            if args.command == "reindex"
            else method(service, args.directory, args.pg_container)
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
