import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from wks.bootstrap import build
from wks.cli import provision
from wks.infrastructure.database import Base
from wks.presentation.http import create_app
from wks.settings import Settings
from wks.worker import Worker


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get(
        "WKS_TEST_DATABASE_URL", "postgresql+psycopg://wks:wks-local@127.0.0.1:55432/wks_test"
    )
    # A dedicated disposable test database; never truncate the application database.
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        raise RuntimeError("Test database name must end in _test")
    admin = create_engine(url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    name = url.rsplit("/", 1)[-1]
    with admin.connect() as c:
        if not c.scalar(text("SELECT 1 FROM pg_database WHERE datname=:n"), {"n": name}):
            if not name.replace("_", "").isalnum():
                raise ValueError("Invalid test database name")
            c.execute(text(f'CREATE DATABASE "{name}"'))
    subprocess.run(
        [str(Path(".venv/bin/alembic")), "upgrade", "head"],
        env=os.environ | {"WKS_DATABASE_URL": url},
        check=True,
    )
    admin.dispose()
    return url


@pytest.fixture
def env(db_url, tmp_path):
    settings = Settings(
        database_url=db_url,
        storage_path=tmp_path / "objects",
        cursor_secret="test-signing-key-only",
        ocr_language="eng",
        processing_timeout_seconds=60,
    )
    service, engine = build(settings)
    with engine.begin() as c:
        names = ",".join('"' + table + '"' for table in Base.metadata.tables)
        c.execute(text("TRUNCATE " + names + " CASCADE"))
    a_id = provision(service, "A", "client", tmp_path / "a-token")
    b_id = provision(service, "B", "client", tmp_path / "b-token")
    executor = provision(service, "executor", "executor", tmp_path / "executor-token")
    a_token, b_token = (tmp_path / "a-token").read_text(), (tmp_path / "b-token").read_text()
    a, b = service.authenticate(a_token), service.authenticate(b_token)
    ns_a = service.namespace_create(a, "ns-a", {"title": "A", "external_ref": "A"})["id"]
    ns_b = service.namespace_create(b, "ns-b", {"title": "B", "external_ref": "B"})["id"]
    with TestClient(create_app(service, engine)) as http:
        yield {
            "s": service,
            "e": engine,
            "a": a,
            "b": b,
            "a_id": a_id,
            "b_id": b_id,
            "ns_a": ns_a,
            "ns_b": ns_b,
            "executor_id": executor,
            "executor_token": (tmp_path / "executor-token").read_text(),
            "a_token": a_token,
            "b_token": b_token,
            "http": http,
            "headers": {"Authorization": "Bearer " + a_token},
            "tmp": tmp_path,
        }
    engine.dispose()


def register_text(
    env,
    value="Resistências em paralelo; tensão elétrica.",
    title="Eletricidade",
    user="a",
    key="text",
):
    r = env["s"].register(
        env[user],
        key,
        {"namespace_id": env["ns_" + user], "kind": "text", "title": title, "text": value},
    )
    return r


def upload(env, path, mime, user="a", key="upload"):
    raw = Path(path).read_bytes()
    r = env["s"].register(
        env[user],
        key,
        {
            "namespace_id": env["ns_" + user],
            "kind": "upload",
            "title": Path(path).name,
            "upload": {
                "filename": Path(path).name,
                "declared_media_type": mime,
                "byte_size": len(raw),
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            },
        },
    )
    env["s"].receive(env[user], r["upload_id"], path)
    return env["s"].commit(env[user], key + "-commit", r["upload_id"])


def process_all(env):
    worker = Worker(env["s"])
    for _ in range(20):
        if not worker.run_once():
            return
    raise AssertionError("Worker did not drain the test queue")
