"""Inventory ordering is applied to authorized results before SDK paging."""

import secrets
from contextlib import asynccontextmanager

import anyio
import httpx
import httpx2
import pytest

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from tests.http.test_mcp_catalog_subscription_limits import (
    ListenStream,
    PluginStream,
    change_count,
    hosted_catalog,  # pylint: disable=unused-import
    wait,
)
from tests.http.test_mcp_protocol_wire import LEGACY_VERSION, _body, _headers, _message


@pytest.fixture
def inventory_server(monkeypatch, prepared_tool_factory):
    """Use production assembly with cached builtin preparation, without a child process."""
    from fastmcp.tools.base import Tool
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", secrets.token_urlsafe(32))
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    with monkeypatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        return create_mcp_server(False)


@asynccontextmanager
async def inventory_http(server, *, modern=True):
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={
                LOCAL_AUTH_HEADER: config.local_auth_token,
                "Accept": "application/json, text/event-stream",
            },
        ) as client:
            headers = {}
            if not modern:
                initialized = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": "wire-request",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": LEGACY_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "inventory-order-test", "version": "1"},
                        },
                    },
                )
                assert initialized.status_code == 200, initialized.text
                assert _message(initialized)["result"]["protocolVersion"] == LEGACY_VERSION
                headers["MCP-Protocol-Version"] = LEGACY_VERSION
                if session_id := initialized.headers.get("mcp-session-id"):
                    headers["Mcp-Session-Id"] = session_id
                ready = await client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                assert ready.status_code == 202, ready.text

            async def request(method, params=None):
                if modern:
                    body = _body(method, params)
                    request_headers = _headers(method, name=(params or {}).get("uri"))
                else:
                    body = {
                        "jsonrpc": "2.0",
                        "id": "wire-request",
                        "method": method,
                        "params": params or {},
                    }
                    request_headers = headers
                response = await client.post("/mcp", headers=request_headers, json=body)
                assert response.status_code == 200, response.text
                message = _message(response)
                assert "error" not in message, message
                result = message["result"]
                if modern:
                    assert result["ttlMs"] == 0
                    assert result["cacheScope"] == "private"
                    assert result["resultType"] == "complete"
                    assert "io.modelcontextprotocol/serverInfo" in result["_meta"]
                else:
                    assert "ttlMs" not in result
                    assert "cacheScope" not in result
                    assert "resultType" not in result
                return result

            yield request


def register_inventory(server):
    """Equivalent inventories can arrive in insertion orders unrelated to their names."""

    def previous(old_value: int) -> str:
        return str(old_value)

    def current(new_value: str) -> str:
        return new_value

    def resource() -> str:
        return "inventory fixture"

    def template(label: str) -> str:
        return label

    def prompt() -> str:
        return "inventory fixture"

    server.tool(name="inventory_z", version="2.0")(current)
    server.tool(name="inventory_z", version="1.0")(previous)
    for name in ("inventory_a", "inventory_A"):
        server.tool(name=name)(current)
    for label in ("z", "a", "A"):
        server.resource(f"fixture://inventory/{label}")(resource)
        server.resource(f"template://inventory/{label}/{{label}}")(template)
    for label in ("z", "é", "a", "A"):
        server.prompt(name=f"inventory_{label}")(prompt)


