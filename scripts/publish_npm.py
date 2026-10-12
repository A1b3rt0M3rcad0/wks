"""Publish one qualified npm tarball; retries accept only identical registry content."""

import argparse
import base64
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("latest", "next"), required=True)
    parser.add_argument("--tarball", type=Path, required=True)
    args = parser.parse_args()
    expected = (
        "sha512-" + base64.b64encode(hashlib.sha512(args.tarball.read_bytes()).digest()).decode()
    )
    request = urllib.request.Request(
        "https://registry.npmjs.org/wksctl/" + args.version,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            existing = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    else:
        if existing["dist"]["integrity"] != expected:
            raise RuntimeError("Immutable npm version exists with different content")
        print(f"wksctl@{args.version} already published with identical integrity")
        return
    subprocess.run(
        ["npm", "publish", str(args.tarball), "--access", "public", "--tag", args.channel],
        check=True,
    )


if __name__ == "__main__":
    main()
