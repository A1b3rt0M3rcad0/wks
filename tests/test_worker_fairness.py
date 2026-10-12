from conftest import register_text
from wks_worker.worker import Worker


def test_profile_claims_respect_namespace_concurrency_and_fairness(env):
    s = env["s"]
    s.settings.namespace_max_concurrent_jobs = 1
    first = register_text(env, key="first")
    register_text(env, key="second")
    other = register_text(env, user="b", key="other")
    a = Worker(s, queues=("native",))
    b = Worker(s, queues=("native",))
    claim = a.claim()
    assert claim["id"] == first["operation_id"]
    next_claim = b.claim()
    assert next_claim["id"] == other["operation_id"]
    assert Worker(s, queues=("native",)).claim() is None
