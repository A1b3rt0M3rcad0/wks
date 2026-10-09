import socket

import boto3
import pytest
from moto import mock_aws

from wks.domain.models import Error
from wks.infrastructure.capture import destination
from wks.infrastructure.enrichment import NoopEnricher
from wks.infrastructure.storage import FilesystemStore, S3Store
from wks.settings import Settings


@pytest.mark.parametrize(
    "uri",
    [
        "http://127.0.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://10.1.2.3",
        "http://[::1]",
        "file:///etc/passwd",
        "http://example.com:8080",
        "https://user:secret@example.com",
        "https://localhost",
    ],
)
def test_A33_ssrf_blocked(uri):
    with pytest.raises(Error, match="Public"):
        destination(uri)


def test_A33_mixed_dns_rebinding_blocked(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(Error):
        destination("https://public.example")


def test_pinned_connection_revalidates_redirect(monkeypatch, tmp_path):
    import http.client

    from wks.infrastructure.capture import fetch_public

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            if args[0] == "public.example"
            else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        ),
    )

    class Response:
        status = 302

        def getheader(self, name, default=None):
            return "http://internal.example" if name == "Location" else default

    class Connection:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    with pytest.raises(Error):
        fetch_public("https://public.example", tmp_path / "capture", Settings())


def test_filesystem_immutable_and_traversal(tmp_path):
    store = FilesystemStore(tmp_path / "objects")
    path = tmp_path / "source"
    path.write_bytes(b"source")
    store.put_file("tenant/opaque", path)
    with pytest.raises(FileExistsError):
        store.put_file("tenant/opaque", path)
    with pytest.raises(ValueError):
        store.path("../../outside")
    assert b"".join(store.iter_bytes("tenant/opaque", 1, 3)) == b"our"
    store.delete("tenant/opaque")
    assert not store.exists("tenant/opaque")


@mock_aws
def test_s3_port_streaming_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    boto3.DEFAULT_SESSION = None
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="wks-content")
    store = S3Store(Settings(storage_provider="s3"))
    path = tmp_path / "blob"
    path.write_bytes(b"123456")
    store.put_file("opaque/source", path)
    assert store.ready() and store.exists("opaque/source")
    assert b"".join(store.iter_bytes("opaque/source", 1, 3)) == b"234"
    target = tmp_path / "download"
    store.materialize("opaque/source", target)
    assert target.read_bytes() == b"123456"
    store.delete("opaque/source")
    assert not store.exists("opaque/source")


def test_noop_enrichment_has_no_external_side_effects():
    assert NoopEnricher().enrich(b"image", "image/png", "op")["state"] == "enrichment_unavailable"


def test_domain_dependency_boundary():
    import ast
    from pathlib import Path

    for file in Path("src/wks/domain").glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("fastapi", "sqlalchemy", "docling", "mcp", "wks.infrastructure")
                )