@pytest.mark.asyncio
@pytest.mark.parametrize("modern", [True, False], ids=["modern", "legacy"])
async def test_inventory_order_is_global_before_deduplication_and_pagination(
    inventory_server, modern
):
    # Given: unsorted registration, two versions of one tool, and real SDK pagination.
    register_inventory(inventory_server)
    inventory_server._list_page_size = 3
    methods = (
        ("tools/list", "tools", "name", ["inventory_A", "inventory_a", "inventory_z"]),
        (
            "resources/list",
            "resources",
            "uri",
            ["fixture://inventory/A", "fixture://inventory/a", "fixture://inventory/z"],
        ),
        (
            "resources/templates/list",
            "resourceTemplates",
            "uriTemplate",
            [
                "template://inventory/A/{label}",
                "template://inventory/a/{label}",
                "template://inventory/z/{label}",
            ],
        ),
        (
            "prompts/list",
            "prompts",
            "name",
            ["inventory_A", "inventory_a", "inventory_z", "inventory_é"],
        ),
    )
    async with inventory_http(inventory_server, modern=modern) as request:
        if modern:
            await request("server/discover")
        await request("resources/read", {"uri": "mcpforunity://instances"})
        for method, collection, key, expected in methods:
            # When: the same authorized inventory is read twice through all cursor pages.
            inventories = []
            for _ in range(2):
                rows, cursor, seen = [], None, set()
                while True:
                    page = await request(method, {"cursor": cursor} if cursor else {})
                    rows.extend(page[collection])
                    cursor = page.get("nextCursor")
                    if cursor is None:
                        break
                    assert cursor not in seen
                    seen.add(cursor)
                inventories.append(rows)

            # Then: complete order is stable, lexical across pages, and preserves the SDK winner.
            assert inventories[0] == inventories[1]
            identifiers = [row[key] for row in inventories[0]]
            assert identifiers == sorted(identifiers)
            assert [value for value in identifiers if value in expected] == expected
            if collection == "tools":
                winners = [row for row in inventories[0] if row["name"] == "inventory_z"]
                assert len(winners) == 1
                assert set(winners[0]["inputSchema"]["properties"]) == {"new_value"}
                assert winners[0]["_meta"]["fastmcp"]["versions"] == ["2.0", "1.0"]


