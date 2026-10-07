"""Hosted tool calls and discovery follow authenticated plugin catalogs."""

import asyncio  # noqa: ANYIO_OK -- isolate the hub's asyncio lock.
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from core.config import config
from models.models import ToolDefinitionModel
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest_asyncio.fixture
async def tenant_servers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_resolve_session_id", AsyncMock(return_value="selected"))
    metadata = [
        {"name": "effect_probe", "unity_target": "manage_vfx"},
        {"name": "disabled_probe", "unity_target": "manage_disabled"},
        {"name": "helper_probe", "unity_target": None},
    ]
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools", lambda: metadata
    )
    servers = {}
    for user in ("alice", "bob", "empty"):
        if user != "empty":
            await registry.register(user, user.title(), user, "6000", user_id=user)
        if user == "alice":
            await registry.register_tools_for_session(
                user, [ToolDefinitionModel(name="manage_vfx")]
            )
        server = FastMCP(user)
        middleware = UnityInstanceMiddleware()
        monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value=user))
        server.add_middleware(middleware)

        @server.tool
        def effect_probe() -> str:
            return "effect"

        @server.tool
        def disabled_probe() -> str:
            pytest.fail("Disabled Unity tool must never execute")

        @server.tool
        def helper_probe() -> str:
            return "helper"

        servers[user] = server
    await registry.register("alice-off", "AliceOff", "alice-off", "6000", user_id="alice")
    return servers


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_remote_enabled_tools_and_helpers_remain_available(tenant_servers, mode: str) -> None:
    async with Client(tenant_servers["alice"], mode=mode) as client:
        assert {tool.name for tool in await client.list_tools()} == {"effect_probe", "helper_probe"}
        effect = await client.call_tool("effect_probe", {"unity_instance": "Alice@alice"})
        assert effect.data == "effect"
        with pytest.raises((ToolError, MCPError)):
            await client.call_tool("disabled_probe", {"unity_instance": "Alice@alice"})
        assert (await client.call_tool("helper_probe")).data == "helper"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("user", ["bob", "empty"])
async def test_remote_empty_catalog_does_not_inherit_another_users_tools(
    tenant_servers, mode: str, user: str
) -> None:
    async with Client(tenant_servers[user], mode=mode) as client:
        assert {tool.name for tool in await client.list_tools()} == {"helper_probe"}
        with pytest.raises((ToolError, MCPError)):
            await client.call_tool("effect_probe", {"unity_instance": f"{user.title()}@{user}"})
        assert (await client.call_tool("helper_probe")).data == "helper"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("target", ["Bob@bob", "AliceOff@alice-off"])
async def test_remote_call_checks_selected_project_catalog(
    tenant_servers, mode: str, target: str
) -> None:
    async with Client(tenant_servers["alice"], mode=mode) as client:
        # Alice's enabled project must not authorize a different selected project.
        with pytest.raises((ToolError, MCPError)):
            await client.call_tool("effect_probe", {"unity_instance": target})
