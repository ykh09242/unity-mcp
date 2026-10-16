"""Registered modern/legacy SDK contract; inert Unity replies and no application network."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_registered_scoped_gameobject_search_contract(mode, tmp_path):
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
        import services.tools as tools
        from services.tools import find_gameobjects as module
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs): raise AssertionError("application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            module.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
            module.preflight = AsyncMock(return_value=None)
            metadata = [item for item in get_registered_tools() if item["name"] == "find_gameobjects"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            server = FastMCP("scoped-search-contract")
            tools.register_all_tools(server)
            requests = []
            reply = {"success": True, "message": "Found", "data": {"instanceIDs": [], "nextCursor": "a" * 32, "totalCount": None, "totalKnown": False, "hasMore": True, "extra_native_field": {"keep": True}}}
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, copy.deepcopy(params)))
                return copy.deepcopy(reply)
            PluginHub.send_command_for_instance = send
            async with Client(server, mode=MODE) as client:
                listed = await client.list_tools()
                entry = next(item for item in listed if item.name == "find_gameobjects")
                schema = entry.input_schema
                assert {"scene_scope", "conditions", "max_depth", "cursor"} <= schema["properties"].keys()
                assert "search_term" not in schema.get("required", [])
                assert entry.annotations.readOnlyHint is False
                async def call(arguments):
                    result = await client.call_tool("find_gameobjects", arguments)
                    return json.loads(result.content[0].text)
                assert await call({"search_term": "Enemy", "page_size": "2", "cursor": "3"}) == reply
                assert requests[-1][2] == {"searchMethod": "by_name", "searchTerm": "Enemy", "includeInactive": False, "pageSize": 2, "cursor": 3}
                conditions = [{"search_method": "by_component", "search_term": "BoxCollider"}]
                args = {"scene_scope": "all_loaded", "conditions": conditions}
                assert await call(args) == reply
                assert requests[-1] == ("Owned@fixture", "find_gameobjects", {"searchMethod": "by_name", "includeInactive": False, "pageSize": 50, "scene_scope": "all_loaded", "conditions": conditions, "max_depth": 64})
                assert await call({**args, "cursor": "a" * 32, "page_size": 17, "max_depth": 0}) == reply
                assert requests[-1][2]["cursor"] == "a" * 32 and requests[-1][2]["max_depth"] == 0
                for options in ({"page_size": "2"}, {"page_size": 0}, {"page_size": 501}, {"page_size": True}, {"page_size": 1.5}, {"max_depth": False}, {"max_depth": 129}, {"cursor": 0}, {"cursor": "g" * 32}, {"conditions": []}, {"conditions": [{"search_method":"by_name", "search_term":"x", "extra":True}]}, {"search_term": "\U0001f600" * 257}):
                    before = len(requests)
                    gates = module.preflight.await_count
                    result = await client.call_tool("find_gameobjects", {**args, **options}, raise_on_error=False)
                    assert result.is_error or json.loads(result.content[0].text)["success"] is False, (options, result)
                    assert len(requests) == before and module.preflight.await_count == gates, options
                reply = {"success": False, "code": "stale_cursor", "error": "stale_cursor", "data": {"restart": True, "message": "Restart after edit"}, "hint": "native-preserved"}
                assert await call({**args, "cursor": "b" * 32}) == reply
                module.preflight.assert_awaited()
                assert all(call.kwargs == {"wait_for_no_compile": True, "refresh_if_dirty": True} for call in module.preflight.await_args_list)
            print("scoped public SDK contract passed", MODE, len(requests))
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
