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
                (
                    [
                        "npm",
                        "publish",
                        str(tarball),
                        "--access",
                        "public",
                        "--tag",
                        "latest",
                        "--provenance",
                    ],
                ),
                {"check": True},
            )
        ]
    else:
        with pytest.raises(urllib.error.HTTPError):
            publisher.main()
        assert calls == []


def test_trusted_publisher_has_one_standalone_identity():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/npm.yaml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert "workflow_call" not in triggers
    assert triggers["workflow_run"]["workflows"] == ["Release WKS"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["publish"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert "environment" not in workflow["jobs"]["publish"]
    assert "NPM_TOKEN" not in (root / ".github/workflows/npm.yaml").read_text()
    assert "NODE_AUTH_TOKEN" not in (root / ".github/workflows/npm.yaml").read_text()
    release = yaml.safe_load((root / ".github/workflows/release.yaml").read_text())
    assert all(
        job.get("uses") != "./.github/workflows/npm.yaml" for job in release["jobs"].values()
    )


@pytest.mark.parametrize(
    "event,matching_source",
    [
        ("workflow_dispatch", True),
        ("workflow_run", True),
        ("workflow_run", False),
        ("workflow_run_recovery", True),
    ],
)
def test_workflow_resolves_only_the_immutable_release_source(tmp_path, event, matching_source):
    import os
    import subprocess

    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/npm.yaml").read_text())
    script = next(
        step["run"]
        for step in workflow["jobs"]["identity"]["steps"]
        if step.get("id") == "identity"
    )
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Release",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "source",
        ],
        cwd=tmp_path,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    subprocess.run(["git", "tag", "v1.2.0-rc.1"], cwd=tmp_path, check=True)
    candidate = tmp_path / "release-candidate"
    candidate.mkdir()
    (candidate / "release-manifest.json").write_text(
        json.dumps({"version": "1.2.0-rc.1", "commit": revision if matching_source else "0" * 40})
    )
    output = tmp_path / "github-output"
    environment = os.environ | {
        "REQUESTED_TAG": "v1.2.0-rc.1",
        "RELEASE_EVENT": "workflow_run" if event == "workflow_run_recovery" else event,
        "EXPECTED_REVISION": "0" * 40 if event == "workflow_run_recovery" else revision,
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    if matching_source:
        assert result.returncode == 0, result.stderr
        fields = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert fields == {"version": "1.2.0-rc.1", "channel": "next", "revision": revision}
    else:
        assert result.returncode != 0
        assert not output.exists()


def test_relative_tarball_is_published_as_a_local_absolute_path(publish, monkeypatch):
    tarball, _, calls = publish
    monkeypatch.chdir(tarball.parent)
    monkeypatch.setattr(
        sys,
        "argv",
        ["publish_npm", "--version", "1.1.0", "--channel", "next", "--tarball", tarball.name],
    )

    def missing(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://registry.npmjs.org/wksctl/1.1.0", 404, "missing", None, None
        )

    monkeypatch.setattr(publisher.urllib.request, "urlopen", missing)
    publisher.main()
    command = calls[0][0][0]
    assert command[2] == str(tarball.resolve())
    assert command[-3:] == ["--tag", "next", "--provenance"]
