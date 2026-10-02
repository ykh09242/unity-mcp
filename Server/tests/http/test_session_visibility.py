"""Group selection persists and stays isolated only for stateful MCP clients."""

import pytest
from fastmcp import Client, FastMCP

from services.tools.manage_tools import manage_tools


@pytest.fixture
def group_server() -> FastMCP:
    server = FastMCP("visibility-migration")
    server.tool(name="manage_tools")(manage_tools)

    @server.tool(tags={"group:vfx"})
    def effect_probe() -> str:
        return "effect"

    server.disable(tags={"group:vfx"}, components={"tool"})
    return server


@pytest.mark.asyncio
async def test_legacy_group_selection_persists_and_isolated_between_clients(group_server: FastMCP) -> None:
    async with Client(group_server, mode="legacy") as first, Client(group_server, mode="legacy") as second:
        assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
        result = await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert result.structured_content["activated"] == "vfx"
        assert "effect_probe" in {tool.name for tool in await first.list_tools()}
        assert "effect_probe" not in {tool.name for tool in await second.list_tools()}
        await first.call_tool("manage_tools", {"action": "deactivate", "group": "vfx"})
        assert "effect_probe" not in {tool.name for tool in await first.list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["activate", "deactivate", "reset"])
async def test_sessionless_group_changes_report_unsupported_persistence(group_server: FastMCP, action: str) -> None:
    async with Client(group_server) as client:
        result = await client.call_tool("manage_tools", {"action": action, "group": "vfx"})
        assert "sessionless" in result.structured_content["error"]
        assert "effect_probe" not in {tool.name for tool in await client.list_tools()}
        groups = await client.call_tool("manage_tools", {"action": "list_groups"})
        assert any(group["name"] == "vfx" for group in groups.structured_content["groups"])
