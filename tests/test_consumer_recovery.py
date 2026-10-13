"""Large public upload recovery and stable paginated snapshots during reprocessing."""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from test_mcp import server as server_fixture
from wks_core.domain.models import Error, ExtractedRepresentation
from wks_core.storage.database import ProcessingRun, now
from wks_sdk import Client, WksError
from wks_worker.worker import Worker


@pytest.fixture
def server(env):
    yield from server_fixture.__wrapped__(env)


class LostAcknowledgement:
    def __init__(self, delegate):
        self.delegate = delegate
        self.lost = False

    def open(self, request, **options):
        response = self.delegate.open(request, **options)
        if request.get_method() != "PUT" or "/parts?" not in request.full_url or self.lost:
            return response
        self.lost = True

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                response.close()

            def read(self, size):
                response.read(size)  # The real server already persisted the part.
                raise TimeoutError("Injected client-side acknowledgement loss")

        return Response()


def test_large_http_upload_recovers_lost_ack_and_commit_receipt(env, server):
    chunk = b"Large original, lexical recovery qualification.\n" * 2048
    raw = chunk * 90  # More than 8 MiB, without storing a customer's document.
    client = Client(server, env["a_token"], timeout=120)
    receipt = client.call(
        "POST",
        "/v1/sources",
        {
            "namespace_id": env["ns_a"],
            "kind": "upload",
            "title": "Large original",
            "upload": {
                "filename": "large.txt",
                "declared_media_type": "text/plain",
                "byte_size": len(raw),
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            },
        },
        key="large-register",
    )
    client._opener = LostAcknowledgement(client._opener)
    with pytest.raises(WksError) as lost:
        client.resume_upload(receipt["upload_id"], io.BytesIO(raw), chunk_bytes=1024**2)
    assert lost.value.write_outcome == "unknown"
    resumed = Client(server, env["a_token"], timeout=120)
    confirmed = resumed.call("GET", "/v1/uploads/" + receipt["upload_id"])
    assert confirmed["offset"] == 1024**2
    progress = []
    resumed.resume_upload(
        receipt["upload_id"],
        io.BytesIO(raw),
        chunk_bytes=1024**2,
        progress=lambda offset, size: progress.append(offset),
    )
    assert progress[0] == 2 * 1024**2 and progress[-1] == len(raw)
    committed = resumed.call(
        "POST", "/v1/uploads/" + receipt["upload_id"] + "/commit", key="large-commit"
    )
    assert (
        resumed.call("POST", "/v1/uploads/" + receipt["upload_id"] + "/commit", key="large-commit")
        == committed
    )
    assert (
        resumed.call("GET", "/v1/receipts/large-commit")["source_version_id"]
        == receipt["source_version_id"]
    )
    sink = io.BytesIO()
    resumed.download(
        f"/v1/sources/{receipt['source_id']}/versions/{receipt['source_version_id']}/original",
        sink,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert sink.getvalue() == raw
    assert len(resumed.call("GET", "/v1/sources/" + receipt["source_id"])["versions"]) == 1


def test_timeout_recovery_and_concurrent_readers_keep_exact_representation(env, server):
    client = Client(server, env["a_token"])
    receipt = client.call(
        "POST",
        "/v1/sources",
        {
            "namespace_id": env["ns_a"],
            "kind": "text",
            "title": "Paginated document",
            "text": "\n".join(f"Paragraph {i}: lexical evidence for consumers." for i in range(95)),
        },
        key="reader-source",
    )
    worker = Worker(env["s"], queues=("native",))
    lease = worker.claim()
    worker.fail(lease, "processing.timeout", retryable=True)
    assert (
        client.call("GET", "/v1/operations/" + receipt["operation_id"])["processing_state"]
        == "retry_wait"
    )
    with env["s"].sessions.begin() as db:
        db.get(ProcessingRun, lease["id"]).next_retry_at = now() - timedelta(seconds=1)
    assert worker.run_once()
    with pytest.raises(Error):
        worker.publish(
            lease, ExtractedRepresentation()
        )  # Stale attempts can never publish over a recovered lease.
    status = client.call("GET", "/v1/operations/" + receipt["operation_id"])
    rid = status["published_representation_id"]
    first = client.call("GET", f"/v1/representations/{rid}/read?page_size=7")
    reprocess = client.call(
        "POST", f"/v1/sources/{receipt['source_id']}/process", {}, key="reader-reprocess"
    )
    assert (
        client.call(
            "POST", f"/v1/sources/{receipt['source_id']}/process", {}, key="reader-reprocess"
        )
        == reprocess
    )
    assert worker.run_once()
    new_status = client.call("GET", "/v1/operations/" + reprocess["operation_id"])
    assert new_status["published_representation_id"] != rid

    def consume(_):
        from urllib.parse import urlencode

        independent = Client(server, env["a_token"])
        page, blocks = first, []
        while True:
            assert (
                page["representation_id"] == rid
                and page["source_version_id"] == receipt["source_version_id"]
            )
            assert (
                page["coverage"] == first["coverage"]
                and page["content_trust"] == "untrusted_source_data"
            )
            blocks.extend(page["blocks"])
            if not page["next_cursor"]:
                break
            page = independent.call(
                "GET",
                f"/v1/representations/{rid}/read?"
                + urlencode({"page_size": 7, "cursor": page["next_cursor"]}),
            )
        assert [b["ordinal"] for b in blocks] == list(range(len(blocks)))
        assert len({b["id"] for b in blocks}) == len(blocks)
        return [b["id"] for b in blocks]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(consume, range(4)))
    assert all(result == results[0] for result in results) and len(results[0]) == 96
    assert (
        status["source_version_id"]
        == new_status["source_version_id"]
        == receipt["source_version_id"]
    )
    assert len(client.call("GET", "/v1/sources/" + receipt["source_id"])["versions"]) == 1
