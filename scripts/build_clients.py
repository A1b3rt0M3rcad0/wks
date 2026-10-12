"""Build standalone clients with a verified Go toolchain; no production Python dependency."""

import os
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
requested = os.environ.get("WKS_GO_BINARY")
go = (
    requested or str(root / ".local/toolchains/go/bin/go")
    if requested or (root / ".local/toolchains/go/bin/go").exists()
    else shutil.which("go")
)
if not go or not subprocess.check_output([go, "version"], text=True).startswith("go version go"):
    raise RuntimeError("Configure a genuine Go toolchain via WKS_GO_BINARY")
configuration = os.environ | {
    "GOPATH": str(root / ".local/go-path"),
    "GOCACHE": str(root / ".local/go-build"),
    "GOMODCACHE": str(root / ".local/go-mod"),
}
# Use existing writable caches when explicitly configured by cloud setup/CI.
for field in ("GOPATH", "GOCACHE", "GOMODCACHE"):
    if field in os.environ:
        configuration[field] = os.environ[field]
output = root / ".local/stabilization/wksctl"
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(
    [go, "build", "-trimpath", "-o", str(output), "./cmd/wksctl"],
    cwd=root / "packages/wksctl",
    env=configuration,
    check=True,
)
