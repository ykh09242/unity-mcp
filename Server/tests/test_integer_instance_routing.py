"""Integer selectors retain port semantics at the public FastMCP boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP
from mcp.shared.exceptions import MCPError

from core.config import config
from transport.plugin_hub import PluginHub
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("selector", [6400, "6400", 6401])
async def test_public_selector_preserves_integer_port_semantics(monkeypatch, transport, selector):
    monkeypatch.setattr(config, "transport_mode", transport)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    middleware = UnityInstanceMiddleware()
    target = SimpleNamespace(id="B@6400abcd12345678", hash="6400abcd12345678", port=6401)
    discovery = AsyncMock(return_value=[target])
    monkeypatch.setattr(middleware, "_discover_instances", discovery)
    server = FastMCP("integer-routing")
    server.add_middleware(middleware)
    dispatched = []

    @server.tool
    async def mutation_probe(ctx: Context) -> str:
        selected = await ctx.get_state("unity_instance")
        dispatched.append(selected)
        return selected

    allowed = selector == "6400" or (transport == "stdio" and selector == 6401)
    async with Client(server) as client:
        if allowed:
            result = await client.call_tool("mutation_probe", {"unity_instance": selector})
            assert result.data == target.id
        else:
            with pytest.raises(MCPError):
                await client.call_tool("mutation_probe", {"unity_instance": selector})
    assert dispatched == ([target.id] if allowed else [])
    if transport == "http" and isinstance(selector, int):
        discovery.assert_not_awaited()
