"""Release identity gates exercised against real commits and immutable tags."""

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


module("version")
identity = module("release_identity")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "master"], check=True, capture_output=True)
    identity.git("config", "user.name", "Release test")
    identity.git("config", "user.email", "release@example.invalid")
    paths = (
        "pyproject.toml",
        "packages/wks-api/pyproject.toml",
        "packages/wks-core/pyproject.toml",
    )

    def commit(message, floor="0.2.0", integrated=True):
        Path("VERSION").write_text(floor + "\n")
        for path in paths:
            file = Path(path)
            file.parent.mkdir(parents=True, exist_ok=True)
            dependency = "wks-api" if path == "pyproject.toml" else "wks-core"
            file.write_text(
                f'[project]\nversion = "{sys.modules["version"].python_version(floor)}"\n'
                f'dependencies = ["{dependency}=={sys.modules["version"].python_version(floor)}"]\n'
            )
        identity.git("add", ".")
        identity.git("commit", "--allow-empty", "-m", message)
        revision = identity.git("rev-parse", "HEAD")
        if integrated:
            identity.git("update-ref", "refs/remotes/origin/master", revision)
        return revision

    return commit


def test_first_release_uses_reviewed_floor_and_rerun_reuses_tag(repository):
    revision = repository("feat: first password accounts")
    assert identity.select() == ("0.2.0", revision)
    with pytest.raises(ValueError, match="source version floor"):
        identity.select("0.1.0", revision)
    identity.git("tag", "-a", "v0.2.0", "-m", "WKS 0.2.0")
    assert identity.select() == ("0.2.0", revision)
    assert identity.select("0.2.0", revision, "v0.2.0") == ("0.2.0", revision)


@pytest.mark.parametrize(
    "previous,message,expected",
    [
        ("0.2.0", "fix(auth): close session replay", "0.2.1"),
        ("0.2.0", "feat(auth): additional authentication method", "0.3.0"),
        ("0.2.0", "feat!: incompatible contract", "0.3.0"),
        ("1.2.3", "refactor: schema\n\nBREAKING CHANGE: removed contract", "2.0.0"),
        ("0.3.0-rc.1", "fix: qualify candidate", "0.3.0"),
    ],
)
def test_conventional_commits_advance_reserved_versions(repository, previous, message, expected):
    repository("chore: baseline")
    identity.git("tag", "v" + previous)
    revision = repository(message)
    assert identity.select() == (expected, revision)


def test_existing_tag_cannot_be_reassigned_and_source_tag_must_match(repository):
    first = repository("feat: baseline")
    identity.git("tag", "v0.2.0")
    second = repository("fix: follow-up")
    with pytest.raises(ValueError, match="immutable tag"):
        identity.select("0.2.0", second)
    with pytest.raises(ValueError, match="Tagged source"):
        identity.select("0.2.0", second, "v0.2.0")
    assert identity.select("0.2.0", first) == ("0.2.0", first)


def test_delayed_master_event_skips_without_publishing_over_newer_source(repository):
    old = repository("feat: baseline")
    identity.git("tag", "v0.2.0")
    delayed = repository("fix: delayed event")
    repository("feat: newer source")
    identity.git("tag", "v0.3.0")
    assert identity.select(revision=delayed) is None
    assert identity.select("0.2.0", old) == ("0.2.0", old)
    with pytest.raises(ValueError, match="predates"):
        identity.select("0.4.0", delayed)


def test_manual_requires_master_but_explicit_immutable_tag_can_qualify_branch(repository):
    repository("feat: master")
    branch = repository("feat: branch candidate", integrated=False)
    with pytest.raises(ValueError, match="integrated in master"):
        identity.select("0.3.0-rc.1", branch)
    identity.git("tag", "v0.3.0-rc.1")
    assert identity.select("0.3.0-rc.1", branch, "v0.3.0-rc.1") == ("0.3.0-rc.1", branch)


def test_floor_and_manifests_must_agree_before_reserving_release(repository):
    repository("feat: baseline")
    Path("packages/wks-api/pyproject.toml").write_text('[project]\nversion="9.9.9"\n')
    identity.git("add", ".")
    identity.git("commit", "-m", "fix: inconsistent manifest")
    identity.git("update-ref", "refs/remotes/origin/master", identity.git("rev-parse", "HEAD"))
    with pytest.raises(ValueError, match="versions differ"):
        identity.select()


def test_new_explicit_version_cannot_go_backwards(repository):
    repository("feat: baseline")
    identity.git("tag", "v0.3.0")
    revision = repository("fix: follow-up")
    with pytest.raises(ValueError, match="advance reserved"):
        identity.select("0.2.1", revision)


def test_real_candidate_survives_actions_upload_download_without_hidden_files(
    tmp_path, monkeypatch
):
    builder = module("package_release")
    verifier = module("verify_release")
    clone = tmp_path / "source"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(SCRIPTS.parent), str(clone)], check=True
    )
    monkeypatch.chdir(clone)
    monkeypatch.setattr(builder, "ROOT", clone)
    revision = identity.git("rev-parse", "HEAD")
    version = identity.source_floor(revision)
    candidate = tmp_path / "candidate"
    builder.build(version, revision, candidate)
    # Match upload-artifact's default policy, then validate the downloaded files.
    archived = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archived, "w", zipfile.ZIP_DEFLATED) as stream:
        for path in candidate.iterdir():
            if not path.name.startswith("."):
                stream.write(path, path.name)
    downloaded = tmp_path / "downloaded"
    with zipfile.ZipFile(archived) as stream:
        stream.extractall(downloaded)
    manifest = verifier.verify(
        downloaded, version, revision, verifier.file_hash(candidate / "release-manifest.json")
    )
    assert manifest == json.loads((candidate / "release-manifest.json").read_text())
    assert not any(name.startswith(".") for name in manifest["files"])


def test_component_exports_preserve_only_the_required_index(tmp_path, monkeypatch):
    # This uses the real committed archive and exports, catching an unrelated CPU
    # index leaking into API/native/media installation (uv first-index semantics).
    builder = module("package_release")
    clone = tmp_path / "source"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(SCRIPTS.parent), str(clone)], check=True
    )
    monkeypatch.chdir(clone)
    monkeypatch.setattr(builder, "ROOT", clone)
    revision = identity.git("rev-parse", "HEAD")
    candidate = tmp_path / "candidate"
    builder.build(identity.source_floor(revision), revision, candidate)
    for component in ("api", "native", "media"):
        assert (
            "download.pytorch.org"
            not in (candidate / (component + "-requirements.txt")).read_text()
        )
    assert "download.pytorch.org" in (candidate / "docling-requirements.txt").read_text()
