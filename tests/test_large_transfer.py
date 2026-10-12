"""Real 1 GiB byte transfer with a confirmed 50% interruption and bounded buffers."""

import hashlib

import pytest
from test_mcp import server as server_fixture
from wks_sdk import Client


@pytest.fixture
def server(env):
    yield from server_fixture.__wrapped__(env)


class DigestSink:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.count = 0

    def write(self, value):
        self.digest.update(value)
        self.count += len(value)
        return len(value)


def test_one_gib_resume_halfway_preserves_full_original(env, server):
    total = 1024**3
    part = 4 * 1024**2
    env["s"].settings.upload_max_bytes = total
    env["s"].settings.namespace_max_bytes = total
    path = env["tmp"] / "large-original.bin"
    with path.open("wb") as file:
        file.truncate(total)
    expected = hashlib.sha256()
    zeros = b"\0" * part
    for _ in range(total // part):
        expected.update(zeros)
    sha = expected.hexdigest()
    client = Client(server, env["a_token"], timeout=120)
    created = client.call(
        "POST",
        "/v1/sources",
        {
            "namespace_id": env["ns_a"],
            "kind": "upload",
            "title": "Synthetic 1 GiB transfer",
            "upload": {
                "filename": "large-original.bin",
                "declared_media_type": "application/octet-stream",
                "byte_size": total,
                "checksum_sha256": sha,
            },
        },
        key="one-gib",
    )
    uid = created["upload_id"]
    part_sha = hashlib.sha256(zeros).hexdigest()
    # Simulate the client process ending after the acknowledged midpoint.
    for offset in range(0, total // 2, part):
        with client.request(
            "PUT",
            f"/v1/uploads/{uid}/parts?offset={offset}&checksum_sha256={part_sha}",
            zeros,
            content_type="application/octet-stream",
        ) as response:
            response.read()
    assert client.call("GET", f"/v1/uploads/{uid}")["offset"] == total // 2
    resumed = Client(server, env["a_token"], timeout=120)
    progress = []
    with path.open("rb") as source:
        resumed.resume_upload(uid, source, progress=lambda offset, size: progress.append(offset))
    assert progress[0] == total // 2 + part and progress[-1] == total
    committed = resumed.call("POST", f"/v1/uploads/{uid}/commit", key="one-gib-commit")
    assert committed["source_version_id"] == created["source_version_id"]
    sink = DigestSink()
    copied = resumed.download(
        f"/v1/sources/{created['source_id']}/versions/{created['source_version_id']}/original",
        sink,
        sha256=sha,
    )
    assert copied == sink.count == total and sink.digest.hexdigest() == sha
    assert len(env["s"].source_get(env["a"], created["source_id"])["versions"]) == 1
    # Free the test's committed object through the actual privacy/deletion flow.
    env["s"].revoke(env["a"], "large-cleanup", created["source_id"], purge=True)
