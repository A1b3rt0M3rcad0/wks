"""Processor matrix exercised through registration, worker publication and full exports."""

import hashlib
from pathlib import Path

import pytest
from conftest import process_all, upload
from wks_core.domain.formats import FormatRegistry

CASES = [
    ("budget.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "=B2*2"),
    ("budget.ods", "application/vnd.oasis.opendocument.spreadsheet", "of:=10*2"),
    ("circuit.epub", "application/epub+zip", "Parallel resistance"),
    ("code.py", "text/x-python", "def resistance"),
]


@pytest.mark.parametrize("filename,mime,expected", CASES)
def test_native_formats_ingest_read_and_export_with_original_integrity(
    env, filename, mime, expected
):
    path = Path("tests/fixtures/stabilization") / filename
    registered = upload(env, path, mime)
    process_all(env)
    operation = env["s"].status(env["a"], registered["operation_id"])
    assert operation["processing_state"] == "succeeded"
    rid = operation["published_representation_id"]
    read = env["s"].read(env["a"], rid)
    assert expected in " ".join(block["text"] for block in read["blocks"])
    original = env["s"].binary(
        env["a"], sid=registered["source_id"], vid=registered["source_version_id"]
    )
    assert original["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    export = env["s"].export_create(env["a"], "full-export", rid, "structured-json")
    process_all(env)
    assert env["s"].export_get(env["a"], export["id"])["status"] == "ready"


def test_capabilities_reflect_worker_presence_and_fixture_catalogue(env):
    response = env["http"].get("/v1/capabilities", headers=env["headers"])
    assert response.status_code == 200
    assert response.json()["worker_queues"] == []
    assert not any(format["can_extract"] for format in response.json()["formats"])
    from wks_worker.worker import Worker

    Worker(env["s"], queues=("native",))
    capability = env["s"].capabilities(env["a"])
    assert capability["worker_queues"] == ["native"]
    for spec in FormatRegistry().specs:
        if spec.queue == "native":
            assert spec.fixtures
            assert next(row for row in capability["formats"] if row["mime"] == spec.mime)[
                "can_extract"
            ]
