"""Deterministic standalone clients and a pinned dependency inventory for releases."""

import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

TARGETS = tuple(
    (system, arch) for system in ("linux", "darwin", "windows") for arch in ("amd64", "arm64")
)


def go_binary():
    candidate = os.environ.get("WKS_GO_BINARY") or shutil.which("go")
    local = Path(__file__).resolve().parents[1] / ".local/toolchains/go/bin/go"
    if not os.environ.get("WKS_GO_BINARY") and local.exists():
        candidate = str(local)
    if not candidate or not subprocess.check_output([candidate, "version"], text=True).startswith(
        "go version go"
    ):
        raise RuntimeError("Release requires a genuine Go toolchain; set WKS_GO_BINARY")
    return candidate


def build_clients(root, destination, version, revision, epoch, environment):
    go = go_binary()
    module = root / "packages/wksctl"
    (module / "internal/cli/openapi.json").write_bytes(
        (root / "packages/wks-api/openapi.json").read_bytes()
    )
    build_env = environment | {"CGO_ENABLED": "0", "GOTOOLCHAIN": "local"}
    for field in ("GOPATH", "GOCACHE", "GOMODCACHE"):
        build_env.setdefault(
            field, str(Path(os.environ.get("TMPDIR", "/tmp")) / ("wks-release-" + field.lower()))
        )
    toolchain = subprocess.check_output([go, "version"], text=True).strip()
    for system, arch in TARGETS:
        executable = "wksctl.exe" if system == "windows" else "wksctl"
        binary = root / executable
        subprocess.run(
            [
                go,
                "build",
                "-mod=readonly",
                "-trimpath",
                "-buildvcs=false",
                "-ldflags",
                f"-s -w -buildid= -X main.version={version} -X main.commit={revision}",
                "-o",
                str(binary),
                "./cmd/wksctl",
            ],
            cwd=module,
            env=build_env | {"GOOS": system, "GOARCH": arch},
            check=True,
        )
        files = {
            executable: binary,
            "LICENSE": root / "LICENSE",
            "NOTICE": root / "NOTICE",
            "THIRD_PARTY_LICENSES.txt": module / "THIRD_PARTY_LICENSES.txt",
        }
        name = f"wksctl-{version}-{system}-{arch}"
        if system == "windows":
            with zipfile.ZipFile(
                destination / (name + ".zip"), "w", zipfile.ZIP_DEFLATED
            ) as archive:
                for filename, path in files.items():
                    info = zipfile.ZipInfo(filename, (1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (0o755 if filename == executable else 0o644) << 16
                    archive.writestr(info, path.read_bytes())
        else:
            with (destination / (name + ".tar.gz")).open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w") as archive:
                        for filename, path in files.items():
                            info = archive.gettarinfo(str(path), filename)
                            info.uid = info.gid = 0
                            info.uname = info.gname = ""
                            info.mtime = epoch
                            info.mode = 0o755 if filename == executable else 0o644
                            with path.open("rb") as stream:
                                archive.addfile(info, stream)
        binary.unlink()
    return toolchain


def inventory(root, destination, version, revision, toolchain):
    lock = tomllib.loads((root / "uv.lock").read_text())
    components = []
    for package in lock["package"]:
        name, pinned = package["name"], package["version"]
        component = {
            "type": "library",
            "name": name,
            "version": pinned,
            "purl": f"pkg:pypi/{name}@{pinned}",
            "bom-ref": f"python:{name}:{pinned}",
        }
        hashes = sorted(
            {
                file["hash"].split(":", 1)[1]
                for file in package.get("wheels", [])
                if file.get("hash", "").startswith("sha256:")
            }
        )
        # Wheel hashes identify upstream files, not the distribution's own bytes.
        component["properties"] = [
            {"name": "wks:scope", "value": "workspace and optional profiles"},
            {"name": "wks:locked-wheel-sha256", "value": json.dumps(hashes)},
        ]
        if name.startswith("wks-") or name == "woobe-knowledge-service":
            component["licenses"] = [{"license": {"id": "Apache-2.0"}}]
        components.append(component)
    for manifest in (root / "packages").glob("*/go.mod"):
        for line in manifest.read_text().splitlines():
            fields = line.strip().removeprefix("require ").split()
            if (
                len(fields) >= 2
                and fields[1].startswith("v")
                and fields[0] != "go"
                and "wks/packages" not in fields[0]
            ):
                name, pinned = fields[:2]
                component = {
                    "type": "library",
                    "name": name,
                    "version": pinned,
                    "purl": f"pkg:golang/{name}@{pinned}",
                    "bom-ref": f"go:{name}:{pinned}",
                }
                if component not in components:
                    components.append(component)
    output = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "WKS", "version": version},
            "properties": [
                {"name": "wks:source-commit", "value": revision},
                {"name": "wks:go-toolchain", "value": toolchain},
                {
                    "name": "wks:inventory-scope",
                    "value": "Locked Python workspace including optional profiles and Go module requirements; OS image packages and external model licenses are documented separately.",
                },
            ],
        },
        "components": components,
    }
    (destination / "sbom.cdx.json").write_text(json.dumps(output, indent=2) + "\n")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(root / name, destination / name)
    shutil.copyfile(root / "docs/THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")
    shutil.copyfile(
        root / "packages/wksctl/THIRD_PARTY_LICENSES.txt",
        destination / "wksctl-THIRD_PARTY_LICENSES.txt",
    )
    (destination / "toolchain.json").write_text(
        json.dumps({"go": toolchain, "source_commit": revision, "version": version}, indent=2)
        + "\n"
    )
