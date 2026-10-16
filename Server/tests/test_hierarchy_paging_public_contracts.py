"""Real registered SDK boundaries for bounded hierarchy reads; Unity replies are inert."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_registered_hierarchy_and_children_paging_contract(mode, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    source = r"""
        import copy, json, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        import anyio
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.resources as resources
        import services.tools as tools
        from services.resources import gameobject
        from services.tools import manage_prefabs as prefab
        from services.registry import get_registered_resources, get_registered_tools
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs): raise AssertionError("application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            for module in (gameobject, prefab):
                module.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
            prefab.preflight = AsyncMock(return_value=None)
            tool_metadata = [item for item in get_registered_tools() if item["name"] == "manage_prefabs"]
            resource_metadata = [item for item in get_registered_resources() if item["name"] in ("gameobject", "gameobject_children")]
            tools.discover_modules = resources.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: tool_metadata
            resources.get_registered_resources = lambda: resource_metadata
            server = FastMCP("bounded-hierarchy-contract")
            tools.register_all_tools(server)
            resources.register_all_resources(server)
            requests = []
            reply = {"success": True, "data": {"extra_native_field": {"keep": True}, "next_cursor": "a" * 32, "total": None, "total_known": False}}
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, dict(params)))
                return copy.deepcopy(reply)
            PluginHub.send_command_for_instance = send
            async with Client(server, mode=MODE) as client:
                listed = await client.list_tools()
                schema = next(item for item in listed if item.name == "manage_prefabs").input_schema["properties"]
                assert {"cursor", "max_depth", "page_size"} <= schema.keys()
                templates = await client.list_resource_templates()
                assert any(str(item.uriTemplate) == "mcpforunity://scene/gameobject/{instance_id}/children{?page_size,cursor}" for item in templates)
                async def call(arguments):
                    result = await client.call_tool("manage_prefabs", arguments)
                    return json.loads(result.content[0].text)
                data = await call({"action": "get_hierarchy", "prefab_path": "Assets/Owned.prefab"})
                assert data == reply
                assert requests[-1] == ("Owned@fixture", "manage_prefabs", {"action": "get_hierarchy", "prefabPath": "Assets/Owned.prefab", "page_size": 50, "max_depth": 64})
                data = await call({"action": "get_hierarchy", "prefab_path": "Assets/Owned.prefab", "page_size": 17, "max_depth": 0, "cursor": "a" * 32})
                assert data == reply and requests[-1][2]["cursor"] == "a" * 32
                assert requests[-1][2]["page_size"] == 17 and requests[-1][2]["max_depth"] == 0
                for arguments in ({"page_size": 0}, {"page_size": 501}, {"max_depth": -1}, {"max_depth": 129}, {"cursor": "bad"}):
                    before = len(requests)
                    data = await call({"action": "get_hierarchy", "prefab_path": "Assets/Owned.prefab", **arguments})
                    assert data["success"] is False and len(requests) == before, (arguments, data)
                for arguments in ({"page_size": True}, {"page_size": 1.5}, {"max_depth": False}):
                    before = len(requests)
                    result = await client.call_tool("manage_prefabs", {"action": "get_hierarchy", "prefab_path": "Assets/Owned.prefab", **arguments}, raise_on_error=False)
                    assert result.is_error or json.loads(result.content[0].text)["success"] is False, arguments
                    assert len(requests) == before
                await call({"action": "list_overrides", "target": 42})
                assert requests[-1][2] == {"action": "list_overrides", "target": 42, "offset": 0, "pageSize": 100}
                reply = {"success": False, "error": "stale_cursor", "message": "Restart after edit", "data": {"restart": True}, "hint": "inspect_state_before_retry"}
                data = await call({"action": "get_hierarchy", "prefab_path": "Assets/Owned.prefab", "cursor": "b" * 32})
                assert data == reply
                reply = {"success": True, "data": {"instanceID": -42, "children": [-7, -8], "childrenNextCursor": "c" * 32, "childrenTotal": 3, "childrenConsistency": "live_indices", "childrenRestartAdvice": "Restart after any edit", "native_extra": False}}
                async def read(suffix):
                    return json.loads((await client.read_resource("mcpforunity://scene/gameobject/-42" + suffix))[0].text)
                assert (await read(""))["data"] == reply["data"]
                assert requests[-1][2] == {"instanceID": -42}
                data = await read("/children?page_size=2&cursor=" + "c" * 32)
                assert data["data"] == reply["data"]
                assert requests[-1] == ("Owned@fixture", "get_gameobject", {"instanceID": -42, "children_page_size": 2, "children_cursor": "c" * 32})
                await read("/children")
                assert requests[-1][2] == {"instanceID": -42, "children_page_size": 50}
                for suffix in ("/children?page_size=0", "/children?page_size=501", "/children?cursor=bad"):
                    before = len(requests)
                    data = await read(suffix)
                    assert data["success"] is False and len(requests) == before, data
                reply = {"success": False, "error": "stale_cursor", "hint": "inspect_state_before_retry", "data": {"reason": "source_changed"}}
                data = await read("/children?page_size=2&cursor=" + "d" * 32)
                for key, value in reply.items(): assert data[key] == value
            print("paging public SDK contract passed", MODE, len(requests))
        anyio.run(scenario)
    """
    source = "MODE = " + repr(mode) + "\n" + textwrap.dedent(source)
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
