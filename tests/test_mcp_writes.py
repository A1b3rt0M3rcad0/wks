"""MCP writes stay in trusted transport scope and share core receipts with HTTP."""

import httpx
import pytest
from conftest import register_text
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_mcp import server as server_fixture


@pytest.fixture
def server(env):
    yield from server_fixture.__wrapped__(env)


async def test_write_tools_are_scoped_typed_and_parity_preserving(env, server):
    actions = [
        "source:create",
        "source:read",
        "source:update",
        "source:delete",
        "operation:read",
        "collection:create",
        "collection:write",
    ]
    grant = env["s"].grant_create(
        env["a"],
        "write-grant",
        {
            "namespace_id": env["ns_a"],
            "caller_binding": env["executor_id"],
            "audience": "wks",
            "allowed_actions": actions,
            "purpose": "MCP functional verification",
            "ttl_seconds": 300,
        },
    )["id"]
    headers = {"Authorization": "Bearer " + env["executor_token"], "X-WKS-Grant": grant}
    async with httpx.AsyncClient(headers=headers) as client:
        async with streamable_http_client(server + "/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert "create_source" in tools and "delete_source" in tools
                assert tools["create_source"].outputSchema["properties"]["source_id"]
                assert not tools["create_source"].annotations.readOnlyHint
                assert tools["delete_source"].annotations.destructiveHint
                for name in ("create_source", "delete_source", "create_collection"):
                    properties = tools[name].inputSchema["properties"]
                    assert (
                        not {"namespace_id", "client_id", "grant_id", "token", "user_id"}
                        & properties.keys()
                    )
                created = await session.call_tool(
                    "create_source",
                    {
                        "title": "MCP-created",
                        "text": "Scoped knowledge",
                        "operation_key": "mcp-create",
                    },
                )
                assert not created.isError
                sid = created.structuredContent["source_id"]
                assert env["s"].source_get(env["a"], sid)["namespace_id"] == env["ns_a"]
                replay = await client.post(
                    server + "/v1/sources",
                    headers={"Idempotency-Key": "mcp-create"},
                    json={
                        "namespace_id": env["ns_a"],
                        "kind": "text",
                        "title": "MCP-created",
                        "text": "Scoped knowledge",
                    },
                )
                assert replay.status_code == 201
                assert replay.json()["source_id"] == sid
                receipt = await session.call_tool(
                    "get_write_receipt", {"operation_key": "mcp-create"}
                )
                assert receipt.structuredContent["source_id"] == sid
                other = register_text(env, user="b", key="other")
                denied = await session.call_tool(
                    "update_source",
                    {
                        "source_ref": other["source_id"],
                        "revision": 1,
                        "title": "attempt",
                        "operation_key": "cross-scope",
                    },
                )
                assert denied.isError
                preflight = await session.call_tool("destructive_preflight", {"source_ref": sid})
                assert not preflight.isError
                deleted = await session.call_tool(
                    "delete_source",
                    {
                        "source_ref": sid,
                        "confirmation": preflight.structuredContent["confirmation_token"],
                        "operation_key": "delete",
                    },
                )
                assert not deleted.isError
                assert deleted.structuredContent["state"] == "deleted"
                assert not (await client.get(server + f"/v1/sources/{sid}")).is_success


async def test_trusted_context_cannot_be_changed_by_source_identifier(env, server):
    source = register_text(env)
    second_ns = env["s"].namespace_create(env["a"], "second-context", {"title": "second"})["id"]
    headers = {"Authorization": "Bearer " + env["a_token"], "X-WKS-Namespace": second_ns}
    async with httpx.AsyncClient(headers=headers) as client:
        async with streamable_http_client(server + "/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                denied = await session.call_tool(
                    "update_source",
                    {
                        "source_ref": source["source_id"],
                        "revision": 1,
                        "title": "outside selected context",
                        "operation_key": "outside",
                    },
                )
                assert denied.isError
                assert (
                    env["s"].source_get(env["a"], source["source_id"])["title"]
                    != "outside selected context"
                )
