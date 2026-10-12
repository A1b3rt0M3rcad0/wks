"""Portable delegated-action vocabulary shared by every transport."""

GRANT_ACTIONS = frozenset(
    {
        "read",
        "search",
        "asset_read",
        "source:read",
        "search:query",
        "asset:read",
        "namespace:read",
        "collection:read",
        "source:create",
        "source:version",
        "source:update",
        "source:process",
        "source:capture",
        "source:revoke",
        "source:delete",
        "upload:read",
        "upload:write",
        "upload:commit",
        "upload:abort",
        "collection:create",
        "collection:write",
        "collection:update",
        "export:create",
        "export:read",
        "operation:read",
        "events:read",
    }
)
LEGACY_ACTIONS = frozenset(
    {"read", "search", "asset_read", "source:read", "search:query", "asset:read", "export:read"}
)


def validate_delegation(body):
    actions = set(body.get("allowed_actions", []))
    if not actions or not actions <= GRANT_ACTIONS:
        raise ValueError("Unsupported delegated action")
    if bool(body.get("namespace_id")) == bool(body.get("source_version_ids")):
        raise ValueError("Choose namespace or exact versions")
    if body.get("collection_id") and not body.get("namespace_id"):
        raise ValueError("Collection scope requires namespace")
    if not body.get("namespace_id") and not actions <= LEGACY_ACTIONS:
        raise ValueError("Exact version grants cannot mutate")
    if not 1 <= body.get("ttl_seconds", 300) <= 3600:
        raise ValueError("Invalid grant lifetime")
    return body
