import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, delete, text
from wks_api.authentication.models import Account, AuthAttempt
from wks_api.authentication.passwords import (
    password_hash,
    password_matches,
    recovery_token,
    token_hash,
)
from wks_api.server.bootstrap import build
from wks_api.server.cli import provision
from wks_api.server.config import Settings
from wks_worker.worker import Worker

base = Settings().database_url.rsplit("/", 1)[0]
url = base + "/wks_browser_test"
admin = create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    if not c.scalar(text("SELECT 1 FROM pg_database WHERE datname='wks_browser_test'")):
        c.execute(text("CREATE DATABASE wks_browser_test"))
subprocess.run(
    [".venv/bin/alembic", "upgrade", "head"], env=os.environ | {"WKS_DATABASE_URL": url}, check=True
)
s, e = build(
    Settings(database_url=url, storage_path=Path(".local/browser-objects"), ocr_language="eng")
)
provision(s, "Workspace de engenharia", "client", ".local/browser-token")
p = s.authenticate(Path(".local/browser-token").read_text())
account_file = Path(".local/browser-account.json")
if not account_file.exists():
    fd = os.open(account_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(
            {"username": "workspace-engineering", "password": secrets.token_urlsafe(32)}, file
        )
credentials = json.loads(account_file.read_text())
with s.sessions.begin() as db:
    db.execute(delete(AuthAttempt))
    account = db.get(Account, p.client_id)
    if account is None:
        db.add(
            Account(
                client_id=p.client_id,
                username=credentials["username"],
                password_hash=password_hash(credentials["password"]),
                recovery_token_hash=token_hash(recovery_token()),
            )
        )
    elif not password_matches(credentials["password"], account.password_hash):
        raise RuntimeError("Browser fixture account and private credential file differ")

ns = s.namespace_create(
    p, "seed-context", {"title": "Conhecimento de engenharia", "external_ref": "wks-browser-seed"}
)["id"]
# Reconcile only data created by these disposable browser journeys.
for source in s.list_sources(p, {"namespace_id": ns}, 100)["items"]:
    if source["title"].startswith(("Teste da biblioteca ", "Arquivo da jornada ")):
        s.revoke(p, "cleanup-" + source["id"], source["id"], purge=True)
for i in range(22):
    s.register(
        p,
        f"seed-{i}",
        {
            "namespace_id": ns,
            "kind": "text",
            "title": f"{'Circuitos em paralelo' if i == 0 else 'Referência de engenharia'} · {i + 1:02d}",
            "description": "Fundamentos, passagens e referências para consultar.",
            "text": f"# Circuitos em paralelo\nResistências em paralelo têm a mesma tensão elétrica. Referência {i + 1}.\n# Cálculo\nA corrente total é a soma das correntes dos ramos.\n# Segurança\nConfira a origem e os limites da medição.",
        },
    )
for i in range(2):
    s.register(
        p,
        f"bookmark-{i}",
        {
            "namespace_id": ns,
            "kind": "bookmark",
            "title": f"Leitura complementar · {i + 1}",
            "external_uri": "https://example.com/reference",
            "description": "Link preservado, sem captura do endereço.",
        },
    )
for filename, mime, key in [
    ("mixed.pdf", "application/pdf", "pdf"),
    ("circuit.png", "image/png", "image"),
]:
    raw = (Path("tests/fixtures") / filename).read_bytes()
    r = s.register(
        p,
        key,
        {
            "namespace_id": ns,
            "kind": "upload",
            "title": "Manual de circuitos" if key == "pdf" else "Diagrama de circuitos",
            "upload": {
                "filename": filename,
                "declared_media_type": mime,
                "byte_size": len(raw),
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            },
        },
    )
    path = Path("tests/fixtures") / filename
    s.receive(p, r["upload_id"], path)
    s.commit(p, key + "-commit", r["upload_id"])
worker = Worker(s)
while worker.run_once():
    pass
Path(".local/browser-namespace").write_text(ns)
print("Browser fixtures ready: private client, isolated database and 26 real sources")

# A separate, disposable database exercises the genuinely empty first-account flow.
# Never reset the application or the engineering fixture database.
account_db = "wks_accounts_browser_test"
with admin.connect() as connection:
    if not connection.scalar(
        text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": account_db}
    ):
        connection.execute(text("CREATE DATABASE wks_accounts_browser_test"))
subprocess.run(
    [".venv/bin/alembic", "upgrade", "head"],
    env=os.environ | {"WKS_DATABASE_URL": base + "/" + account_db},
    check=True,
)
account_engine = create_engine(base + "/" + account_db)
with account_engine.begin() as connection:
    from wks_core.storage.database import Base

    tables = ",".join('"' + name + '"' for name in Base.metadata.tables)
    connection.execute(text("TRUNCATE " + tables + " CASCADE"))
account_engine.dispose()
admin.dispose()
e.dispose()
print("First-account browser database is empty and ready")
