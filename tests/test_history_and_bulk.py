import pytest
from conftest import process_all, register_text
from wks_core.domain.models import Error


def test_history_and_versions_are_complete_across_pages(env):
    service, principal = env["s"], env["a"]
    receipt = register_text(env)
    process_all(env)
    source = receipt["source_id"]
    service.process(principal, "reprocess", source, {})
    process_all(env)
    first = service.source_history(principal, source, 1)
    assert not first["complete"] and first["next_cursor"]
    second = service.source_history(principal, source, 1, first["next_cursor"])
    assert second["complete"] and second["next_cursor"] is None
    assert first["representations"][0]["id"] != second["representations"][0]["id"]
    assert first["operations"][0]["id"] != second["operations"][0]["id"]
    service.new_version(principal, "revision", source, {"text": "Second immutable revision"})
    page = service.versions_list(principal, source, 1)
    assert page["items"][0]["revision_number"] == 1 and not page["complete"]
    final = service.versions_list(principal, source, 1, page["next_cursor"])
    assert final["items"][0]["revision_number"] == 2 and final["complete"]
    with pytest.raises(Error):
        service.source_history(env["b"], source, 1, first["next_cursor"])


def test_bulk_reprocess_plan_filters_and_receipt_do_not_duplicate(env):
    service, principal = env["s"], env["a"]
    register_text(env)
    register_text(env, key="second")
    process_all(env)
    plan = service.bulk_process(principal, "plan", env["ns_a"], {"dry_run": True})
    assert len(plan["items"]) == 2 and not plan["mutated"]
    assert all(item["operation_id"] is None for item in plan["items"])
    assert not service.bulk_process(
        principal, "missing", env["ns_a"], {"missing_index_only": True}
    )["items"]
    result = service.bulk_process(principal, "apply", env["ns_a"], {"dry_run": False})
    assert result == service.bulk_process(principal, "apply", env["ns_a"], {"dry_run": False})
    assert all(item["operation_id"] and item["scheduled"] for item in result["items"])
    with pytest.raises(Error):
        service.bulk_process(env["b"], "cross", env["ns_a"], {"dry_run": False})
