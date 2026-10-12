import pytest
from conftest import process_all, register_text
from wks_core.domain.models import Error


def test_metadata_filters_remain_bound_to_cursor_generation(env):
    service, principal = env["s"], env["a"]
    sources = [
        register_text(env, key=str(i), value="resistance electrical circuit", title="Physics")
        for i in range(3)
    ]
    process_all(env)
    for i, source in enumerate(sources):
        service.metadata_patch(
            principal,
            "category" + str(i),
            "source",
            source["source_id"],
            {"metadata": {"category": "physics"}},
            1,
        )
    request = {
        "query": "resistance",
        "language": "simple",
        "page_size": 1,
        "filters": {"metadata": {"category": "physics"}},
    }
    first = service.search(principal, request)
    assert first["next_cursor"]
    for i, source in enumerate(sources):
        service.metadata_patch(
            principal,
            "category-change" + str(i),
            "source",
            source["source_id"],
            {"metadata": {"category": "changed"}},
            2,
        )
    following = service.search(principal, {**request, "cursor": first["next_cursor"]})
    assert (
        following["items"]
        and following["items"][0]["segment_id"] != first["items"][0]["segment_id"]
    )
    assert not service.search(principal, request)["items"]


def test_title_does_not_match_every_body_segment_and_snippet_finds_late_hit(env):
    registered = register_text(
        env,
        title="UniqueHeading",
        value=" ".join(["padding"] * 120 + ["needle"] + ["padding"] * 120),
    )
    process_all(env)
    title = env["s"].search(env["a"], {"query": "UniqueHeading"})
    assert len(title["items"]) == 1
    assert title["items"][0]["match_origin"] == "metadata"
    result = env["s"].search(env["a"], {"query": "needle"})
    assert result["items"]
    hit = result["items"][0]
    assert "needle" in hit["snippet"]
    assert any(hit["snippet"][lo:hi].lower() == "needle" for lo, hi in hit["match_positions"])
    assert hit["source_id"] == registered["source_id"]


def test_exact_phrase_fuzzy_and_bounded_proximity_are_lexical(env):
    registered = register_text(
        env,
        value="Electrical resistance in a parallel circuit. Voltage changes the circuit.",
        title="Physics",
        key="physics",
    )
    process_all(env)
    sid = registered["source_id"]
    for query, mode, options in (
        ("parallel circuit", "phrase", {}),
        ("resistance in a", "exact", {}),
        ("resistence", "fuzzy", {}),
        ("resistance parallel", "proximity", {"distance": 4}),
    ):
        result = env["s"].search(
            env["a"], {"query": query, "mode": mode, "language": "simple", **options}
        )
        assert any(hit["source_id"] == sid for hit in result["items"]), mode
    with pytest.raises(Error):
        env["s"].search(env["a"], {"query": "one two three", "mode": "proximity"})
    with pytest.raises(Error):
        env["s"].search(env["a"], {"query": "ab", "mode": "fuzzy"})


def test_metadata_search_uses_revision_and_preserves_original(env):
    registered = register_text(env, value="Body content only", title="BeforeTitle")
    process_all(env)
    sid = registered["source_id"]
    checksum = env["s"].source_get(env["a"], sid)["versions"][0]["checksum"]
    env["s"].metadata_patch(env["a"], "title-change", "source", sid, {"title": "AfterTitle"}, 1)
    assert env["s"].search(env["a"], {"query": "BeforeTitle"})["items"] == []
    assert env["s"].search(env["a"], {"query": "AfterTitle"})["items"][0]["title"] == "AfterTitle"
    assert env["s"].source_get(env["a"], sid)["versions"][0]["checksum"] == checksum


def test_fuzzy_does_not_expose_other_namespace_terms(env):
    register_text(env, value="secretresistanceword", title="Other private", user="b", key="private")
    register_text(env, value="Public voltage content", title="Owned", key="owned")
    process_all(env)
    assert (
        env["s"].search(env["a"], {"query": "secretresistancword", "mode": "fuzzy"})["items"] == []
    )


def test_facets_and_suggestions_are_scoped(env):
    owned = register_text(env, value="Electrical resistance in parallel", title="Owned")
    register_text(env, value="secretcapacitorword", title="Other", user="b", key="other")
    process_all(env)
    facets = env["s"].search(
        env["a"], {"query": "resistance", "language": "simple", "include_facets": True}
    )
    assert facets["facets"]["media_types"] == {"text/plain": 1}
    assert {hit["source_id"] for hit in facets["items"]} == {owned["source_id"]}
    suggestion = env["s"].suggestions(env["a"], {"query": "resistence", "language": "simple"})
    assert "resistance" in {item["text"].lower() for item in suggestion["suggestions"]}
    hidden = env["s"].suggestions(env["a"], {"query": "secretcapactitorword", "language": "simple"})
    assert hidden["suggestions"] == []
