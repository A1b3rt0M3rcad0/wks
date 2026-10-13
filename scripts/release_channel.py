"""New releases are stable; existing prereleases remain immutable and recoverable."""

from version import validate


def choose(version, existing, *, explicit=False):
    validate(version)
    if version in existing:
        return version
    if "-" in version and explicit:
        raise ValueError("New releases must use a stable X.Y.Z version")
    return version.split("-")[0]
