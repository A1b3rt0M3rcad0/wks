"""Qualify the wheel-based release image through Compose, HTTP and first-account setup."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    environment = os.environ | {
        "WKS_IMAGE": args.image,
        "WKS_POSTGRES_PORT": "55435",
        "WKS_HTTP_PORT": "8085",
        "WKS_ENV": "development",
        "WKS_WEB_PUBLIC_ORIGIN": "",
    }
    compose = ["docker", "compose", "-p", "wks-release-smoke"]
    token_file = Path(".local/release-smoke-token")
    token_file.parent.mkdir(exist_ok=True)
    try:
        subprocess.run(compose + ["up", "-d", "--no-build"], env=environment, check=True)
        with httpx.Client(base_url="http://127.0.0.1:8085", timeout=10) as client:
            for attempt in range(120):
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Release image did not become ready")
            if client.get("/health/live").json() != {
                "status": "live",
                "version": args.version,
                "commit": args.revision,
            }:
                raise ValueError("Image does not run the validated wheel revision")
            assert client.get("/app/").status_code == 200
            assert client.get("/app/setup").json()["setup_required"]
            password = secrets.token_urlsafe(32)
            result = client.post(
                "/app/setup", json={"username": "release-smoke", "password": password}
            )
            assert result.status_code == 201
            identity = result.json()
            assert identity["recovery_token"].startswith("wks-recovery-")
            assert client.get("/v1/namespaces").status_code == 200
            assert (
                client.delete(
                    "/app/session", headers={"X-WKS-CSRF": identity["csrf_token"]}
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/app/session", json={"username": "release-smoke", "password": password}
                ).status_code
                == 200
            )
        subprocess.run(
            compose
            + [
                "exec",
                "-T",
                "api",
                "wks",
                "provision-client",
                "--name",
                "release-http-smoke",
                "--token-file",
                ".local/release-smoke-token",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            compose + ["cp", "api:/app/.local/release-smoke-token", str(token_file)],
            env=environment,
            check=True,
        )
        token_file.chmod(0o600)
        subprocess.run(
            [
                sys.executable,
                "scripts/smoke.py",
                "--url",
                "http://127.0.0.1:8085",
                "--token-file",
                str(token_file),
            ],
            check=True,
        )
        metadata = subprocess.check_output(
            compose + ["exec", "-T", "api", "wks", "version"], env=environment, text=True
        )
        assert json.loads(metadata) == {"version": args.version, "commit": args.revision}
        print(
            "PASS: wheel-based image, version/revision, first account, password login and HTTP/worker smoke"
        )
    finally:
        # This project and its volumes belong solely to this disposable release smoke.
        subprocess.run(compose + ["down", "-v"], env=environment, check=True)
        token_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
