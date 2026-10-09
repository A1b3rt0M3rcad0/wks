"""Release metadata comes from the validated wheel; source installs use distribution metadata."""

from importlib.metadata import version

try:
    from wks_core._build_info import COMMIT, VERSION
except ModuleNotFoundError:
    VERSION, COMMIT = version("wks-core"), "development"


def build_info():
    return {"version": VERSION, "commit": COMMIT}
