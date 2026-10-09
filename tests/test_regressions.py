"""Regression checks for interrupted deletion and snapshots across namespaces."""

import httpx
import pytest
from conftest import process_all, register_text
from sqlalchemy import select
from wks_core.domain.models import Error
from wks_core.storage.database import Source
from wks_core.storage.enrichment import HTTPMediaEnricher
from wks_worker.worker import Worker


def test_development_cursor_key_is_atomic_across_process_starts(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from wks_core.application.service import Service

    monkeypatch.chdir(env["tmp"])
    settings = env["s"].settings.model_copy(update={"cursor_secret": ""})

    def start(_):
        return Service(settings, env["s"].sessions, env["s"].store).cursor_key

    with ThreadPoolExecutor(max_workers=16) as pool:
        keys = list(pool.map(start, range(32)))
    assert len(set(keys)) == 1 and len(keys[0]) == 64


def test_docling_pdf_in_bounded_worker(env):
    from pathlib import Path

    from conftest import upload
    from wks_core.storage.database import Representation

    pytest.importorskip("docling")
    root = Path(".local/models/docling").resolve()
    if not (root / "wks-model-manifest.json").exists():
        pytest.skip("Pinned Docling PDF models are not installed")
    s = env["s"]
    s.settings.docling_artifacts_path = str(root)
    s.settings.extraction_profile = "docling"
    s.settings.processing_timeout_seconds = 180
    r = upload(env, Path("tests/fixtures/mixed.pdf"), "application/pdf")
    process_all(env)
    status = s.status(env["a"], r["operation_id"])
    assert status["published_representation_id"], status
    with s.sessions() as db:
        representation = db.get(Representation, status["published_representation_id"])
        assert representation.producer["name"] == "docling", representation.warnings


def test_snapshot_excludes_late_publication_in_other_owned_namespace(env):
    s, a = env["s"], env["a"]
    second = s.namespace_create(a, "second-ns", {"title": "Second"})["id"]
    for i in range(3):
        register_text(env, key=str(i))
    process_all(env)
    body = {"query": "paralelo", "page_size": 1}
    first = s.search(a, body)
    late = s.register(
        a,
        "late-other-ns",
        {"namespace_id": second, "kind": "text", "title": "Late", "text": "paralelo"},
    )
    process_all(env)
    items = first["items"]
    cursor = first["next_cursor"]
    while cursor:
        page = s.search(a, body | {"cursor": cursor})
        items += page["items"]
        cursor = page["next_cursor"]
    assert len(items) == 3
    assert late["source_id"] not in {item["source_id"] for item in items}


def test_delete_interrupted_expurgo_is_denied_and_reconciled(env, monkeypatch):
    s = env["s"]
    r = register_text(env)
    process_all(env)
    delete = s.store.delete
    monkeypatch.setattr(
        s.store, "delete", lambda key: (_ for _ in ()).throw(OSError("interrupted"))
    )
    with pytest.raises(OSError):
        s.revoke(env["a"], "delete", r["source_id"], purge=True)
    with pytest.raises(Error):
        s.source_get(env["a"], r["source_id"])
    with s.sessions() as db:
        src = db.scalar(select(Source).where(Source.id == r["source_id"]))
        assert src.deleted_at and src.state == "revoked"
    monkeypatch.setattr(s.store, "delete", delete)
    Worker(s).reconcile()
    assert s.revoke(env["a"], "delete", r["source_id"], purge=True)["state"] == "deleted"
    with s.sessions() as db:
        assert db.get(Source, r["source_id"]).state == "deleted"
    assert s.search(env["a"], {"query": "paralelo"})["items"] == []


@pytest.mark.parametrize("payload", [b"x" * 20000, b"[]", b'{"description":"ok","producer":{}}'])
def test_enrichment_response_is_bounded_and_validated(env, monkeypatch, payload):
    client_class = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client_class(transport=transport, **kwargs)
    )
    settings = env["s"].settings.model_copy(
        update={"enrichment_endpoint": "https://provider.example.test/enrich"}
    )
    with pytest.raises(Error, match="unavailable"):
        HTTPMediaEnricher(settings).enrich(b"png", "image/png", "operation")
