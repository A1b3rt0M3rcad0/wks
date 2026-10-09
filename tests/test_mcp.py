import socket
import threading
import time

import httpx
import pytest
import uvicorn
from conftest import process_all, register_text, upload
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from wks_api.http.app import create_app


@pytest.fixture
def server(env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(env["s"], env["e"])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("MCP test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    assert not thread.is_alive()


def grant(env, receipt, key="grant"):
    return env["s"].grant_create(
        env["a"],
        key,
        {
            "caller_binding": env["executor_id"],
            "audience": "wks",
            "source_version_ids": [receipt["source_version_id"]],
            "allowed_actions": ["read", "search", "asset_read"],
            "purpose": "mcp-test",
            "ttl_seconds": 300,
        },
    )["id"]


async def test_A21_external_mcp_initialize_list_call_http_parity(env, server):
    r = register_text(env)
    other = register_text(env, user="b", title="Private B")
    process_all(env)
    gid = grant(env, r)
    headers = {"Authorization": "Bearer " + env["executor_token"], "X-WKS-Grant": gid}
    async with httpx.AsyncClient(headers=headers) as client:
        async with streamable_http_client(server + "/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.protocolVersion
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    "search",
                    "list_sources",
                    "outline",
                    "read",
                    "read_asset",
                    "get_processing_status",
                }
                assert all(t.annotations.readOnlyHint for t in tools.tools)
                for t in tools.tools:
                    schema = str(t.inputSchema)
                    assert (
                        "token" not in schema
                        and "grant_id" not in schema
                        and "user_id" not in schema
                    )
                result = await session.call_tool("search", {"query": "paralelo"})
                assert not result.isError
                actual = result.structuredContent
                http_result = await client.post(server + "/v1/search", json={"query": "paralelo"})
                assert actual["items"] == http_result.json()["items"]
                denied = await session.call_tool("outline", {"source_ref": other["source_id"]})
                assert denied.isError
                items = await session.call_tool("list_sources", {})
                assert len(items.structuredContent["items"]) == 1


async def test_A32_mcp_asset_resource_fallback(env, server):
    from pathlib import Path

    r = upload(env, Path("tests/fixtures/circuit.png"), "image/png")
    process_all(env)
    gid = grant(env, r)
    p = env["s"].authenticate(env["executor_token"], gid)
    hit = env["s"].search(p, {"query": "circuit"})["items"][0]
    ref = hit["asset_refs"][0]
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + env["executor_token"], "X-WKS-Grant": gid}
    ) as client:
        async with streamable_http_client(server + "/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "read_asset", {"asset_ref": ref, "delivery_mode": "inline"}
                )
                assert not result.isError
                assert result.content[1].type == "resource_link"
                assert "media_available_but_not_delivered_to_model" in result.content[0].text
                resource = await session.read_resource(ref)
                assert resource.contents[0].mimeType == "image/png"
                import base64

                assert base64.b64decode(resource.contents[0].blob).startswith(b"\x89PNG")
        env["s"].grant_revoke(env["a"], "revoke", gid)
        revoked = await client.post(
            server + "/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"}
        )
        assert revoked.status_code == 403


async def test_mcp_no_auth_is_denied_before_discovery(server):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            server + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        assert r.status_code == 401
