"""Build deterministic release wheels and source from one committed revision."""

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

from release_identity import source_floor
from version import python_version, semver_key, validate

ROOT = Path(__file__).resolve().parent.parent


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd or ROOT, env=env, check=True)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def overlay_lock(lock, version):
    before = tomllib.loads(lock.read_text())
    chunks = lock.read_text().split("[[package]]\n")
    count = 0
    for index in range(1, len(chunks)):
        package = tomllib.loads("[[package]]\n" + chunks[index])["package"][0]
        if package["name"] in {"wks-core", "wks-api", "woobe-knowledge-service"}:
            chunks[index] = chunks[index].replace(
                f'version = "{package["version"]}"', f'version = "{version}"', 1
            )
            count += 1
    text = "[[package]]\n".join(chunks)
    after = tomllib.loads(text)
    if count != 3:
        raise ValueError("Expected exactly three workspace entries in the lockfile")
    for old, new in zip(before["package"], after["package"], strict=True):
        expected = dict(old)
        if old["name"] in {"wks-core", "wks-api", "woobe-knowledge-service"}:
            expected["version"] = version
        if new != expected:
            raise ValueError("Release version overlay changed a dependency or hash")
    lock.write_text(text)


def build(version, revision, destination):
    validate(version)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if (
        revision != head
        or subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
        ).strip()
    ):
        raise ValueError("Package requires a clean checkout at the exact committed revision")
    floor = source_floor(revision)
    if semver_key(version) < semver_key(floor):
        raise ValueError("Version is below the reviewed source floor")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if list(destination.iterdir()):
        raise ValueError("Package output directory must be empty")
    epoch = int(
        subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", revision], cwd=ROOT, text=True
        )
    )
    environment = os.environ | {"SOURCE_DATE_EPOCH": str(epoch)}
    with tempfile.TemporaryDirectory(prefix="wks-release-") as temporary:
        staging = Path(temporary)
        archive = staging / "committed.tar"
        run("git", "archive", "--format=tar", "--output=" + str(archive), revision)
        with tarfile.open(archive) as stream:
            stream.extractall(staging, filter="data")
        archive.unlink()
        (staging / "VERSION").write_text(version + "\n")
        pep_version = python_version(version)
        for relative in [
            "pyproject.toml",
            "packages/wks-core/pyproject.toml",
            "packages/wks-api/pyproject.toml",
        ]:
            manifest = staging / relative
            text = manifest.read_text()
            previous = tomllib.loads(text)["project"]["version"]
            text = text.replace(f'version = "{previous}"', f'version = "{pep_version}"', 1)
            for name in ["wks-api", "wks-core"]:
                text = text.replace(name + "==" + python_version(floor), name + "==" + pep_version)
            manifest.write_text(text)
        (staging / "packages/wks-api/src/wks_api/_build_info.py").write_text(
            f'VERSION = "{version}"\nCOMMIT = "{revision}"\n'
        )
        contract = staging / "packages/wks-api/openapi.json"
        api = json.loads(contract.read_text())
        api["info"]["version"] = version
        contract.write_text(json.dumps(api, indent=2, ensure_ascii=False) + "\n")
        overlay_lock(staging / "uv.lock", pep_version)
        run("uv", "lock", "--check", "--offline", cwd=staging, env=environment)
        run(
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-workspace",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(destination / "native-requirements.txt"),
            cwd=staging,
            env=environment,
        )
        requirements = destination / "native-requirements.txt"
        lines = requirements.read_text().splitlines(keepends=True)
        requirements.write_text(
            "# Locked native dependencies exported from the reviewed workspace.\n"
            + "".join(lines[2:])
        )
        for package in ["wks-core", "wks-api"]:
            run(
                "uv",
                "build",
                "--system-certs",
                "--package",
                package,
                "--out-dir",
                str(destination),
                cwd=staging,
                env=environment,
            )
        (destination / "openapi.json").write_bytes(contract.read_bytes())

        def normalize(info):
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = epoch
            info.mode = 0o755 if info.isdir() or info.mode & 0o111 else 0o644
            return info

        # Source is the reviewed revision plus only version/build metadata generated above.
        with (destination / f"wks-{version}-source.tar.gz").open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as source:
                    for path in sorted(staging.iterdir()):
                        source.add(path, arcname="wks/" + path.name, filter=normalize)
    # uv build creates this local safety file. Actions omits hidden files, and it is
    # not a release asset; remove it before constructing the complete manifest.
    (destination / ".gitignore").unlink(missing_ok=True)
    files = {path.name: file_hash(path) for path in sorted(destination.iterdir())}
    manifest = {"version": version, "commit": revision, "source_floor": floor, "files": files}
    (destination / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    files["release-manifest.json"] = file_hash(destination / "release-manifest.json")
    (destination / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items()))
    )
    print(f"Packaged WKS {version} at {revision}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.version, args.revision, args.destination)
