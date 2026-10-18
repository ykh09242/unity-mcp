"""Real SDK object-handle registration/schema/wire contracts; outbound Unity is inert."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_object_handle_registered_tool_and_resource(mode, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[key] = str(tmp_path / key)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    source = r"""
        import copy, importlib.util, json, socket
        from unittest.mock import AsyncMock
        import anyio
        from fastmcp import Client, FastMCP
        from fastmcp.server.middleware import Middleware
        from core.config import config
        import services.tools as tools
        from services.registry import get_registered_tools
        from services.resources import register_all_resources
        from transport.plugin_hub import PluginHub
        from transport.unity_instance_middleware import UnityInstanceMiddleware

        assert importlib.util.find_spec("services.tools.manage_object_handles") is not None, "The public object-handle API is missing"
        from services.tools import manage_object_handles as module
        import services.resources.gameobject as resources
        async def main():
            def deny(*args, **kwargs): raise AssertionError("Application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            module.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
            resources.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
            module.preflight = AsyncMock(return_value=None)
            metadata = [x for x in get_registered_tools() if x["name"] == "manage_object_handles"]
            assert len(metadata) == 1
            assert metadata[0]["unity_target"] == "manage_object_handles_v2"
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            server = FastMCP("object-handle-contract")
            tools.register_all_tools(server)
            register_all_resources(server)
            visibility = UnityInstanceMiddleware()
            visibility._refresh_tool_visibility_metadata_from_registry()
            assert visibility._is_tool_visible("manage_object_handles", {"manage_object_handles_v2"})
            assert not visibility._is_tool_visible("manage_object_handles", set())
            assert not visibility._is_tool_visible("manage_object_handles", {"get_gameobject"})
            assert not visibility._is_tool_visible("manage_object_handles", {"manage_object_handles_v1"})
            assert visibility._tool_alias_to_unity_target["manage_object_handles"] == "manage_object_handles_v2"
            requests, options = [], []
            reply = {"success": False, "code": "unknown_command", "error": "unknown_command", "data": {"reason": "old_peer", "zero": 0, "false": False, "null": None}}
            lost = False
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, copy.deepcopy(params)))
                options.append(kwargs)
                if lost: raise TimeoutError("Controlled response loss")
                return copy.deepcopy(reply)
            PluginHub.send_command_for_instance = send
            token = "mcp-oh-v2." + "a" * 32 + "_" + "b" * 32
            async with Client(server, mode=MODE) as client:
                listed = await client.list_tools()
                tool = next(x for x in listed if x.name == "manage_object_handles")
                assert tool.annotations.readOnlyHint is True
                assert tool.annotations.destructiveHint is False
                assert "session" in tool.description and "Undo/Redo" in tool.description
                assert {"action", "instance_id", "handle", "require_persistent", "allow_asset_load"} <= tool.input_schema["properties"].keys()
                for name in ("require_persistent", "allow_asset_load"):
                    assert "Unsupported legacy field" in tool.input_schema["properties"][name]["description"]
                templates = await client.list_resource_templates()
                assert any(str(x.uri_template) == "mcpforunity://scene/gameobject-by-handle/{handle}" for x in templates)
                for args,wire in [
                    ({"action":"capture","instance_id":-123}, {"action":"capture","instance_id":-123}),
                    ({"action":"resolve","handle":token}, {"action":"resolve","handle":token}),
                ]:
                    before = len(requests)
                    result = await client.call_tool("manage_object_handles", args)
                    assert json.loads(result.content[0].text) == reply
                    assert requests[before:] == [("Owned@fixture", "manage_object_handles_v2", wire)]
                bad = [
                    {"action":"capture"}, {"action":"capture","instance_id":None},
                    {"action":"capture","instance_id":True}, {"action":"capture","instance_id":"1"},
                    {"action":"capture","instance_id":1.0}, {"action":"capture","instance_id":0},
                    {"action":"capture","instance_id":2147483648}, {"action":"capture","instance_id":-2147483649},
                    {"action":"capture","instance_id":1,"handle":token},
                    {"action":"capture","instance_id":1,"allow_asset_load":False},
                    {"action":"capture","instance_id":1,"require_persistent":None},
                    {"action":"capture","instance_id":1,"require_persistent":"false"},
                    {"action":"resolve","handle":token,"instance_id":None},
                    {"action":"resolve","handle":token,"require_persistent":False},
                    {"action":"resolve","handle":None}, {"action":"resolve","handle":False},
                    {"action":"resolve","handle":""}, {"action":"resolve","handle":"x" * 1025},
                    {"action":"resolve","handle":token,"allow_asset_load":None},
                    {"action":"resolve","handle":token,"allow_asset_load":1},
                    {"action":"resolve","handle":token,"unknown":0}, {"action":"CAPTURE","instance_id":1},
                ]
                for action in ("capture", "resolve"):
                    for flag in ("require_persistent", "allow_asset_load"):
                        for value in (False, True, None, 0, "false"):
                            args = {"action":action, "instance_id":-123} if action == "capture" else {"action":action, "handle":token}
                            bad.append({**args, flag:value})
                bad.append({"action":"resolve", "handle":"mcp-oh-v1.eyJ2IjoxfQ"})
                for args in bad:
                    before, gates = len(requests), module.preflight.await_count
                    result = await client.call_tool("manage_object_handles", args, raise_on_error=False)
                    assert result.is_error or json.loads(result.content[0].text)["success"] is False, args
                    assert len(requests) == before and module.preflight.await_count == gates, args
                before = len(requests)
                decoded = json.loads((await client.read_resource("mcpforunity://scene/gameobject-by-handle/" + token))[0].text)
                assert decoded == reply
                assert requests[before:] == [("Owned@fixture", "get_gameobject_by_handle_v2", {"handle":token})]
                lost = True
                before = len(requests)
                await client.call_tool("manage_object_handles", {"action":"resolve","handle":token}, raise_on_error=False)
                assert len(requests) == before + 1
                assert all(option.get("retry_on_reload") is False for option in options)
                assert all(call.kwargs == {"wait_for_no_compile":True,"refresh_if_dirty":False} for call in module.preflight.await_args_list)
            print("HANDLE_SDK_PASS", MODE, len(requests))
        anyio.run(main)
    """
    result = subprocess.run(
        [sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_actual_http_native_catalog_controls_handle_alias(mode, tmp_path):
    source = r"""
        import asyncio, copy, json, socket, time
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from starlette.websockets import WebSocketState
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.tools as tools
        from services.tools import manage_object_handles as module
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.unity_instance_middleware import UnityInstanceMiddleware
        from contextlib import AsyncExitStack
        from tests.remote_credentials import remote_credentials

        async def main():
            def deny(*args, **kwargs): raise AssertionError("Application network forbidden")
            socket.socket.connect = deny
            config.transport_mode = "http"
            config.http_remote_hosted = True
            module.preflight = AsyncMock(return_value=None)
            metadata = [x for x in get_registered_tools() if x["name"] == "manage_object_handles"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            server = FastMCP("object-handle-native-catalog")
            tools.register_all_tools(server)
            registry = PluginRegistry()
            PluginHub.configure(registry)
            async with AsyncExitStack() as credentials:
                auth = await credentials.enter_async_context(remote_credentials({"requester:reader": "reader", "plugin:owned": "reader"}))
                await credentials.enter_async_context(auth.requester("requester:reader"))
                credentials.push_async_callback(PluginHub.shutdown)
                await registry.register("fixture", "Owned", "fixture", "6000", user_id="reader")
                await registry.register_tools_for_session("fixture", [ToolDefinitionModel(name="manage_object_handles_v2")])
                websocket = SimpleNamespace(state=SimpleNamespace(plugin_registered=True, plugin_session_id="fixture", plugin_generation="fixture-generation", user_id="reader"), application_state=WebSocketState.CONNECTED, client_state=WebSocketState.CONNECTED, close=AsyncMock())
                await auth.attach_socket(websocket, "plugin:owned")
                PluginHub._connections["fixture"] = websocket
                PluginHub._last_pong["fixture"] = time.monotonic()
                middleware = UnityInstanceMiddleware()
                server.add_middleware(middleware)
                requests = []
                async def send(instance, command, params, **kwargs):
                    requests.append((instance, command, copy.deepcopy(params)))
                    return {"success":True,"data":{"handle":"opaque-native"}}
                PluginHub.send_command_for_instance = send
                async with Client(server, mode=MODE) as client:
                    assert {x.name for x in await client.list_tools()} == {"manage_object_handles"}
                    args = {"action":"capture","instance_id":-123,"unity_instance":"Owned@fixture"}
                    result = await client.call_tool("manage_object_handles", args)
                    assert json.loads(result.content[0].text)["success"] is True, (result, requests)
                    assert requests == [("Owned@fixture","manage_object_handles_v2",{"action":"capture","instance_id":-123})]
                    # An older Editor advertises only v1 while the client retains its v2 public schema.
                    await registry.register_tools_for_session("fixture", [ToolDefinitionModel(name="manage_object_handles_v1")])
                    assert "manage_object_handles" not in {x.name for x in await client.list_tools()}
                    before = len(requests)
                    from mcp.shared.exceptions import MCPError
                    try:
                        result = await client.call_tool("manage_object_handles", args, raise_on_error=False)
                    except MCPError as error:
                        assert "disabled or unavailable" in str(error) or (MODE == "2026-07-28" and error.code == -32603), repr(error)
                    else:
                        assert result.is_error
                    assert len(requests) == before, "Missing native capability must be refused before target transport"
                    await registry.register_tools_for_session("fixture", [ToolDefinitionModel(name="manage_object_handles_v2")])
                    assert "manage_object_handles" in {x.name for x in await client.list_tools()}
                print("HANDLE_HTTP_CATALOG_PASS", MODE)
        asyncio.run(main())
    """
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "UNITY_MCP_LOG_DIR": str(tmp_path / "log"),
        "PYTHONPATH": os.pathsep.join(
            (
                str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / "src"),
            )
        ),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


"""Tests-first source draft for the owned public-contract test module; not executed."""

from unittest.mock import AsyncMock

import pytest

from services.tools import manage_object_handles as module


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["capture", "resolve"])
@pytest.mark.parametrize("flag", ["require_persistent", "allow_asset_load"])
@pytest.mark.parametrize("value", [False, True, None, 0, "false"])
async def test_obsolete_flag_presence_refuses_before_any_outbound_work(
    monkeypatch, action, flag, value
):
    context = AsyncMock()
    preflight = AsyncMock(return_value=None)
    send = AsyncMock()
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    arguments = (
        {"instance_id": -123}
        if action == "capture"
        else {"handle": "mcp-oh-v2." + "a" * 32 + "_" + "b" * 32}
    )
    response = await module.manage_object_handles(None, action, **arguments, **{flag: value})
    assert response["success"] is False
    assert response["data"]["reason"] == "invalid_parameters"
    context.assert_not_awaited()
    preflight.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "handle",
    [
        "mcp-oh-v1.eyJ2IjoxLCJrIjoic2NlbmUifQ",
        "mcp-oh-v3." + "a" * 32 + "_" + "b" * 32,
        "mcp-oh-v2." + "a" * 31 + "_" + "b" * 32,
        "mcp-oh-v2." + "A" * 32 + "_" + "b" * 32,
        "mcp-oh-v2." + "a" * 32 + "_" + "b" * 32 + "\n",
    ],
)
def test_transport_refuses_non_v2_and_malformed_session_tokens(handle):
    assert module.validate_handle(handle) is False


def test_transport_accepts_bounded_v2_session_token():
    assert module.validate_handle("mcp-oh-v2." + "a" * 32 + "_" + "b" * 32) is True


"""Append to the owned public-contract module for a focused semantic RED baseline."""


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize(
    "action,flag", [("capture", "require_persistent"), ("resolve", "allow_asset_load")]
)
def test_session_obsolete_false_rejected_by_actual_sdk(mode, action, flag, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[key] = str(tmp_path / key)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    binding = env.get("EDIT03_GUARD_MANIFEST")
    if binding is None:
        import json

        binding_path = tmp_path / "handle-source-binding.json"
        binding_path.write_text(
            json.dumps({"source": str(Path(__file__).resolve().parents[2])}), encoding="utf-8"
        )
        binding = str(binding_path)
    env["EDIT03_HANDLE_SOURCE_MANIFEST"] = binding
    source = r"""
        import json, socket, os
        from pathlib import Path
        from unittest.mock import AsyncMock
        import anyio
        from fastmcp import Client, FastMCP
        from services.registry import get_registered_tools
        import services.tools as tools
        from services.tools import manage_object_handles as module

        expected_module = Path(json.loads(Path(os.environ["EDIT03_HANDLE_SOURCE_MANIFEST"]).read_bytes())["source"]) / "Server/src/services/tools/manage_object_handles.py"
        assert Path(module.__file__).resolve() == expected_module.resolve()

        async def main():
            def deny(*args, **kwargs): raise AssertionError("Application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            module.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
            module.preflight = AsyncMock(return_value=None)
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"accepted_obsolete_flag": True}})
            metadata = [x for x in get_registered_tools() if x["name"] == "manage_object_handles"]
            assert len(metadata) == 1, "Captured original candidate must be present"
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            server = FastMCP("session-obsolete-false-baseline")
            tools.register_all_tools(server)
            # Both old broad validation and new session validation accept this shape.
            # GREEN must prove obsolete-flag rejection rather than rejection of an old token.
            arguments = ({"action": ACTION, "instance_id": -123} if ACTION == "capture"
                         else {"action": ACTION, "handle": "mcp-oh-v2." + "a" * 32 + "_" + "b" * 32})
            arguments[FLAG] = False
            async with Client(server, mode=MODE) as client:
                result = await client.call_tool("manage_object_handles", arguments, raise_on_error=False)
                response = json.loads(result.content[0].text)
                assert result.is_error or response.get("success") is False, "obsolete false flag reached outbound Unity dispatch"
                module.get_unity_instance_from_context.assert_not_awaited()
                module.preflight.assert_not_awaited()
                module.send_with_unity_instance.assert_not_awaited()
            print("SESSION_OBSOLETE_FALSE_PASS", MODE, ACTION, FLAG,
                  "module=" + str(Path(module.__file__).resolve()),
                  "context=" + str(module.get_unity_instance_from_context.await_count),
                  "preflight=" + str(module.preflight.await_count),
                  "outbound=" + str(module.send_with_unity_instance.await_count))
        anyio.run(main)
    """
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "MODE = "
            + repr(mode)
            + "\nACTION = "
            + repr(action)
            + "\nFLAG = "
            + repr(flag)
            + "\n"
            + textwrap.dedent(source),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
