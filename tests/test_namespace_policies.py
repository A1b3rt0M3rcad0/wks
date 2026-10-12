import pytest
from conftest import register_text
from wks_core.domain.models import Error


def test_namespace_schema_and_input_limits_are_enforced_in_core(env):
    s = env["s"]
    policy = {
        "max_input_bytes": 16,
        "metadata_schema": {
            "required": ["category"],
            "properties": {"category": {"type": "string", "enum": ["lesson"]}},
            "additionalProperties": False,
        },
    }
    s.metadata_patch(env["a"], "policy", "namespace", env["ns_a"], {"policy": policy}, 1)
    with pytest.raises(Error):
        register_text(env, value="valid small", key="missing-schema")
    with pytest.raises(Error):
        s.register(
            env["a"],
            "oversized",
            {
                "namespace_id": env["ns_a"],
                "kind": "text",
                "title": "budget",
                "text": "x" * 17,
                "metadata": {"category": "lesson"},
            },
        )
    created = s.register(
        env["a"],
        "valid",
        {
            "namespace_id": env["ns_a"],
            "kind": "text",
            "title": "valid",
            "text": "small",
            "metadata": {"category": "lesson"},
        },
    )
    assert created["source_id"]
    with pytest.raises(Error):
        s.metadata_patch(
            env["a"],
            "invalid-meta",
            "source",
            created["source_id"],
            {"metadata": {"category": "unknown"}},
            1,
        )


def test_action_window_applies_before_new_mutations_and_receipt_replay_is_safe(env):
    env["s"].settings.action_requests_per_minute = 1
    created = register_text(env, key="once")
    assert register_text(env, key="once") == created
    with pytest.raises(Error, match="budget"):
        register_text(env, key="twice")


def test_persisted_exports_consume_namespace_storage_budget(env):
    from conftest import process_all, register_text

    service, principal = env["s"], env["a"]
    registered = register_text(env, value="Original storage budget text. " * 20)
    process_all(env)
    source = service.source_get(principal, registered["source_id"])
    representation = source["versions"][0]["representation_id"]
    artifact = service.export_create(principal, "budget-export", representation, "plain-text")
    process_all(env)
    exported = service.export_get(principal, artifact["id"])
    assert exported["status"] == "ready" and exported["byte_size"] > 0
    service.settings.namespace_max_bytes = (
        source["versions"][0]["byte_size"] + exported["byte_size"] + 10
    )
    with pytest.raises(Error) as rejected:
        register_text(env, key="overflow", value="X" * 20)
    assert rejected.value.code == "upload.quota_exceeded"
