"""HTTP custom-tool project resolution must not inspect the stdio host."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from core.config import config
import services.custom_tool_service as module
from services.custom_tool_service import CustomToolService
from transport.legacy.port_discovery import PortDiscovery
import transport.legacy.unity_connection as uc


@pytest.fixture
def resolution(monkeypatch):
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(SimpleNamespace(custom_route=lambda *_a, **_k: lambda fn: fn))
    pool = uc.UnityConnectionPool()
    state = SimpleNamespace(now=1000.0, scans=0, instances=[], service=service)

    def scan():
        state.scans += 1
        return state.instances

    monkeypatch.setattr(module, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(uc, "time", SimpleNamespace(time=lambda: state.now))
    monkeypatch.setattr(PortDiscovery, "discover_all_unity_instances", scan)
    return state


@pytest.mark.parametrize("hosted", [False, True])
def test_http_resolution_never_scans_stdio_even_after_cache_expiry(resolution, monkeypatch, hosted):
    monkeypatch.setattr(config, "transport_mode", "HTTP")
    monkeypatch.setattr(config, "http_remote_hosted", hosted)
    for _ in range(100):
        assert module.resolve_project_id_for_unity_instance("Remote@AABBCCDD") == "aabbccdd"
        resolution.now += 6
    assert resolution.scans == 0


@pytest.mark.parametrize(
    "selector,expected",
    [
        (None, None),
        ("", None),
        ("Remote@", None),
        ("AABBCCDD", "aabbccdd"),
        ("Remote@AABBCCDD", "aabbccdd"),
    ],
)
def test_http_selector_fallback_contract(resolution, monkeypatch, selector, expected):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    assert module.resolve_project_id_for_unity_instance(selector) == expected
    assert resolution.scans == 0


def test_local_http_mapping_changes_are_read_without_stale_resolution_cache(
    resolution, monkeypatch
):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    resolution.service._register_project_tools("first-project", [], project_hash="AABBCCDD")
    assert module.resolve_project_id_for_unity_instance("Remote@AABBCCDD") == "first-project"
    resolution.service._register_project_tools("replacement-project", [], project_hash="AABBCCDD")
    assert module.resolve_project_id_for_unity_instance("Remote@AABBCCDD") == "replacement-project"
    assert resolution.scans == 0


@pytest.mark.parametrize("selector", ["Main@deadbeef", "deadbeef", "dead"])
def test_stdio_keeps_discovery_and_refreshes_expired_inventory(resolution, monkeypatch, selector):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    resolution.instances = [SimpleNamespace(id="Main@deadbeef", name="Main", hash="deadbeef")]
    assert module.resolve_project_id_for_unity_instance(selector) == "deadbeef"
    assert resolution.scans == 1
    assert module.resolve_project_id_for_unity_instance(selector) == "deadbeef"
    assert resolution.scans == 1
    resolution.now += 6
    resolution.instances = []
    assert module.resolve_project_id_for_unity_instance(selector) == selector.rsplit("@", 1)[-1]
    assert resolution.scans == 2


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("hosted", [False, True])
def test_sdk_resource_and_execution_follow_http_reconnect_catalog(tmp_path, mode, hosted):
    # Collection-time SDK shims in legacy tests require a fresh real-SDK process.
    source = r"""
    import asyncio, json, sys, time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    sys.path.insert(0, "src")
    from fastmcp import Client, FastMCP
    from starlette.websockets import WebSocketState
    from core.config import config
    import services.custom_tool_service as service_module
    import services.tools as tools
    import services.resources as resources
    import services.tools.execute_custom_tool as execute
    import services.resources.custom_tools as listing
    from models.models import ToolDefinitionModel
    from services.registry import get_registered_tools, get_registered_resources
    from transport.models import CommandResultMessage
    from transport.plugin_hub import PluginHub
    from transport.plugin_registry import PluginRegistry
    from transport.response_limit_middleware import ResponseLimitMiddleware
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    async def scenario():
        config.transport_mode = "http"
        config.http_remote_hosted = HOSTED
        user = "alice" if HOSTED else None
        scans = []
        def forbidden_pool():
            scans.append(1)
            # A local stdio editor may share the HTTP name/hash prefix but is not
            # part of the HTTP registry, including on a remotely hosted server.
            return SimpleNamespace(discover_all_instances=lambda: [
                SimpleNamespace(id="Remote@aabbccddff", name="Remote", hash="aabbccddff"),
                SimpleNamespace(id="Local@aabb0000", name="Local", hash="aabb0000")])
        service_module.get_unity_connection_pool = forbidden_pool
        execute.get_unity_instance_from_context = AsyncMock(return_value="Remote@aabbccdd")
        listing.get_unity_instance_from_context = AsyncMock(return_value="Remote@aabbccdd")
        execute.get_user_id_from_context = AsyncMock(return_value=user)
        listing.get_user_id_from_context = AsyncMock(return_value=user)
        server = FastMCP("http-project-resolution")
        server.add_middleware(ResponseLimitMiddleware())
        service_module.CustomToolService(server)
        tool_metadata = [t for t in get_registered_tools() if t["name"] == "execute_custom_tool"]
        resource_metadata = [r for r in get_registered_resources() if r["name"] == "custom_tools"]
        tools.discover_modules = resources.discover_modules = lambda *_a: []
        tools.get_registered_tools = lambda: tool_metadata
        resources.get_registered_resources = lambda: resource_metadata
        tools.register_all_tools(server)
        resources.register_all_resources(server)
        registry = PluginRegistry()
        PluginHub.configure(registry)
        endpoint = PluginHub.__new__(PluginHub)
        calls = []
        generation = [1]

        async def register(session_id):
            websocket = SimpleNamespace(state=SimpleNamespace(plugin_registered=True,
                plugin_session_id=session_id, plugin_generation=session_id, user_id=user),
                application_state=WebSocketState.CONNECTED, client_state=WebSocketState.CONNECTED,
                close=AsyncMock())
            async def send_json(message):
                calls.append((session_id, message["params"]))
                await endpoint._handle_command_result(websocket,
                    CommandResultMessage(id=message["id"], result={"success":True,
                        "data":{"generation":generation[0]}}))
            websocket.send_json = send_json
            await registry.register(session_id, "Remote", "aabbccdd", "6000", user_id=user)
            await registry.register_tools_for_session(session_id, [ToolDefinitionModel(name="probe")])
            PluginHub._connections[session_id] = websocket
            PluginHub._last_pong[session_id] = time.monotonic()

        async def read(client):
            return json.loads((await client.read_resource("mcpforunity://custom-tools"))[0].text)
        async def call(client):
            result = await client.call_tool("execute_custom_tool", {"tool_name":"probe"})
            return json.loads(result.content[0].text)
        await register("first")
        middleware = UnityInstanceMiddleware()
        selector_ctx = SimpleNamespace(get_state=AsyncMock(return_value=user))
        if HOSTED:
            await registry.register("bob", "OtherTenant", "aabbffff", "6000", user_id="bob")
        resolved = await middleware._resolve_instance_value("AABB", selector_ctx)
        assert resolved == "Remote@aabbccdd"
        execute.get_unity_instance_from_context.return_value = resolved
        listing.get_unity_instance_from_context.return_value = resolved
        for rejected, message in (("Remote@aabb", "not found"), ("ffff", "No running Unity instance matches")):
            try:
                await middleware._resolve_instance_value(rejected, selector_ctx)
            except ValueError as exc:
                assert message in str(exc), str(exc)
            else:
                raise AssertionError(f"unexpected selector resolution: {rejected}")
        await registry.register("collision", "Other", "aabb1234", "6000", user_id=user)
        try:
            await middleware._resolve_instance_value("aabb", selector_ctx)
        except ValueError as exc:
            assert "ambiguous" in str(exc), str(exc)
        else:
            raise AssertionError("HTTP hash prefix must reject registry ambiguity")
        await registry.unregister("collision")
        try:
            async with Client(server, mode=MODE) as client:
                assert (await read(client))["data"]["tools"][0]["name"] == "probe"
                assert (await call(client))["data"]["generation"] == 1
                assert not scans, len(scans)
                await registry.unregister("first")
                PluginHub._connections.pop("first")
                assert (await read(client))["data"]["tool_count"] == 0
                generation[0] = 2
                await register("replacement")
                assert (await read(client))["data"]["tool_count"] == 1
                assert (await call(client))["data"]["generation"] == 2
                assert [session for session, _ in calls] == ["first", "replacement"]
                assert not scans, len(scans)
                assert not PluginHub._pending
        finally:
            await PluginHub.shutdown()
    asyncio.run(scenario())
    """.replace("HOSTED", repr(hosted)).replace("mode=MODE", "mode=" + repr(mode))
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
