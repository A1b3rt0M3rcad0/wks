"""Functional HTTP receipt/upload/status/search/read/original validation."""

import argparse
import hashlib
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    headers = {"Authorization": "Bearer " + Path(args.token_file).read_text().strip()}
    prefix = "smoke-" + str(uuid4())
    with httpx.Client(base_url=args.url, headers=headers, timeout=30) as client:

        def post(path, body=None, key="request"):
            r = client.post(path, json=body, headers={"Idempotency-Key": prefix + "-" + key})
            r.raise_for_status()
            return r.json()

        client.get("/health/ready").raise_for_status()
        ns = post("/v1/namespaces", {"title": "Smoke acceptance", "external_ref": prefix}, "ns")
        raw = "# Circuitos\nResistências em paralelo: tensão igual.".encode()
        receipt = post(
            "/v1/sources",
            {
                "namespace_id": ns["id"],
                "kind": "upload",
                "title": "Acceptance reference",
                "upload": {
                    "filename": "reference.md",
                    "declared_media_type": "text/markdown",
                    "byte_size": len(raw),
                    "checksum_sha256": hashlib.sha256(raw).hexdigest(),
                },
            },
            "source",
        )
        client.put(f"/v1/uploads/{receipt['upload_id']}/content", content=raw).raise_for_status()
        committed = post(f"/v1/uploads/{receipt['upload_id']}/commit", key="commit")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = client.get(f"/v1/operations/{committed['operation_id']}").json()
            if status["processing_state"] in {"succeeded", "partial"}:
                break
            if status["processing_state"] == "failed":
                raise RuntimeError(status["error_code"])
            time.sleep(0.5)
        else:
            raise RuntimeError("Processing readiness timeout")
        result = post(
            "/v1/search",
            {"query": "resistencia paralelo", "filters": {"source_ids": [receipt["source_id"]]}},
            "search",
        )
        assert result["items"] and result["index_kind"] == "lexical_postgresql_fts"
        rid = result["items"][0]["representation_id"]
        assert client.get(f"/v1/representations/{rid}/read").json()["blocks"]
        original = client.get(
            f"/v1/sources/{receipt['source_id']}/versions/{receipt['source_version_id']}/original"
        )
        assert original.content == raw
        # Clean only the source created by this smoke run; namespace remains an audit boundary.
        client.delete(
            f"/v1/sources/{receipt['source_id']}", headers={"Idempotency-Key": prefix + "-delete"}
        ).raise_for_status()
        assert client.get(f"/v1/sources/{receipt['source_id']}").status_code == 404
        print(
            "PASS: readiness, stream upload, SHA, durable processing, lexical search, read, original, purge"
        )


if __name__ == "__main__":
    main()
