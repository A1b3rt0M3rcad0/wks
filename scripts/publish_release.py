"""Publish exact validated wheels/source and an immutable GHCR image after all gates."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from release_identity import select
from release_registry import preflight, promote
from verify_release import file_hash, verify
from version import semver_key, validate


def run(*args, capture=False):
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else None


def github(path, paginate=False):
    args = ["gh", "api", path]
    if paginate:
        args += ["--paginate", "--slurp"]
    result = json.loads(run(*args, capture=True))
    return [item for page in result for item in page] if paginate else result


def publish(version, revision, manifest_hash, image, digest, directory, tagged=False):
    validate(version)
    repository = os.environ["GITHUB_REPOSITORY"]
    manifest = verify(directory, version, revision, manifest_hash)
    if select(version, revision, "v" + version if tagged else "") != (version, revision):
        raise ValueError("Candidate source no longer matches release identity")
    # Authentication or a private-repository 404 must fail before interpreting missing releases.
    github("repos/" + repository)
    releases = github("repos/" + repository + "/releases?per_page=100", paginate=True)
    matching = [release for release in releases if release["tag_name"] == "v" + version]
    if len(matching) > 1:
        raise ValueError("Multiple releases exist for the same immutable version")
    release = matching[0] if matching else None
    files = {path.name: path for path in directory.iterdir()}
    uploaded = set()
    if release:
        assets = release["assets"]
        if len({asset["name"] for asset in assets}) != len(assets) or any(
            asset["name"] not in files for asset in assets
        ):
            raise ValueError("Published release contains unexpected or duplicate assets")
        with tempfile.TemporaryDirectory(prefix="wks-assets-") as temporary:
            for asset in assets:
                path = files[asset["name"]]
                if asset["size"] != path.stat().st_size:
                    raise ValueError("Immutable release asset size differs")
                run(
                    "gh",
                    "release",
                    "download",
                    "v" + version,
                    "--repo",
                    repository,
                    "--pattern",
                    asset["name"],
                    "--dir",
                    temporary,
                )
                if file_hash(Path(temporary) / asset["name"]) != file_hash(path):
                    raise ValueError("Immutable release asset checksum differs")
                uploaded.add(asset["name"])
        if not release["draft"] and uploaded != set(files):
            raise ValueError("Published release is incomplete; do not mutate its assets")
    existing_image = preflight(image, version, digest)
    remote = run(
        "git",
        "ls-remote",
        "--tags",
        "origin",
        "refs/tags/v" + version,
        "refs/tags/v" + version + "^{}",
        capture=True,
    )
    rows = [line.split() for line in remote.splitlines()]
    remote_commit = next(
        (row[0] for row in rows if row[1].endswith("^{}")), rows[0][0] if rows else None
    )
    if remote_commit and remote_commit != revision:
        raise ValueError("Remote tag points to a different immutable source")
    if not remote_commit:
        if run("git", "tag", "--list", "v" + version, capture=True):
            if (
                run("git", "rev-parse", "refs/tags/v" + version + "^{commit}", capture=True)
                != revision
            ):
                raise ValueError("Local tag differs from release source")
        else:
            run(
                "git",
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "tag",
                "-a",
                "v" + version,
                revision,
                "-m",
                "WKS " + version,
            )
        run("git", "push", "origin", "refs/tags/v" + version)
    stable_versions = []
    for item in releases:
        tag = item["tag_name"]
        try:
            parsed = validate(tag.removeprefix("v"))
        except ValueError:
            continue
        if not item["draft"] and "-" not in parsed:
            stable_versions.append(parsed)
    latest = "-" not in version and all(
        semver_key(version) >= semver_key(v) for v in stable_versions
    )
    with tempfile.TemporaryDirectory(prefix="wks-notes-") as temporary:
        notes = Path(temporary) / "notes.md"
        notes.write_text(
            f"WKS {version}, fonte `{revision}`.\n\nUsername/senha, recuperação por token e workspace humano.\n\n"
            f"Imagem validada: `{image}@{digest}`.\n\n"
            "Baixe os wheels ou o pacote de fontes e confira SHA256SUMS antes de instalar.\n"
            f"Com Compose, use `WKS_IMAGE={image}:{version} docker compose up -d --no-build`.\n"
            "O frontend está em `/app/`; a primeira abertura guia a criação da conta.\n"
        )
        if release is None:
            run(
                "gh",
                "release",
                "create",
                "v" + version,
                "--repo",
                repository,
                "--verify-tag",
                "--draft",
                "--title",
                "WKS " + version,
                "--notes-file",
                str(notes),
            )
        pending = sorted(set(files) - uploaded)
        if pending:
            run(
                "gh",
                "release",
                "upload",
                "v" + version,
                *[str(files[name]) for name in pending],
                "--repo",
                repository,
            )
        if not existing_image:
            promote(image, version, digest)
        if latest:
            promote(image, "latest", digest)
        if release is None or release["draft"]:
            run(
                "gh",
                "release",
                "edit",
                "v" + version,
                "--repo",
                repository,
                "--draft=false",
                "--prerelease=" + str("-" in version).lower(),
                "--latest=" + str(latest).lower(),
            )
        elif latest:
            run("gh", "release", "edit", "v" + version, "--repo", repository, "--latest=true")
    print(f"Published WKS {manifest['version']} from {manifest['commit']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--tagged-source", action="store_true")
    args = parser.parse_args()
    publish(
        args.version,
        args.revision,
        args.manifest_sha256,
        args.image,
        args.digest,
        args.directory,
        args.tagged_source,
    )
