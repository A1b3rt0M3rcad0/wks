"""Validate the reviewed source version floor and all workspace manifests."""

import argparse
import re
import tomllib
from pathlib import Path

NUMBER = r"(?:0|[1-9][0-9]*)"
SEMVER = re.compile(rf"{NUMBER}\.{NUMBER}\.{NUMBER}(?:-(?:alpha|beta|rc)\.{NUMBER})?")
ROOT = Path(__file__).resolve().parent.parent


def validate(value):
    if not SEMVER.fullmatch(value):
        raise ValueError("Expected X.Y.Z or X.Y.Z-{alpha,beta,rc}.N, without a v prefix")
    return value


def semver_key(value):
    core, _, pre = validate(value).partition("-")
    rank = {"alpha": 0, "beta": 1, "rc": 2}
    return (
        *map(int, core.split(".")),
        1 if not pre else 0,
        (rank[pre.split(".")[0]], int(pre.split(".")[1])) if pre else (0, 0),
    )


def python_version(value):
    return validate(value).replace("-alpha.", "a").replace("-beta.", "b").replace("-rc.", "rc")


def check(root=ROOT):
    floor = validate((root / "VERSION").read_text().strip())
    for relative in [
        "pyproject.toml",
        "packages/wks-core/pyproject.toml",
        "packages/wks-api/pyproject.toml",
    ]:
        project = tomllib.loads((root / relative).read_text())["project"]
        if project["version"] != python_version(floor):
            raise ValueError("Workspace versions differ from VERSION")
        for dependency in project.get("dependencies", []):
            if dependency.startswith(("wks-core==", "wks-api==")) and dependency.split("==")[
                1
            ] != python_version(floor):
                raise ValueError("Workspace dependency differs from VERSION")
    return floor


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate")
    args = parser.parse_args()
    print(validate(args.validate) if args.validate else check())
