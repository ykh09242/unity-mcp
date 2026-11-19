"""Real MCP search sees the final visibility cap without modifying selection."""

from fastmcp import Client
import pytest

from tests.http.test_discovery_profiles import discovery_server, names  # noqa: F401


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
