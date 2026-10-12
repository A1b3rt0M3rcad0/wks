"""CLI and SDK consumer conformance over the real public HTTP gateway."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from test_mcp import server as server_fixture
from wks_sdk import Client
from wks_worker.worker import Worker


@pytest.fixture
def server(env):
    yield from server_fixture.__wrapped__(env)


@pytest.fixture
def cli():
    binary = Path(".local/stabilization/wksctl").resolve()
    if not binary.is_file():
        subprocess.run([str(Path(".venv/bin/python")), "scripts/build_clients.py"], check=True)
    return binary


@pytest.fixture
def processing(env):
    stop = threading.Event()
    worker = Worker(env["s"], queues=("native",))

    def run():
        while not stop.is_set():
            if not worker.run_once():
                stop.wait(0.05)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=90)
    assert not thread.is_alive()


def invoke(cli, config, *args, input=None):
    result = subprocess.run(
        [str(cli), *args, "--config", str(config)],
        input=input,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cli_real_auth_create_read_export_and_declarative_sync(env, server, cli, processing):
    root = env["tmp"] / "project"
    root.mkdir()
    config = env["tmp"] / "contexts.json"
    login = invoke(
        cli, config, "auth", "login", "--token-stdin", "--endpoint", server, input=env["a_token"]
    )
    assert login["authenticated"]
    assert env["a_token"] not in json.dumps(login)
    assert invoke(cli, config, "doctor")["health"]["status"] == "ready"
    namespace = invoke(
        cli, config, "namespace", "create", "--title", "CLI namespace", "--key", "cli-namespace"
    )["id"]
    invoke(cli, config, "context", "set", "--namespace", namespace)
    created = invoke(
        cli,
        config,
        "source",
        "create",
        "--title",
        "Consumer text",
        "--text",
        "Independent public client lexical resistance.",
        "--key",
        "cli-text",
    )
    operation = invoke(cli, config, "operation", "wait", "--id", created["operation_id"])
    rid = operation["published_representation_id"]
    assert invoke(cli, config, "search", "--query", "resistance", "--language", "auto")["items"]
    assert invoke(cli, config, "read", "--id", rid)["blocks"]
    export = invoke(
        cli,
        config,
        "export",
        "create",
        "--id",
        rid,
        "--format",
        "plain-text",
        "--key",
        "cli-export",
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = invoke(cli, config, "export", "get", "--id", export["id"])
        if status["status"] == "ready":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Public export did not complete")
    destination = root / "export.txt"
    assert invoke(
        cli, config, "export", "download", "--id", export["id"], "--output", str(destination)
    )["saved"]
    assert "Independent public client" in destination.read_text()
    invoke(cli, config, "init", "--directory", str(root), "--agents")
    manifest = root / ".wks/manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "namespace": {"title": "CLI namespace", "external_ref": "cli-project"},
                "sources": [
                    {
                        "external_ref": "lesson",
                        "title": "Lesson",
                        "kind": "text",
                        "text": "Manifest source content",
                        "metadata": {"category": "lesson"},
                    }
                ],
                "collections": [
                    {"external_ref": "lessons", "title": "Lessons", "sources": ["lesson"]}
                ],
            }
        )
    )
    assert invoke(cli, config, "plan", "--manifest", str(manifest))["mutated"] is False
    applied = invoke(cli, config, "apply", "--manifest", str(manifest), "--yes")
    repeated = invoke(cli, config, "sync", "--manifest", str(manifest), "--yes")
    assert applied["sources_applied"] == repeated["sources_applied"] == 1
    sources = env["s"].list_sources(env["a"], {"namespace_id": namespace})["items"]
    lesson = [source for source in sources if source["external_ref"] == "lesson"]
    assert len(lesson) == 1 and len(lesson[0]["versions"]) == 1
    declaration = json.loads(manifest.read_text())
    declaration["sources"][0]["text"] = "Changed manifest source content"
    declaration["collections"][0]["title"] = "Renamed lessons"
    manifest.write_text(json.dumps(declaration))
    invoke(cli, config, "sync", "--manifest", str(manifest), "--yes")
    changed = invoke(cli, config, "source", "get", "--id", lesson[0]["id"])
    assert len(changed["versions"]) == 2
    declaration["sources"][0]["text"] = "Manifest source content"
    manifest.write_text(json.dumps(declaration))
    invoke(cli, config, "sync", "--manifest", str(manifest), "--yes")
    reverted = invoke(cli, config, "source", "get", "--id", lesson[0]["id"])
    assert len(reverted["versions"]) == 3
    tests = root / ".wks/tests.yaml"
    tests.write_text(
        "schema_version: 1\nfixtures:\n  - name: physics\n    text: Electrical resistance in a parallel circuit.\nqueries:\n  - name: lexical\n    query: resistance\n    language: simple\n    expected_sources: [physics]\n"
    )
    tested = invoke(cli, config, "test", "--tests", str(tests))
    assert tested["passed"] == 1 and tested["failed"] == 0 and tested["isolated"]
    import hashlib

    original = b"Resumable CLI original.\n" * 10000
    upload_file = root / "large.txt"
    upload_file.write_bytes(original)
    prepared = env["s"].register(
        env["a"],
        "cli-resume-source",
        {
            "namespace_id": namespace,
            "kind": "upload",
            "title": "CLI resumed",
            "upload": {
                "filename": "large.txt",
                "declared_media_type": "text/plain",
                "byte_size": len(original),
                "checksum_sha256": hashlib.sha256(original).hexdigest(),
            },
        },
    )
    part = root / "first-part"
    part.write_bytes(original[:65536])
    env["s"].receive_part(
        env["a"], prepared["upload_id"], part, 0, hashlib.sha256(original[:65536]).hexdigest()
    )
    resumed = invoke(
        cli,
        config,
        "upload",
        "resume",
        "--id",
        prepared["upload_id"],
        "--file",
        str(upload_file),
        "--key",
        "cli-resume-commit",
    )
    assert resumed["source_version_id"] == prepared["source_version_id"]
    status = invoke(cli, config, "upload", "status", "--id", prepared["upload_id"])
    assert status["status"] == "committed" and status["offset"] == len(original)


def test_python_sdk_real_resumable_transfer_and_receipt(env, server, processing):
    client = Client(server, env["a_token"])
    raw = b"Streaming standalone SDK original." * 10000
    import hashlib
    import io

    registered = client.call(
        "POST",
        "/v1/sources",
        {
            "namespace_id": env["ns_a"],
            "title": "SDK upload",
            "kind": "upload",
            "upload": {
                "filename": "sdk.txt",
                "byte_size": len(raw),
                "declared_media_type": "text/plain",
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            },
        },
        key="sdk-upload",
    )
    client.resume_upload(registered["upload_id"], io.BytesIO(raw), chunk_bytes=128 * 1024)
    committed = client.call(
        "POST", f"/v1/uploads/{registered['upload_id']}/commit", key="sdk-commit"
    )
    assert client.receipt("sdk-commit").source_id == committed["source_id"]
    operation = client.wait_operation(committed["operation_id"], deadline_seconds=90)
    assert operation.processing_state == "succeeded"
    destination = io.BytesIO()
    client.download(
        f"/v1/sources/{registered['source_id']}/versions/{registered['source_version_id']}/original",
        destination,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert destination.getvalue() == raw


def test_go_sdk_live_gateway_without_server_imports(env, server, processing):
    import shutil

    toolchain = Path(".local/toolchains/go/bin/go").resolve()
    go = str(toolchain) if toolchain.is_file() else shutil.which("go")
    assert go, "A verified Go toolchain is required for live SDK conformance"
    configuration = os.environ | {
        "GOPATH": os.environ.get("GOPATH", str(Path(".local/go-path").resolve())),
        "GOCACHE": os.environ.get("GOCACHE", str(Path(".local/go-build").resolve())),
        "GOMODCACHE": os.environ.get("GOMODCACHE", str(Path(".local/go-mod").resolve())),
        "WKS_SDK_TEST_ENDPOINT": server,
        "WKS_SDK_TEST_TOKEN": env["a_token"],
        "WKS_SDK_TEST_NAMESPACE": env["ns_a"],
    }
    result = subprocess.run(
        [go, "test", "-run", "^TestLivePublicGateway$", "-count=1", "./..."],
        cwd="packages/wks-sdk-go",
        env=configuration,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_qrels_are_evaluated_through_the_public_sdk(env, server, processing):
    import runpy

    evaluate = runpy.run_path("scripts/evaluate_search.py")["evaluate"]
    corpus = json.loads(Path("tests/fixtures/stabilization/search-corpus.json").read_text())
    client = Client(server, env["a_token"])
    namespace = client.call(
        "POST", "/v1/namespaces", {"title": "Synthetic relevance corpus"}, key="qrels-namespace"
    )["id"]
    identities = {}
    for document in corpus["documents"]:
        receipt = client.call(
            "POST",
            "/v1/sources",
            {
                "namespace_id": namespace,
                "kind": "text",
                "title": document["title"],
                "text": document["text"],
            },
            key="qrels-" + document["id"],
        )
        client.wait_operation(receipt["operation_id"], deadline_seconds=90)
        identities[document["id"]] = receipt["source_id"]
    cases = [
        {
            **query,
            "relevance": {identities[name]: grade for name, grade in query["relevance"].items()},
            "filters": {"namespace_id": namespace},
        }
        for query in corpus["queries"]
    ]
    report = evaluate(client, cases)
    report["dataset"] = corpus["dataset"]
    assert report["query_count"] == 4
    assert report["mean_recall_at_k"] == report["mean_ndcg_at_k"] == 1
    output = Path(".local/stabilization/search-evaluation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
