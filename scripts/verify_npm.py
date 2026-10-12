"""Wait for staged npm publication and verify the exact qualified tarball is public."""

import argparse
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tarball", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    integrity = (
        "sha512-" + base64.b64encode(hashlib.sha512(args.tarball.read_bytes()).digest()).decode()
    )
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            request = urllib.request.Request(
                "https://registry.npmjs.org/wksctl/" + args.version,
                headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                metadata = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (404, 429, 500, 502, 503, 504):
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "npm accepted publication but the exact version is not publicly available yet"
                ) from error
            print(
                f"Waiting for npm processing of wksctl@{args.version} (HTTP {error.code})",
                flush=True,
            )
            time.sleep(min(30, max(0, deadline - time.monotonic())))
            continue
        if metadata["version"] != args.version or metadata["dist"]["integrity"] != integrity:
            raise RuntimeError("Public npm content differs from the qualified tarball")
        print(f"Verified public wksctl@{args.version}: SHA-512 matches qualified tarball")
        return


if __name__ == "__main__":
    main()
