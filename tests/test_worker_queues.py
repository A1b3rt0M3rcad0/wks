"""Real queue isolation, lease recovery and independent package boundaries."""

import ast
import importlib.util
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import register_text, upload
from sqlalchemy import select
from wks_core.storage.database import ProcessingRun, now
from wks_worker.worker import Worker


def test_profiles_claim_only_their_own_jobs_and_preserve_other_attempts(env):
    native = register_text(env)
    env["s"].settings.extraction_profile = "docling"
    docling = upload(
        env,
        "tests/fixtures/circuit.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    media = upload(env, "tests/fixtures/speech.wav", "audio/wav", key="audio")
    docling_worker = Worker(env["s"], queues=("docling",))
    lease = docling_worker.claim()
    assert lease["id"] == docling["operation_id"] and lease["queue"] == "docling"
    assert docling_worker.claim() is None
    # A dead lease remains reserved for the same processor type.
    with env["s"].sessions.begin() as db:
        db.get(ProcessingRun, lease["id"]).deadline = now() - timedelta(seconds=1)
    assert Worker(env["s"], queues=("native",)).run_once()
    assert Worker(env["s"], queues=("native",)).claim() is None
    media_worker = Worker(env["s"], queues=("media",))
    assert media_worker.run_once()
    assert media_worker.claim() is None
    with env["s"].sessions() as db:
        rows = {r.id: r for r in db.scalars(select(ProcessingRun)).all()}
        assert rows[native["operation_id"]].attempt == 1
        assert rows[media["operation_id"]].attempt == 1
        assert rows[docling["operation_id"]].attempt == 1
    replacement = docling_worker.claim()
    assert replacement["id"] == lease["id"] and replacement["generation"] > lease["generation"]
    assert not docling_worker.heartbeat(lease)


def test_missing_processor_package_fails_before_claiming(env, monkeypatch):
    import wks_worker.capabilities as capabilities

    register_text(env)
    original = importlib.util.find_spec
    monkeypatch.setattr(
        capabilities,
        "find_spec",
        lambda name: None if name == "wks_worker_docling" else original(name),
    )
    with pytest.raises(ValueError, match="Install"):
        Worker(env["s"], queues=("docling",))
    with env["s"].sessions() as db:
        run = db.scalar(select(ProcessingRun))
        assert run.status == "queued" and run.attempt == 0


def test_api_and_core_have_no_worker_or_decoder_dependencies():
    for package in ("wks-core", "wks-api"):
        manifest = tomllib.loads((Path("packages") / package / "pyproject.toml").read_text())
        assert not any("wks-worker" in dep for dep in manifest["project"]["dependencies"])
        for file in (Path("packages") / package / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text())):
                names = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else (
                        [name.name for name in node.names] if isinstance(node, ast.Import) else []
                    )
                )
                assert not any(
                    (name or "").startswith(
                        ("wks_worker", "docling", "faster_whisper", "PIL", "pypdf", "trafilatura")
                    )
                    for name in names
                ), file
    for package in ("wks-worker", "wks-worker-native", "wks-worker-docling", "wks-worker-media"):
        for file in (Path("packages") / package / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text())):
                names = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else (
                        [name.name for name in node.names] if isinstance(node, ast.Import) else []
                    )
                )
                assert not any(
                    (name or "").startswith(("wks_api", "fastapi", "mcp", "uvicorn"))
                    for name in names
                ), file
