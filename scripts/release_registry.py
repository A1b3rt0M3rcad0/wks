"""Inspect immutable GHCR versions and promote a validated image without overwriting it."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from version import validate


def inspect(reference, missing=False):
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", reference],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        if missing and re.search(r"manifest unknown|not found|name unknown", result.stderr, re.I):
            return None
        raise RuntimeError("Registry lookup failed for " + reference)
    manifest = json.loads(result.stdout)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["digest"]):
        raise ValueError("Invalid image digest")
    return manifest


def preflight(image, version, digest):
    validate(version)
    candidate = inspect(image + "@" + digest)
    platforms = {
        (item.get("platform", {}).get("os"), item.get("platform", {}).get("architecture"))
        for item in candidate.get("manifests", [])
    }
    if platforms != {("linux", "amd64"), ("linux", "arm64")}:
        raise ValueError("Candidate must contain exactly Linux amd64 and arm64")
    existing = inspect(image + ":" + version, missing=True)
    if existing and existing["digest"] != digest:
        raise ValueError("Published image version is immutable and has a different digest")
    return existing


def promote(image, tag, digest):
    subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            image + ":" + tag,
            image + "@" + digest,
        ],
        check=True,
    )
    if inspect(image + ":" + tag)["digest"] != digest:
        raise ValueError("Image promotion changed the validated digest")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate(args.version)
    manifest = inspect(args.image + ":" + args.version, missing=True)
    with args.output.open("a") as output:
        output.write(f"reused={'true' if manifest else 'false'}\n")
        if manifest:
            output.write(f"digest={manifest['digest']}\n")
