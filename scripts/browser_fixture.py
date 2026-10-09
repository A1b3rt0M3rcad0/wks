import hashlib
import os
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text
from wks_api.server.bootstrap import build
from wks_api.server.cli import provision
from wks_api.server.config import Settings
from wks_core.worker import Worker

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
