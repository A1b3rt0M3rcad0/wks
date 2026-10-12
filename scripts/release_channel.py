"""Automatic releases remain candidates; stable promotion is explicit and immutable."""

from version import validate


def choose(version, existing, *, explicit=False, tagged=False, reviewed=False):
    validate(version)
    if "-" in version or version in existing:
        return version
    if tagged or (explicit and reviewed):
        return version
    if explicit:
        raise ValueError("Stable promotion requires the reviewed gate or an explicit stable tag")
    core = version.split("-")[0]
    numbers = [
        int(value.rsplit(".", 1)[1]) for value in existing if value.startswith(core + "-rc.")
    ]
    return core + "-rc." + str(max(numbers, default=0) + 1)
