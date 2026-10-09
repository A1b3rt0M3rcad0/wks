from wks_core.bootstrap import build as core_build

from wks_api.server.config import Settings


def build(settings=None):
    return core_build(settings or Settings())
