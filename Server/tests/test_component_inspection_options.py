"""Public URI contract for explicitly evaluating potentially unsafe user getters."""

from tests.test_component_read_public_contracts import _run


def test_component_getter_evaluation_is_explicit_for_all_public_read_routes(tmp_path):
    _run(
        r"""
import asyncio, json, sys
from unittest.mock import AsyncMock
sys.path.insert(0, "src")
from fastmcp import FastMCP, Client
from fastmcp.exceptions import ResourceError
from mcp.shared.exceptions import MCPError
import services.resources as resources
import services.resources.gameobject as gameobject
from services.registry import get_registered_resources

async def send(fn, instance, command, params, **kwargs):
    return {"success": True, "data": {"command": command, "params": params}}

async def scenario():
    gameobject.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
    gameobject.send_with_unity_instance = AsyncMock(side_effect=send)
    metadata = [item for item in get_registered_resources() if item["name"] in ("gameobject_components", "gameobject_component")]
    resources.discover_modules = lambda *args: []
    resources.get_registered_resources = lambda: metadata
    server = FastMCP("safe-inspection-options")
    resources.register_all_resources(server)
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            for suffix in ("components", "component/Fixture.Probe", "component/Fixture.Probe?component_index=1"):
                uri = "mcpforunity://scene/gameobject/42/" + suffix
                separator = "&" if "?" in uri else "?"
                ordinary = json.loads((await client.read_resource(uri))[0].text)
                assert "evaluateGetters" not in ordinary["data"]["params"]
                explicit = json.loads((await client.read_resource(uri + separator + "evaluate_getters=true"))[0].text)
                assert explicit["data"]["params"]["evaluateGetters"] is True, explicit
                disabled = json.loads((await client.read_resource(uri + separator + "evaluate_getters=false"))[0].text)
                assert "evaluateGetters" not in disabled["data"]["params"]
                for invalid in ("yes", "1", "unexpected"):
                    before = gameobject.send_with_unity_instance.await_count
                    try:
                        result = json.loads((await client.read_resource(uri + separator + "evaluate_getters=" + invalid))[0].text)
                        assert result["success"] is False, result
                    except (ResourceError, MCPError):
                        pass
                    assert gameobject.send_with_unity_instance.await_count == before
asyncio.run(scenario())
""",
        tmp_path,
    )