@pytest.mark.asyncio
async def test_zero_ttl_sdk_cache_reaches_authenticated_http_for_each_read(inventory_server):
    # Given: a cache-enabled modern SDK client and the production authenticated ASGI app.
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    app = inventory_server.http_app(path="/mcp", stateless_http=True, json_response=True)
    reads = 0

    async def counted(scope, receive, send):
        nonlocal reads
        if scope["type"] == "http" and dict(scope["headers"]).get(b"mcp-method") == b"tools/list":
            reads += 1
        await app(scope, receive, send)

    def factory(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=counted), **kwargs)

    transport = StreamableHttpTransport(
        "http://fixture/mcp",
        headers={LOCAL_AUTH_HEADER: config.local_auth_token},
        httpx_client_factory=factory,
    )
    async with app.router.lifespan_context(app):
        async with Client(transport, mode="2026-07-28", cache=True) as client:
            # When: two unchanged reads are followed by a changed catalog read.
            first = await client.list_tools_mcp()
            second = await client.list_tools_mcp()
            assert reads == 2

            def added() -> str:
                return "current"

            inventory_server.tool(name="inventory_new")(added)
            third = await client.list_tools_mcp()

    # Then: zero/private hints prevent fresh-cache reuse, and the new inventory is immediate.
    assert reads == 3
    assert first == second
    for result in (first, second, third):
        assert result.ttl_ms == 0
        assert result.cache_scope == "private"
    assert "inventory_new" not in {tool.name for tool in first.tools}
    assert "inventory_new" in {tool.name for tool in third.tools}


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted_catalog", [(True, True)], indirect=True)
async def test_committed_catalog_order_and_scope_remain_current_between_credentials(hosted_catalog):
    # Given: three scoped catalogs, real plugin publication and authenticated subscriptions.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            alice = PluginStream(app, "alice-key", "a", "A")
            other = PluginStream(app, "alice-key", "other", "Other")
            bob = PluginStream(app, "bob-key", "a", "A")
            foreign = PluginStream(app, "bob-key", "foreign", "Foreign")
            peers = (alice, other, bob, foreign)
            for peer in peers:
                await peer.connect(group)
            await other.catalog([{"name": "other_only"}, {"name": "run_tests"}])
            await bob.catalog([{"name": "bob_only"}, {"name": "read_console"}])
            await foreign.catalog([{"name": "foreign_only"}, {"name": "manage_scene"}])
            watch = ListenStream(app, "inventory-alice")
            group.start_soon(watch.run)
            await wait(watch.acknowledged)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://fixture",
                headers={"Accept": "application/json, text/event-stream"},
            ) as client:

                async def current(principal="alice-key", selector="A@a", *, resource=False):
                    method = "resources/read" if resource else "tools/list"
                    params = {"uri": "mcpforunity://custom-tools"} if resource else {}
                    body = _body(method, params)
                    body["params"]["_meta"]["unity_instance"] = selector
                    response = await client.post(
                        "/mcp",
                        headers={
                            **_headers(method, name=params.get("uri")),
                            "x-api-key": principal,
                        },
                        json=body,
                    )
                    assert response.status_code == 200, response.text
                    message = _message(response)
                    assert "error" not in message, message
                    result = message["result"]
                    assert result["ttlMs"] == 0
                    assert result["cacheScope"] == "private"
                    if resource:
                        import json

                        decoded = json.loads(result["contents"][0]["text"])
                        assert decoded["success"] is True, decoded
                        return {tool["name"]: tool for tool in decoded["data"]["tools"]}
                    names = [tool["name"] for tool in result["tools"]]
                    assert names == sorted(names)
                    return {tool["name"]: tool["inputSchema"] for tool in result["tools"]}

                before_other = await current(selector="Other@other")
                before_bob = await current("bob-key")
                # When: add, replacement/removal, empty and reconnect commits change only Alice A.
                catalogs = (
                    [
                        {"name": "read_console"},
                        {"name": "manage_scene"},
                        {"name": "alice_only", "parameters": [{"name": "old", "type": "integer"}]},
                    ],
                    [
                        {"name": "manage_scene"},
                        {"name": "alice_only", "parameters": [{"name": "new", "type": "string"}]},
                    ],
                    [],
                )
                for index, rows in enumerate(catalogs, start=1):
                    watch.changed = anyio.Event()
                    await alice.catalog(rows)
                    await wait(watch.changed)
                    await anyio.wait_all_tasks_blocked()
                    assert change_count(watch) == index
                    listing = await current()
                    assert {"read_console", "manage_scene"} & listing.keys() == {
                        row["name"]
                        for row in rows
                        if row["name"] in {"read_console", "manage_scene"}
                    }
                    if rows:
                        assert "action" in listing["manage_scene"]["properties"]
                    descriptors = await current(resource=True)
                    assert set(descriptors) == {row["name"] for row in rows}
                    if rows:
                        assert descriptors["alice_only"]["parameters"][0]["name"] == (
                            "old" if index == 1 else "new"
                        )
                    # Then: interleaved credentials/instance metadata retain their own inventory/schema.
                    assert await current(selector="Other@other") == before_other
                    assert await current("bob-key") == before_bob
                    assert set(await current("bob-key", resource=True)) == {
                        "bob_only",
                        "read_console",
                    }
                    assert set(await current(selector="Other@other", resource=True)) == {
                        "other_only",
                        "run_tests",
                    }

                denied = ListenStream(app, "inventory-foreign", instance="Foreign@foreign")
                await denied.run()
                assert denied.frames[0]["status"] == 400
                assert not denied.acknowledged.is_set()

                replacement = PluginStream(app, "alice-key", "a", "A")
                await replacement.connect(group)
                await wait(alice.done)
                assert replacement.session_id != alice.session_id
                assert await current(resource=True) == {}
                watch.changed = anyio.Event()
                await replacement.catalog([{"name": "read_console"}])
                await wait(watch.changed)
                assert set(await current(resource=True)) == {"read_console"}
                assert await current("bob-key") == before_bob
                await replacement.close()

            watch.disconnect.set()
            await wait(watch.done)
            for peer in peers:
                await peer.close()
        assert not runtime._leases
        assert not runtime.bus.scopes
