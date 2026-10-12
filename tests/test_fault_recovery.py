"""Twenty durable-write interruption points recover without duplicate source versions."""

import pytest
from sqlalchemy import func, select
from wks_core.storage.database import Source, SourceVersion


class InjectedCrash(BaseException):
    pass


POINTS = [
    "source.before_prepare",
    "source.after_prepare",
    "source.before_insert",
    "source.after_insert",
    "source.before_version",
    "source.after_version",
    "blob.before_intent",
    "blob.after_intent",
    "blob.before_put",
    "blob.after_put",
    "blob.before_verify",
    "blob.after_verify",
    "blob.after_verified_commit",
    "blob.before_attach",
    "blob.before_metadata_insert",
    "blob.after_attach",
    "mutation.before_transaction",
    "mutation.before_callback",
    "mutation.after_callback",
    "mutation.before_receipt",
]


@pytest.mark.parametrize("point", POINTS)
def test_interrupted_write_recovers_exactly_once(env, point):
    def crash(name):
        if name == point:
            raise InjectedCrash("simulated process termination")

    env["s"].fault_injector = crash
    body = {
        "namespace_id": env["ns_a"],
        "kind": "text",
        "title": "durable source",
        "text": "recoverable original bytes",
    }
    with pytest.raises(InjectedCrash):
        env["s"].register(env["a"], "recover-write", body)
    env["s"].fault_injector = None
    recovered = env["s"].register(env["a"], "recover-write", body)
    assert env["s"].register(env["a"], "recover-write", body) == recovered
    with env["s"].sessions() as db:
        assert (
            db.scalar(select(func.count(Source.id)).where(Source.namespace_id == env["ns_a"])) == 1
        )
        assert (
            db.scalar(
                select(func.count(SourceVersion.id)).where(
                    SourceVersion.source_id == recovered["source_id"]
                )
            )
            == 1
        )
    assert env["s"].binary(
        env["a"], sid=recovered["source_id"], vid=recovered["source_version_id"]
    )["size"] == len(body["text"].encode())
