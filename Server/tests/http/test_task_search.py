"""Real MCP search sees the final visibility cap without modifying selection."""

import json

from fastmcp import Client
import pytest
from starlette.testclient import TestClient

from tests.http.test_discovery_profiles import discovery_server, names  # noqa: F401


@pytest.mark.parametrize(
    ("version", "expected"),
    [("2024-11-05", ["text"]), ("2025-03-26", ["text"]), ("2025-06-18", ["text", "resource_link"])],
)
def test_resource_link_negotiation(discovery_server, version, expected):
    server, _ = discovery_server[0]("core")
    uri = "mcpforunity://scene/cameras"

    @server.resource(uri, name="camera_inventory")
    def inventory() -> str:
        return "camera inventory"

    with TestClient(server.http_app(transport="http", json_response=True)) as client:
        client.headers.update({"Accept": "application/json, text/event-stream"})
        initialized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "discovery", "version": "1"},
                },
            },
        )
        assert initialized.json()["result"]["protocolVersion"] == version
        client.headers.update(
            {
                "Mcp-Session-Id": initialized.headers["mcp-session-id"],
                "MCP-Protocol-Version": version,
            }
        )
        assert (
            client.post(
                "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
            ).status_code
            == 202
        )
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "manage_tools",
                    "arguments": {"action": "search", "query": uri, "route_surfaces": "resources"},
                },
            },
        )
        content = response.json()["result"]["content"]
        assert [item["type"] for item in content] == expected
        assert json.loads(content[0]["text"])["route_groups"][0]["representative"]["uri"] == uri


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_search_links_open_advertised_resources_with_unchanged_json(
    discovery_server, mode, monkeypatch
):
    from services.registry import get_registered_tools
    from services.tools.manage_tools import search_unity_tools

    alias = dict(
        name="search_unity_tools",
        func=search_unity_tools,
        description="Search",
        group=None,
        unity_target=None,
        kwargs={},
    )
    monkeypatch.setattr(
        "services.registry.tool_registry._tool_registry", [*get_registered_tools(), alias]
    )
    build, _ = discovery_server
    server, _ = build("core")
    fixed = "mcpforunity://scene/cameras"
    reads = []

    @server.resource(fixed, name="camera_inventory", description="Camera inventory")
    def inventory() -> str:
        reads.append(fixed)
        return "observed camera"

    @server.resource("mcpforunity://scene/cameras/{camera_id}", name="camera_by_id")
    def camera(camera_id: str) -> str:
        return camera_id

    async with Client(server, mode=mode) as client:
        for tool, args in (("search_unity_tools", {}), ("manage_tools", {"action": "search"})):
            match = await client.call_tool(
                tool, {**args, "query": fixed, "route_surfaces": "resources"}
            )
            assert json.loads(match.content[0].text) == match.structured_content
            links = [item for item in match.content if item.type == "resource_link"]
            assert [(str(item.uri), item.name) for item in links] == [(fixed, "camera_inventory")]
            assert reads == []
            assert (await client.read_resource(links[0].uri))[0].text == "observed camera"
            reads.clear()
            for query, surfaces in (
                (fixed, "tools"),
                ("camera_by_id", "resources"),
                ("no_such_thing", "both"),
            ):
                other = await client.call_tool(
                    tool, {**args, "query": query, "route_surfaces": surfaces, "limit": 1}
                )
                assert [item.type for item in other.content] == ["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_active_scene_link_and_tools_only_binding_respect_visibility(
    discovery_server, mode, monkeypatch
):
    from services.registry import get_registered_tools
    from services.registry.action_catalog import EDITOR_STATE_OBSERVATION, OBSERVATION_META

    uri = "mcpforunity://editor/state"
    snapshot = {
        "editor": {"active_scene": {"name": "Unsaved fixture"}},
        "staleness": {"is_stale": False},
    }
    meta = {OBSERVATION_META: EDITOR_STATE_OBSERVATION}

    def inspect_editor_state() -> dict:
        return snapshot

    entry = dict(
        name="inspect_editor_state",
        func=inspect_editor_state,
        description="Editor state",
        group="core",
        unity_target=None,
        kwargs={"meta": meta},
    )
    monkeypatch.setattr(
        "services.registry.tool_registry._tool_registry", [*get_registered_tools(), entry]
    )
    server, _ = discovery_server[0]("core")
    server.resource(
        uri, name="editor_state", description="Editor state", meta=meta, tags={"snapshot"}
    )(inspect_editor_state)
    query = {
        "action": "search",
        "observation_source": "editor_live",
        "required_values": ["active_scene.name"],
    }
    async with Client(server, mode=mode) as client:
        both = await client.call_tool("manage_tools", query)
        links = [item for item in both.content if item.type == "resource_link"]
        assert [str(item.uri) for item in links] == [uri]
        observed = json.loads((await client.read_resource(links[0].uri))[0].text)
        tools = await client.call_tool(
            "manage_tools",
            {**query, "route_surfaces": "tools", "host_tool_names": ["inspect_editor_state"]},
        )
        assert [item.type for item in tools.content] == ["text"]
        binding = tools.structured_content["route_groups"][0]["representative"]
        assert binding["tool"] == "inspect_editor_state"
        assert (
            (await client.call_tool(binding["tool"], {})).structured_content == observed == snapshot
        )

    server.disable(tags={"snapshot"}, components={"resource"})
    async with Client(server, mode=mode) as client:
        filtered = await client.call_tool("manage_tools", {**query, "route_surfaces": "resources"})
        assert filtered.structured_content["route_groups"] == []
        assert [item.type for item in filtered.content] == ["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_search_reports_inactive_tools_without_activating(discovery_server, mode):
    # Given: core selection with a registered optional effect tool.
    build, _ = discovery_server
    server, store = build("core")
    async with Client(server, mode=mode) as client:
        before = await names(client)

        # When: task-purpose search describes that optional capability.
        result = (
            await client.call_tool("manage_tools", {"action": "search", "query": "effect_probe"})
        ).structured_content

        # Then: actual advertisement stays false and no selection state is created.
        assert result["scope"] == "mcp_context"
        assert result["results"][0]["name"] == "effect_probe"
        assert result["results"][0]["advertised"] is False
        assert await names(client) == before
        assert store.retained_entries == 0


@pytest.mark.asyncio
async def test_unidentified_scope_can_search_but_cannot_gain_availability(discovery_server):
    # Given: no project-bound modern selection scope.
    build, state = discovery_server
    state["project"] = None
    server, store = build("minimal")
    async with Client(server) as client:
        # When: a task is searched without activating anything.
        result = (
            await client.call_tool("manage_tools", {"action": "search", "query": "effect"})
        ).structured_content

        # Then: discovery remains useful and disabled inventory stays disabled.
        assert result["results"][0]["advertised"] is False
        assert await names(client) == {"manage_tools"}
        assert store.retained_entries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_read_only_search_uses_same_scoped_registration(discovery_server, mode, monkeypatch):
    from services.registry import get_registered_tools
    from services.operation_permissions import Permission, required_permissions
    from services.tools.manage_tools import search_unity_tools

    build, _ = discovery_server
    server, store = build("minimal")
    before_entries = get_registered_tools()
    server.tool(search_unity_tools)
    monkeypatch.setattr(
        "services.tools.manage_tools.get_registered_tools",
        lambda: [*before_entries, {"name": "other_server_probe", "group": "core"}],
    )
    async with Client(server, mode=mode) as client:
        before = await names(client)
        for arguments in (
            {"query": "effect_probe"},
            {"required_values": ["nodeCount"]},
            {"query": " "},
        ):
            alias = (await client.call_tool("search_unity_tools", arguments)).structured_content
            legacy = (
                await client.call_tool("manage_tools", {"action": "search", **arguments})
            ).structured_content
            assert alias == legacy
        miss = (
            await client.call_tool("search_unity_tools", {"query": "other_server_probe"})
        ).structured_content
        assert all(row["name"] != "other_server_probe" for row in miss["results"])
        assert await names(client) == before
        assert store.retained_entries == 0
    assert required_permissions("search_unity_tools", {}) == {Permission.READ}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_host_inventory_only_narrows_the_current_server_list(discovery_server, mode):
    build, _ = discovery_server
    server, _ = build("core")
    async with Client(server, mode=mode) as client:
        visible = await names(client)
        for inventory, status in (
            (None, "host_unknown"),
            ([], "host_filtered"),
            (sorted(visible), "host_advertised"),
        ):
            args = {"action": "inspect_inventory", "tool": "manage_tools"}
            if inventory is not None:
                args["host_tool_names"] = inventory
            receipt = (await client.call_tool("manage_tools", args)).structured_content
            assert receipt["status"] == status and receipt["server_advertised"]
        missing = {
            "action": "inspect_inventory",
            "tool": "effect_probe",
            "host_tool_names": ["effect_probe"],
        }
        receipt = (await client.call_tool("manage_tools", missing)).structured_content
        assert receipt["status"] == "server_unavailable"
        results = (
            await client.call_tool(
                "manage_tools",
                {"action": "search", "query": "effect_probe", "host_tool_names": ["effect_probe"]},
            )
        ).structured_content
        assert not results["results"] and not results["route_groups"]
