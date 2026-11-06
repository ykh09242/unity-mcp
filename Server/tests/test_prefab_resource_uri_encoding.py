"""Registered actual SDK prefab URIs preserve already-decoded asset paths."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests.test_resource_uri_contracts import run_sdk_regression


def test_prefab_helper_search_recipe_calls_an_available_core_tool():
    run_sdk_regression("""
        import asyncio, importlib, shlex
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.resources.prefab import get_prefab_api_docs
        from services.tools import register_all_tools
        asset = importlib.import_module("services.tools.manage_asset")
        asset.get_unity_instance_from_context = AsyncMock(return_value="Selected@owned")
        asset.send_with_unity_instance = AsyncMock(return_value={
            "success": True, "message": "Found", "data": {"assets": []}})

        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            docs = (await get_prefab_api_docs(None)).data
            words = shlex.split(docs["workflow"][0])
            tool = words[words.index("Use") + 1]
            arguments = dict(word.split("=", 1) for word in words if "=" in word)
            server = FastMCP("prefab-helper-example")
            register_all_tools(server, project_scoped_tools=False)
            async with Client(server, mode="2026-07-28") as client:
                assert tool in {item.name for item in await client.list_tools()}
                result = await client.call_tool(tool, arguments, raise_on_error=False)
                assert not result.is_error, result
            assert asset.send_with_unity_instance.await_count == 1
            assert asset.send_with_unity_instance.call_args.args[3]["filterType"] == "Prefab"
            assert "prefab_stage" in docs["related_tools"]
        asyncio.run(scenario())
    """)


@pytest.mark.parametrize(
    "resource_name,suffix,action",
    [
        ("prefab_info", "", "get_info"),
        ("prefab_hierarchy", "/hierarchy", "get_hierarchy"),
    ],
)
def test_prefab_resource_preserves_decoded_path_and_error_metadata(
    resource_name, suffix, action, tmp_path
):
    paging_params = {"page_size": 50, "max_depth": 64} if action == "get_hierarchy" else {}
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    source = f"""
        import json, os, socket, sys
        from pathlib import Path
        from urllib.parse import quote
        from unittest.mock import AsyncMock
        home = Path(os.environ["APPDATA"]).parent / "owned-home"
        home.mkdir()
        Path.home = lambda: home
        sys.path.insert(0, "src")
        import anyio
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.resources as resources
        from services.resources import prefab
        from services.registry import get_registered_resources
        from transport.plugin_hub import PluginHub

        async def scenario():
            # Given actual templates, an explicit selected instance, and inert Unity.
            def deny(*args, **kwargs): raise AssertionError("application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            prefab.get_unity_instance_from_context = AsyncMock(return_value="Selected@owned")
            metadata = [item for item in get_registered_resources() if item["name"] == {resource_name!r}]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("prefab-path-contract")
            resources.register_all_resources(server)
            requests = []
            reply = {{"success": True, "data": {{"extra_native_field": {{"keep": True}}}}}}
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, dict(params)))
                return reply
            PluginHub.send_command_for_instance = send
            paths = (
                "Assets/Prefabs/Player.prefab", "Assets/Prefabs/My Player.prefab",
                "Assets/Prefabs/Icon%20Small.prefab", "Assets/Prefabs/Icon%2FSmall.prefab",
                "Assets/Prefabs/Rate%25.prefab", "Assets/Prefabs/Literal%23.prefab",
                "Assets/Prefabs/Hash#Name.prefab", "Assets/Prefabs/\ud55c\uae00.prefab",
                "Assets/Prefabs/100%Ready.prefab",
            )
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for path in paths:
                        # When Client reads the correctly percent-encoded public URI.
                        uri = "mcpforunity://prefab/" + quote(path, safe="") + {suffix!r}
                        result = json.loads((await client.read_resource(uri))[0].text)
                        # Then Unity receives the identical asset path exactly once.
                        assert requests[-1] == ("Selected@owned", "manage_prefabs", {{"action": {action!r}, "prefabPath": path, **{paging_params!r}}}), requests[-1]
                        assert result["success"] is True and result["data"] == reply["data"]
                    reply = {{"success": False, "error": "fixture_missing", "hint": "retry", "data": {{"reason": "reloading", "retry_after_ms": 123}}}}
                    result = json.loads((await client.read_resource("mcpforunity://prefab/" + quote(paths[2], safe="") + {suffix!r}))[0].text)
                    for key, value in reply.items(): assert result[key] == value
                    assert requests[-1][2]["prefabPath"] == paths[2]
                    reply = {{"success": True, "data": {{"extra_native_field": {{"keep": True}}}}}}
            assert len(requests) == 20
        anyio.run(scenario)
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_registered_prefab_hierarchy_continues_bounded_pages_and_validates_queries(tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    source = """
        import json, os, socket, sys
        from pathlib import Path
        from urllib.parse import quote, urlencode
        from unittest.mock import AsyncMock
        home = Path(os.environ["APPDATA"]).parent / "owned-home"
        home.mkdir()
        Path.home = lambda: home
        sys.path.insert(0, "src")
        import anyio
        from fastmcp import Client, FastMCP
        from fastmcp.exceptions import ResourceError
        from mcp.shared.exceptions import MCPError
        from core.config import config
        import services.resources as resources
        from services.resources import prefab
        from services.registry import get_registered_resources
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs): raise AssertionError("application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            prefab.get_unity_instance_from_context = AsyncMock(return_value="Selected@owned")
            metadata = [item for item in get_registered_resources() if item["name"] == "prefab_hierarchy"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("prefab-paging-contract")
            resources.register_all_resources(server)
            requests = []
            path = "Assets/Prefabs/Rate%20 ?&#.prefab"
            items = [{"name": f"Object{i}", "hierarchyKey": str(i)} for i in range(123)]
            async def send(instance, command, params, **kwargs):
                assert instance == "Selected@owned" and command == "manage_prefabs"
                assert params["prefabPath"] == path and params["action"] == "get_hierarchy"
                requests.append(dict(params))
                start = int(params["cursor"], 16) if "cursor" in params else 0
                end = min(start + params["page_size"], len(items))
                assert 1 <= params["page_size"] <= 500 and 0 <= params["max_depth"] <= 128
                return {"success": True, "data": {"items": items[start:end],
                    "total": len(items) if end == len(items) else None,
                    "next_cursor": f"{end:032x}" if end < len(items) else None,
                    "truncated": end < len(items), "extra_native_field": "keep"}}
            PluginHub.send_command_for_instance = send
            base = "mcpforunity://prefab/" + quote(path, safe="") + "/hierarchy"
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    templates = await client.list_resource_templates()
                    assert templates[0].uri_template.endswith("{?page_size,cursor,max_depth}")
                    first = json.loads((await client.read_resource(base))[0].text)
                    assert len(first["data"]["items"]) == 50
                    assert requests[-1] == {"action": "get_hierarchy", "prefabPath": path,
                        "page_size": 50, "max_depth": 64}
                    partial = json.loads((await client.read_resource(
                        base + "?cursor=" + first["data"]["next_cursor"]))[0].text)
                    assert len(partial["data"]["items"]) == 50
                    assert requests[-1] == {"action": "get_hierarchy", "prefabPath": path,
                        "page_size": 50, "max_depth": 64, "cursor": f"{50:032x}"}
                    boundary = json.loads((await client.read_resource(base + "?page_size=1&max_depth=0"))[0].text)
                    assert len(boundary["data"]["items"]) == 1
                    assert requests[-1]["max_depth"] == 0
                    names = []
                    cursor = None
                    for _ in range(3):
                        query = {"page_size": 50, "max_depth": 7}
                        if cursor is not None: query["cursor"] = cursor
                        page = json.loads((await client.read_resource(base + "?" + urlencode(query)))[0].text)
                        assert page["data"]["extra_native_field"] == "keep"
                        assert requests[-1] == {"action": "get_hierarchy", "prefabPath": path, **query}
                        names.extend(item["hierarchyKey"] for item in page["data"]["items"])
                        cursor = page["data"]["next_cursor"]
                    assert names == [item["hierarchyKey"] for item in items] and cursor is None
                    before = len(requests)
                    for query in ("page_size=0", "page_size=501", "max_depth=-1", "max_depth=129",
                                  "page_size=bad", "max_depth=bad", "cursor=", "cursor=invalid"):
                        try:
                            invalid = json.loads((await client.read_resource(base + "?" + query))[0].text)
                        except (ResourceError, MCPError):
                            pass
                        else:
                            assert invalid["success"] is False, (query, invalid)
                        assert len(requests) == before, query
                    async def stale(instance, command, params, **kwargs):
                        assert params["cursor"] == "f" * 32
                        return {"success": False, "error": "stale_cursor", "hint": "restart"}
                    PluginHub.send_command_for_instance = stale
                    stale_page = json.loads((await client.read_resource(base + "?cursor=" + "f" * 32))[0].text)
                    assert stale_page["error"] == "stale_cursor" and stale_page["hint"] == "restart"
                    PluginHub.send_command_for_instance = send
        anyio.run(scenario)
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
