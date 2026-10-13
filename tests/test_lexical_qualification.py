"""Judged lexical retrieval in a larger synthetic corpus, without semantic claims."""

import importlib.util
import json
from pathlib import Path

from wks_core.domain.models import Block, ExtractedRepresentation
from wks_worker.worker import Worker


def test_judged_corpus_recall_literal_synonyms_and_isolation(env):
    dataset = json.loads(Path("tests/fixtures/stabilization/search-qualification.json").read_text())
    worker = Worker(env["s"], queues=("native",))
    identities = {}
    documents = dataset["documents"] + [
        {
            "id": f"noise-{i}",
            "title": f"Arquivo neutro {i}",
            "text": f"Registro neutro de manutenção número {i}.",
        }
        for i in range(200)
    ]
    # Original registration uses the public API. Extraction is synthetic in this
    # retrieval test; real decoder/format coverage has its own processor matrix.
    for document in documents:
        response = env["http"].post(
            "/v1/sources",
            json={
                "namespace_id": env["ns_a"],
                "kind": "text",
                "title": document["title"],
                "text": document["text"],
                "metadata": {"declared_language": document.get("language", "pt")},
            },
            headers=env["headers"] | {"Idempotency-Key": document["id"]},
        )
        assert response.status_code == 201
        identities[document["id"]] = response.json()["source_id"]
        lease = worker.claim()
        assert lease["id"] == response.json()["operation_id"]
        worker.publish(
            lease,
            ExtractedRepresentation(
                blocks=[
                    Block(
                        "paragraph",
                        document["text"],
                        {"kind": "line_range", "start_line": 1, "end_line": 1},
                    )
                ],
                processor_name="synthetic-retrieval-fixture",
                coverage={"text": {"state": "complete"}},
            ),
        )
    hidden = env["http"].post(
        "/v1/sources",
        json={
            "namespace_id": env["ns_b"],
            "kind": "text",
            "title": "Hidden",
            "text": "carro automóvel intercorrências calor resistência paralelo",
        },
        headers={"Authorization": "Bearer " + env["b_token"], "Idempotency-Key": "hidden"},
    )
    assert hidden.status_code == 201
    lease = worker.claim()
    worker.publish(
        lease,
        ExtractedRepresentation(
            blocks=[
                Block(
                    "paragraph",
                    "carro automóvel intercorrências calor resistência paralelo",
                    {"kind": "line_range", "start_line": 1, "end_line": 1},
                )
            ],
            processor_name="synthetic-retrieval-fixture",
        ),
    )
    cases = [
        {**case, "relevance": {identities[key]: grade for key, grade in case["relevance"].items()}}
        for case in dataset["queries"]
    ]
    spec = importlib.util.spec_from_file_location(
        "evaluate_search", Path("scripts/evaluate_search.py")
    )
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)

    class Client:
        def call(self, method, path, body):
            response = env["http"].request(method, path, json=body, headers=env["headers"])
            assert response.status_code == 200
            result = response.json()
            assert all(hit["source_id"] != hidden.json()["source_id"] for hit in result["items"])
            return result

    report = evaluator.evaluate(Client(), cases)
    assert report["mean_recall_at_k"] == 1, report["queries"]
    assert report["mean_ndcg_at_k"] == 1
    literal = Client().call("POST", "/v1/search", {"query": "carro", "language": "pt"})
    assert {hit["source_id"] for hit in literal["items"]} == {identities["car-literal"]}
    output = Path(".local/audit-2026-10-12/search-quality.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            report
            | {
                "dataset": dataset["dataset"],
                "documents": len(documents),
                "private_documents": 1,
                "extraction": "synthetic canonical blocks; decoder matrix qualified separately",
            },
            indent=2,
        )
        + "\n"
    )
