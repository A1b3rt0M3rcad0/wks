"""Resolve one immutable WKS version and commit, without rewriting source or merging."""

import argparse
import re
import subprocess
import tomllib
from pathlib import Path

from version import python_version, semver_key, validate


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def ancestor(older, newer):
    status = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer]).returncode
    if status not in (0, 1):
        raise RuntimeError("Cannot establish release ancestry")
    return status == 0


def source_floor(revision):
    floor = validate(git("show", revision + ":VERSION"))
    files = ["pyproject.toml"] + [
        file
        for file in git("ls-tree", "-r", "--name-only", revision, "packages").splitlines()
        if re.fullmatch(r"packages/[^/]+/pyproject.toml", file)
    ]
    for file in files:
        project = tomllib.loads(git("show", revision + ":" + file))["project"]
        if project["version"] != python_version(floor):
            raise ValueError("Source workspace versions differ")
        if any(
            dependency.startswith("wks-")
            and "==" in dependency
            and dependency.split("==")[1] != python_version(floor)
            for dependency in project.get("dependencies", [])
        ):
            raise ValueError("Source workspace dependencies differ")
    return floor


def bump(previous, messages):
    major, minor, patch = map(int, previous.split("-")[0].split("."))
    breaking = bool(re.search(r"(?m)^\w+(?:\([^\n)]+\))?!:|^BREAKING[ -]CHANGE:", messages))
    feature = bool(re.search(r"(?m)^feat(?:\([^\n)]+\))?:", messages))
    if breaking and major:
        return f"{major + 1}.0.0"
    if breaking or feature:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch if '-' in previous else patch + 1}"


def select(version="", revision="", tag=""):
    versions = {}
    for existing in git("tag", "--list", "v*").splitlines():
        try:
            valid = validate(existing[1:])
        except ValueError:
            continue
        versions[valid] = git("rev-parse", "refs/tags/" + existing + "^{commit}")
    if tag:
        tagged = validate(tag.removeprefix("v"))
        if tag != "v" + tagged or (version and version != tagged):
            raise ValueError("Version and source tag differ")
        version = tagged
    if version:
        validate(version)
    if revision and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Revision must be a full lowercase commit SHA")
    revision = revision or versions.get(version) or git("rev-parse", "HEAD")
    if tag:
        if versions.get(version) != revision:
            raise ValueError("Tagged source differs from its immutable commit")
    elif not ancestor(revision, "origin/master"):
        raise ValueError("Automatic and manual releases require source integrated in master")
    floor = source_floor(revision)
    if version in versions:
        if versions[version] != revision:
            raise ValueError("An immutable tag already names a different commit")
    elif not version:
        matching = [
            v
            for v, commit in versions.items()
            if commit == revision and semver_key(v) >= semver_key(floor)
        ]
        if matching:
            version = max(matching, key=semver_key)
    if version in versions:
        if semver_key(version) < semver_key(floor):
            raise ValueError("Release is below the source version floor")
        return version, revision
    if versions:
        latest = max(versions, key=semver_key)
        previous = versions[latest]
        if not ancestor(previous, revision):
            if version:
                raise ValueError("New release predates the latest reserved source")
            return None
        if version and semver_key(version) <= semver_key(latest):
            raise ValueError("New versions must advance reserved tags")
        if not version:
            proposed = bump(latest, git("log", "--format=%B", previous + ".." + revision))
            version = max((floor, proposed), key=semver_key)
    version = version or floor
    if semver_key(version) < semver_key(floor):
        raise ValueError("Release is below the source version floor")
    return version, revision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="")
    parser.add_argument("--revision", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = select(args.version, args.revision, args.tag)
    with args.output.open("a") as output:
        if result is None:
            output.write("skip=true\n")
        else:
            output.write(f"version={result[0]}\nrevision={result[1]}\nskip=false\n")
