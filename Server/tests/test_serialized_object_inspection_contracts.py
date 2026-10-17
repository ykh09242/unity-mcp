"""Real SDK serialized inspection contracts; outbound Unity is inert and state is owned."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def run_contract(source, mode, tmp_path):
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "UNITY_MCP_LOG_DIR": str(tmp_path / "log"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", "MODE=" + repr(mode) + "\n" + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_native_inspection_alias_is_reserved_but_raw_descriptor_survives(mode, tmp_path):
    run_contract(
        r"""
        import asyncio, socket
        from fastmcp import Client, FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
        from services.custom_tool_service import CustomToolService
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        async def main():
            def deny(*args, **kwargs): raise AssertionError("Application network forbidden")
            socket.socket.connect = deny
            config.http_remote_hosted = False
            config.transport_mode = "http"
            PluginHub.configure(PluginRegistry())
            server = FastMCP("serialized-inspection-reservation")
            service = CustomToolService(server)
            descriptor = ToolDefinitionModel(name="inspect_serialized_object_v1", description="native-only fixture")
            service.register_global_tools([descriptor])
            raw = await service.get_tool_definition("owned", descriptor.name)
            assert raw is not None and raw.name == descriptor.name
            async with Client(server, mode=MODE) as client:
                names = {tool.name for tool in await client.list_tools()}
                assert descriptor.name not in names, "The native alias must not bypass the public inspector wrapper"
            print("INSPECTION_ALIAS_RESERVED", MODE)
        asyncio.run(main())
    """,
        mode,
        tmp_path,
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_registered_inspector_strict_projection_wire_and_old_peer(mode, tmp_path):
    run_contract(
        r"""
        import asyncio, copy, importlib.util, json, socket
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.tools as tools
        from services.registry import get_registered_tools
        from services.custom_tool_service import CustomToolService
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        assert importlib.util.find_spec("services.tools.inspect_serialized_object") is not None, "The public serialized inspector is missing"
        from services.tools import inspect_serialized_object as module
        async def main():
            def deny(*args, **kwargs): raise AssertionError("Application network forbidden")
            socket.socket.connect = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            module.preflight = AsyncMock(return_value=None)
            module.get_unity_instance_from_context = AsyncMock(return_value="Owned@fixture")
            metadata = [x for x in get_registered_tools() if x["name"] == "inspect_serialized_object"]
            assert len(metadata) == 1 and metadata[0]["unity_target"] == "inspect_serialized_object_v1"
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            server = FastMCP("serialized-inspector-contract")
            tools.register_all_tools(server)
            server.enable(tags={"group:scripting_ext"}, components={"tool"})
            PluginHub.configure(PluginRegistry())
            service = CustomToolService(server)
            service.register_global_tools([ToolDefinitionModel(name="inspect_serialized_object_v1")])
            calls, options = [], []
            reply = {"success":False,"code":"unknown_command","data":{"reason":"old_peer","zero":0,"false":False,"null":None}}
            lose = False
            async def send(instance, command, params, **kwargs):
                calls.append((instance, command, copy.deepcopy(params)))
                options.append(kwargs)
                if lose: raise TimeoutError("Controlled response loss")
                return copy.deepcopy(reply)
            PluginHub.send_command_for_instance = send
            async with Client(server, mode=MODE) as client:
                listed = await client.list_tools()
                assert {tool.name for tool in listed} == {"inspect_serialized_object"}
                tool = listed[0]
                # The lookup may load an asset and invoke arbitrary native callbacks.
                # Assert the public serialized hints in both SDK modes, not just decorator data.
                hints = tool.annotations.model_dump(by_alias=True)
                assert hints["readOnlyHint"] is False, hints
                assert hints["destructiveHint"] is True, hints
                assert hints["openWorldHint"] is False, hints
                assert "load" in tool.description.lower() and "callback" in tool.description.lower()
                for args, wire in [
                    ({"instance_id":-1}, {"instance_id":-1,"mode":"fields","property_path":"","include_values":True,"page_size":32}),
                    ({"instance_id":2,"mode":"value","property_path":"secret","include_values":False}, {"instance_id":2,"mode":"value","property_path":"secret","include_values":False}),
                    ({"instance_id":2,"mode":"array","property_path":"groups.Array.data[1].values","start_index":99997,"page_size":3}, {"instance_id":2,"mode":"array","property_path":"groups.Array.data[1].values","include_values":True,"start_index":99997,"page_size":3}),
                    ({"instance_id":2,"after_path":"secret","page_size":1}, {"instance_id":2,"mode":"fields","property_path":"","include_values":True,"after_path":"secret","page_size":1}),
                ]:
                    before = len(calls)
                    result = await client.call_tool("inspect_serialized_object", args)
                    assert json.loads(result.content[0].text) == reply
                    assert calls[before:] == [("Owned@fixture","inspect_serialized_object_v1",wire)]
                bad = [
                    {}, {"instance_id":None}, {"instance_id":True}, {"instance_id":"2"}, {"instance_id":2.0},
                    {"instance_id":0}, {"instance_id":2147483648}, {"instance_id":-2147483649},
                    {"instance_id":2,"mode":"VALUE"}, {"instance_id":2,"mode":None},
                    {"instance_id":2,"property_path":None}, {"instance_id":2,"property_path":"x"*513},
                    *[{"instance_id":2,"property_path":path} for path in ("nested..marker", "items.Array.data[01]", "items.Array.data[-1]", "items.Array.data[2147483648]", "x."*16+"x", " secret")],
                    {"instance_id":2,"property_path":"\U0001F600"*257},
                    {"instance_id":2,"include_values":1}, {"instance_id":2,"include_values":None},
                    {"instance_id":2,"page_size":0}, {"instance_id":2,"page_size":65}, {"instance_id":2,"page_size":True},
                    {"instance_id":2,"mode":"value","property_path":"secret","page_size":1},
                    {"instance_id":2,"mode":"value","property_path":""},
                    {"instance_id":2,"start_index":0}, {"instance_id":2,"after_path":None},
                    {"instance_id":2,"mode":"array","property_path":"items","after_path":"items"},
                    {"instance_id":2,"mode":"array","property_path":"items","start_index":-1},
                    {"instance_id":2,"mode":"array","property_path":"items","start_index":2147483648},
                    {"instance_id":2,"mode":"array","property_path":"items","start_index":False},
                    {"instance_id":2,"unknown":1}, {"instance_id":2,"instanceID":2},
                ]
                for args in bad:
                    before, gate = len(calls), module.preflight.await_count
                    result = await client.call_tool("inspect_serialized_object", args, raise_on_error=False)
                    assert result.is_error or json.loads(result.content[0].text)["success"] is False, args
                    assert len(calls) == before and module.preflight.await_count == gate, args
                lose = True
                before = len(calls)
                await client.call_tool("inspect_serialized_object", {"instance_id":2}, raise_on_error=False)
                assert len(calls) == before + 1
                assert all(option.get("retry_on_reload") is False for option in options)
                assert all(call.kwargs == {"wait_for_no_compile":True,"refresh_if_dirty":False} for call in module.preflight.await_args_list)
            print("INSPECTION_SDK_PASS", MODE)
        asyncio.run(main())
    """,
        mode,
        tmp_path,
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_actual_http_native_catalog_controls_inspection_alias(mode, tmp_path):
    source = r"""
        import asyncio, copy, json, socket, time
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from starlette.websockets import WebSocketState
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.tools as tools
        from services.tools import inspect_serialized_object as module
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.unity_instance_middleware import UnityInstanceMiddleware

        async def main():
            def deny(*args, **kwargs): raise AssertionError("Application network forbidden")
            socket.socket.connect = deny
            config.transport_mode = "http"
            config.http_remote_hosted = True
            import transport.unity_transport as transport
            # The SDK fixture has no HTTP request/API key; inject its authenticated user only.
            transport._resolve_user_id_from_request = AsyncMock(return_value="reader")
            module.preflight = AsyncMock(return_value=None)
            metadata = [x for x in get_registered_tools() if x["name"] == "inspect_serialized_object"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            server = FastMCP("serialized-inspector-native-catalog")
            tools.register_all_tools(server)
            registry = PluginRegistry()
            PluginHub.configure(registry)
            await registry.register("fixture", "Owned", "fixture", "6000", user_id="reader")
            await registry.register_tools_for_session("fixture", [ToolDefinitionModel(name="inspect_serialized_object_v1")])
            websocket = SimpleNamespace(state=SimpleNamespace(plugin_registered=True, plugin_session_id="fixture", plugin_generation="fixture-generation", user_id="reader"), application_state=WebSocketState.CONNECTED, client_state=WebSocketState.CONNECTED, close=AsyncMock())
            PluginHub._connections["fixture"] = websocket
            PluginHub._last_pong["fixture"] = time.monotonic()
            middleware = UnityInstanceMiddleware()
            middleware._resolve_user_id = AsyncMock(return_value="reader")
            server.add_middleware(middleware)
            requests = []
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, copy.deepcopy(params)))
                return {"success":True,"data":{"handle":"opaque-native"}}
            PluginHub.send_command_for_instance = send
            try:
                async with Client(server, mode=MODE) as client:
                    assert {x.name for x in await client.list_tools()} == {"inspect_serialized_object"}
                    args = {"instance_id":-123,"unity_instance":"Owned@fixture"}
                    result = await client.call_tool("inspect_serialized_object", args)
                    assert json.loads(result.content[0].text)["success"] is True, (result, requests)
                    assert requests == [("Owned@fixture","inspect_serialized_object_v1",{"instance_id":-123,"mode":"fields","property_path":"","page_size":32,"include_values":True})]
                    # Real native membership changes while the SDK client retains its public tool schema.
                    await registry.register_tools_for_session("fixture", [])
                    assert "inspect_serialized_object" not in {x.name for x in await client.list_tools()}
                    before = len(requests)
                    from mcp.shared.exceptions import MCPError
                    try:
                        result = await client.call_tool("inspect_serialized_object", args, raise_on_error=False)
                    except MCPError as error:
                        assert "disabled or unavailable" in str(error) or (MODE == "2026-07-28" and error.code == -32603), repr(error)
                    else:
                        assert result.is_error
                    assert len(requests) == before, "Missing native capability must be refused before target transport"
                    await registry.register_tools_for_session("fixture", [ToolDefinitionModel(name="inspect_serialized_object_v1")])
                    assert "inspect_serialized_object" in {x.name for x in await client.list_tools()}
                print("INSPECTION_HTTP_CATALOG_PASS", MODE)
            finally:
                await PluginHub.shutdown()
        asyncio.run(main())
    """
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "UNITY_MCP_LOG_DIR": str(tmp_path / "log"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
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
