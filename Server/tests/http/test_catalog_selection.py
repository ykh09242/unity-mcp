"""Availability is a final cap, never a change to a client's selection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
import pytest

from core.config import config
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService
from services.tools import sync_tool_visibility_from_unity
from services.tools.manage_tools import manage_tools
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture
def catalog_server(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    metadata = [
        {"name": "scene_probe", "group": "core", "unity_target": "scene_probe"},
        {"name": "effect_probe", "group": "vfx", "unity_target": "effect_probe"},
        {"name": "texture_probe", "group": "vfx", "unity_target": "texture_probe"},
        {"name": "manage_tools", "group": None, "unity_target": None},
    ]
    monkeypatch.setattr("services.registry.tool_registry._tool_registry", metadata)
    server = FastMCP("independent-selection")
    for item in metadata[:-1]:
        server.tool(name=item["name"], tags={f"group:{item['group']}"})(lambda: "ok")
    server.tool(name="manage_tools")(manage_tools)
    server.disable(tags={"group:vfx"}, components={"tool"})
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_mcp", server)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    middleware = UnityInstanceMiddleware()
    selected = {"project": "First@first"}

    async def inject(context, **_kwargs):
        await context.fastmcp_context.set_state("unity_instance", selected["project"])

    # The connection boundary is controlled; registry, publication, SDK transforms,
    # final middleware and actual client requests remain real.
    monkeypatch.setattr(middleware, "_inject_unity_instance", inject)
    server.add_middleware(middleware)
    return server, registry, service, selected


async def publish(registry, session_id, names):
    return await registry.register_tools_for_session(
        session_id,
        [ToolDefinitionModel(name=name) for name in names],
        _publish=lambda session, tools: PluginHub._publish_session_catalog(
            session, tools, registry._sessions, generation=session_id
        ),
    )


async def names(client):
    return {tool.name for tool in await client.list_tools()}


@pytest.mark.asyncio
async def test_catalog_sync_preserves_default_and_individual_client_selection(catalog_server):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    async with Client(server, mode="legacy") as first, Client(server, mode="legacy") as second:
        assert await names(first) == {"scene_probe", "manage_tools"}
        await publish(registry, "one", ["scene_probe", "effect_probe"])
        assert await names(first) == {"scene_probe", "manage_tools"}
        await first.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        assert await names(first) == {"scene_probe", "effect_probe", "manage_tools"}
        assert await names(second) == {"scene_probe", "manage_tools"}
        await publish(registry, "one", ["scene_probe", "effect_probe", "texture_probe"])
        assert await names(first) == {"scene_probe", "effect_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_reset_reports_actual_default_visibility_after_catalog_changes(catalog_server):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    await publish(registry, "one", ["effect_probe"])
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}
        reset = (await client.call_tool("manage_tools", {"action": "reset"})).structured_content
        assert reset["visible_tools"] == ["manage_tools"]
        assert await names(client) == {"manage_tools"}
        await publish(registry, "one", ["scene_probe", "effect_probe"])
        assert await names(client) == {"scene_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_known_empty_catalog_hides_native_tools_but_unknown_preserves_discovery(
    catalog_server,
):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    async with Client(server, mode="legacy") as client:
        assert await names(client) == {"scene_probe", "manage_tools"}
        await publish(registry, "one", [])
        assert await names(client) == {"manage_tools"}
        await registry.unregister("one")
        assert await names(client) == {"scene_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_project_switch_and_reconnect_keep_selection_with_current_cap(catalog_server):
    server, registry, _, selected = catalog_server
    await registry.register("one", "First", "first", "6000")
    await registry.register("two", "Second", "second", "6000")
    await publish(registry, "one", ["effect_probe"])
    await publish(registry, "two", ["texture_probe"])
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}
        selected["project"] = "Second@second"
        assert await names(client) == {"texture_probe", "manage_tools"}
        await registry.register("replacement", "Second", "second", "6000")
        await publish(registry, "replacement", ["effect_probe"])
        assert await names(client) == {"effect_probe", "manage_tools"}
        await client.call_tool("manage_tools", {"action": "reset"})
        assert await names(client) == {"manage_tools"}


@pytest.mark.asyncio
async def test_stdio_filters_after_session_transforms_and_discards_stale_catalog(
    catalog_server, monkeypatch
):
    server, _, service, selected = catalog_server
    monkeypatch.setattr(config, "transport_mode", "stdio")
    current = {"generation": "socket-one"}

    async def generation(instance):
        assert instance == selected["project"]
        return current["generation"]

    monkeypatch.setattr(
        "transport.legacy.unity_connection.get_authenticated_stdio_generation", generation
    )
    service.replace_project_catalog(
        "first",
        [ToolDefinitionModel(name="effect_probe")],
        kind="stdio",
        generation="socket-one",
        publish_global=False,
    )
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}
        service.replace_project_catalog(
            "first",
            [],
            kind="stdio",
            generation="socket-one",
            publish_global=False,
        )
        assert await names(client) == {"manage_tools"}
        current["generation"] = "socket-two"
        # Unknown current generation falls back to the client's discovery profile.
        assert await names(client) == {
            "scene_probe",
            "effect_probe",
            "texture_probe",
            "manage_tools",
        }


@pytest.mark.asyncio
async def test_stdio_sync_retains_native_availability_and_never_promotes_groups(
    catalog_server, monkeypatch
):
    server, _, service, _ = catalog_server
    monkeypatch.setattr(config, "transport_mode", "stdio")
    connection = SimpleNamespace(instance_id="First@first")

    async def send(_command, instance, command, _params, **kwargs):
        assert (instance, command) == ("First@first", "get_tool_states")
        kwargs["_catalog_capture"].append((connection, "socket-one"))
        return {
            "data": {
                "tools": [
                    {"name": "effect_probe", "enabled": True, "is_built_in": True},
                    {"name": "texture_probe", "enabled": False, "is_built_in": True},
                ]
            }
        }

    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", send)
    monkeypatch.setattr(
        "transport.legacy.unity_connection.get_authenticated_stdio_generation",
        AsyncMock(return_value="socket-one"),
    )
    monkeypatch.setattr(
        "transport.legacy.unity_connection._commit_stdio_catalog",
        lambda _connection, _generation, publish: publish(),
    )
    assert (await sync_tool_visibility_from_unity("First@first", notify=False))["synced"]
    assert set(service._sources[("stdio", None, "first")].tools) == {"effect_probe"}
    async with Client(server, mode="legacy") as client:
        assert await names(client) == {"manage_tools"}
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert await names(client) == {"effect_probe", "manage_tools"}


@pytest.mark.asyncio
async def test_local_call_admission_checks_availability_after_client_selection(catalog_server):
    server, registry, _, _ = catalog_server
    await registry.register("one", "First", "first", "6000")
    await publish(registry, "one", ["effect_probe"])
    async with Client(server, mode="legacy") as client:
        await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        # A stale client list must not dispatch a now-disabled native operation.
        assert not (await client.call_tool("effect_probe", {})).is_error
        await publish(registry, "one", ["texture_probe"])
        with pytest.raises(MCPError, match="disabled or unavailable"):
            await client.call_tool("effect_probe", {}, raise_on_error=False)
