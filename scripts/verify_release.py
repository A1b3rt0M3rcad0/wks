"""Verify complete candidate checksums, wheel metadata and source identity before publishing."""

import argparse
import hashlib
import json
import zipfile
from email.parser import Parser
from pathlib import Path

from version import python_version, validate


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory, version, revision, manifest_hash=None):
    validate(version)
    manifest_file = directory / "release-manifest.json"
    if manifest_hash and file_hash(manifest_file) != manifest_hash:
        raise ValueError("Downloaded candidate manifest differs from validation output")
    manifest = json.loads(manifest_file.read_text())
    if (manifest["version"], manifest["commit"]) != (version, revision):
        raise ValueError("Candidate release identity differs")
    names = set(manifest["files"])
    if names | {"SHA256SUMS", "release-manifest.json"} != {
        path.name for path in directory.iterdir()
    }:
        raise ValueError("Candidate has missing or unexpected files")
    for name, checksum in manifest["files"].items():
        path = directory / name
        if (
            path.name != name
            or path.is_symlink()
            or not path.is_file()
            or file_hash(path) != checksum
        ):
            raise ValueError("Candidate checksum differs: " + name)
    expected = dict(manifest["files"]) | {"release-manifest.json": file_hash(manifest_file)}
    sums = "".join(f"{digest}  {name}\n" for name, digest in sorted(expected.items()))
    if (directory / "SHA256SUMS").read_text() != sums:
        raise ValueError("SHA256SUMS differs from the validated manifest")
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != 2:
        raise ValueError("Both Core and API wheels are required")
    pep_version = python_version(version)
    wheel_names = set()
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            metadata = Parser().parsestr(
                archive.read(
                    next(
                        name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
                    )
                ).decode()
            )
            wheel_names.add(metadata["Name"])
            if metadata["Version"] != pep_version:
                raise ValueError("Wheel version differs")
            if metadata["Name"] == "wks-api":
                stamp = f'VERSION = "{version}"\nCOMMIT = "{revision}"\n'
                if archive.read("wks_api/_build_info.py").decode() != stamp:
                    raise ValueError("Wheel source identity differs")
                for name in ["app.js", "presentation.js", "index.html", "style.css"]:
                    archive.getinfo("wks_api/web/assets/" + name)
                requirements = metadata.get_all("Requires-Dist", [])
                if f"wks-core=={pep_version}" not in requirements:
                    raise ValueError("API wheel does not depend on the same Core release")
    if wheel_names != {"wks-api", "wks-core"}:
        raise ValueError("Candidate requires exactly one Core and one API wheel")
    if json.loads((directory / "openapi.json").read_text())["info"]["version"] != version:
        raise ValueError("OpenAPI release version differs")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    verify(args.directory, args.version, args.revision, args.manifest_sha256)
    print("PASS: exact candidate identity, all checksums and bundled versioned wheels")
