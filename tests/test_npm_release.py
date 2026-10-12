"""Immutable npm retry and registry failure gates prevent accidental publication."""

import base64
import hashlib
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "publish_npm", Path(__file__).resolve().parents[1] / "scripts/publish_npm.py"
)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


@pytest.fixture
def publish(tmp_path, monkeypatch):
    tarball = tmp_path / "wksctl-1.1.0.tgz"
    tarball.write_bytes(b"qualified package")
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
    monkeypatch.setattr(
        sys,
        "argv",
        ["publish_npm", "--version", "1.1.0", "--channel", "latest", "--tarball", str(tarball)],
    )
    calls = []
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    return tarball, integrity, calls


def test_identical_published_package_does_not_write(publish, monkeypatch):
    _, integrity, calls = publish
    monkeypatch.setattr(
        publisher.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps({"dist": {"integrity": integrity}}).encode()),
    )
    publisher.main()
    assert calls == []


def test_different_published_package_is_rejected(publish, monkeypatch):
    _, _, calls = publish
    monkeypatch.setattr(
        publisher.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(b'{"dist":{"integrity":"different"}}'),
    )
    with pytest.raises(RuntimeError, match="different content"):
        publisher.main()
    assert calls == []


@pytest.mark.parametrize("status", [404, 401, 429, 503])
def test_only_missing_version_allows_publish(publish, monkeypatch, status):
    tarball, _, calls = publish

    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://registry.npmjs.org/wksctl/1.1.0", status, "registry failure", None, None
        )

    monkeypatch.setattr(publisher.urllib.request, "urlopen", unavailable)
    if status == 404:
        publisher.main()
        assert calls == [
            (
                (["npm", "publish", str(tarball), "--access", "public", "--tag", "latest"],),
                {"check": True},
            )
        ]
    else:
        with pytest.raises(urllib.error.HTTPError):
            publisher.main()
        assert calls == []
