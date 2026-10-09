"""Fail before claiming a job when its processor package is absent."""

from importlib.util import find_spec

from wks_core.processing import QUEUES


def available_queues():
    return tuple(queue for queue in QUEUES if find_spec("wks_worker_" + queue) is not None)


def validate_queues(queues):
    queues = tuple(queues)
    if not queues or any(queue not in available_queues() for queue in queues):
        raise ValueError("Install the processor packages for the requested queues")
    return queues
