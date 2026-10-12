"""Build the self-contained npm CLI package using the release's native build rules."""

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from distribution import TARGETS, build_clients


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", args.version):
        parser.error("version must be a WKS SemVer")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be a complete commit SHA")
    root = Path(__file__).resolve().parents[1]
    if args.output.exists():
        parser.error("output already exists; existing files are preserved")
    shutil.copytree(root / "packages/wksctl/npm", args.output)
    package = json.loads((args.output / "package.json").read_text())
    package.update(version=args.version, wksCommit=args.revision)
    (args.output / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(root / name, args.output / name)
    shutil.copyfile(
        root / "packages/wksctl/THIRD_PARTY_LICENSES.txt", args.output / "THIRD_PARTY_LICENSES.txt"
    )
    with tempfile.TemporaryDirectory(prefix="wks-npm-") as temporary:
        staged = Path(temporary)
        # Keep release builds from altering tracked embedded contracts in the checkout.
        shutil.copytree(root / "packages/wksctl", staged / "packages/wksctl")
        shutil.copytree(root / "packages/wks-sdk-go", staged / "packages/wks-sdk-go")
        (staged / "packages/wks-api").mkdir()
        schema = json.loads((root / "packages/wks-api/openapi.json").read_text())
        schema["info"]["version"] = args.version
        (staged / "packages/wks-api/openapi.json").write_text(json.dumps(schema))
        for name in ("LICENSE", "NOTICE"):
            shutil.copyfile(root / name, staged / name)
        archives = staged / "archives"
        archives.mkdir()
        build_clients(staged, archives, args.version, args.revision, 0, dict(os.environ))
        import tarfile
        import zipfile

        for system, arch in TARGETS:
            name = f"wksctl-{args.version}-{system}-{arch}"
            executable = "wksctl.exe" if system == "windows" else "wksctl"
            destination = args.output / "native" / f"{system}-{arch}" / executable
            destination.parent.mkdir(parents=True)
            if system == "windows":
                with zipfile.ZipFile(archives / (name + ".zip")) as archive:
                    content = archive.read(executable)
            else:
                with tarfile.open(archives / (name + ".tar.gz")) as archive:
                    content = archive.extractfile(executable).read()
            destination.write_bytes(content)
            destination.chmod(0o755)
    source = (root / "packages/wksctl/internal/cli/initialize.go").read_text()
    skill = source.split("const AgentSkill = `", 1)[1].split("`", 1)[0]
    (args.output / "SKILL.md").write_text(skill)


if __name__ == "__main__":
    main()
