"""Public plugin hub regressions using real WebSockets and inert ASGI I/O."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def hub_environment(monkeypatch, tmp_path):
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR", "UNITY_MCP_STATUS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import socket

    def deny(*args, **kwargs):
        raise AssertionError("No application network in plugin session tests")

    monkeypatch.setattr(socket, "create_connection", deny)
    hub_module = importlib.import_module("transport.plugin_hub")
    registry_module = importlib.import_module("transport.plugin_registry")
    monkeypatch.setattr(hub_module.config, "http_remote_hosted", False)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(hub_module.PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(hub_module.PluginHub, name, None)
    monkeypatch.setattr(hub_module.PluginHub, "CLOSE_TIMEOUT", 0.01)
    return hub_module.PluginHub, registry_module.PluginRegistry()


async def _register(hub, block_close=False):
    from starlette.websockets import WebSocket

    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "websocket.connect"})
    scope = {"type": "websocket", "path": "/owned-plugin", "headers": []}
    executes = []
    started = asyncio.Event()
    release = asyncio.Event()
    websocket = endpoint = None

    async def send(message):
        if message["type"] == "websocket.close" and block_close:
            started.set()
            await release.wait()
        elif message["type"] == "websocket.send":
            payload = json.loads(message["text"])
            if payload.get("type") == "execute":
                executes.append(payload)
                await endpoint.on_receive(
                    websocket,
                    {
                        "type": "command_result",
                        "id": payload["id"],
                        "result": {"status": "success", "result": {"controlled_reply": True}},
                    },
                )

    websocket = WebSocket(scope, incoming.get, send)
    endpoint = hub(scope, incoming.get, send)
    await websocket.accept()
    await endpoint.on_receive(
        websocket,
        {
            "type": "register",
            "project_name": "Other",
            "project_hash": "bbbbbbbb",
            "unity_version": "owned",
        },
    )
    return websocket, incoming, executes, started, release


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["Missing@", "@", "Missing@ \t"])
async def test_malformed_explicit_selector_does_not_auto_route(hub_environment, selector):
    hub, registry = hub_environment
    hub.configure(registry)
    _, _, executes, _, _ = await _register(hub)
    try:
        result = await hub.send_command_for_instance(
            selector, "owned_query", {}, retry_on_reload=False
        )
        assert executes == []
        assert result["success"] is False
        assert result["hint"] == "retry"
    finally:
        await hub.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [None, "", "bbbbbbbb", "Other@bbbbbbbb"])
async def test_default_and_valid_selectors_keep_successful_routing(hub_environment, selector):
    hub, registry = hub_environment
    hub.configure(registry)
    _, _, executes, _, _ = await _register(hub)
    try:
        result = await hub.send_command_for_instance(
            selector, "owned_query", {}, retry_on_reload=False
        )
        assert result["status"] == "success"
        assert len(executes) == 1
    finally:
        await hub.shutdown()


@pytest.mark.asyncio
async def test_stale_eviction_bounds_close_and_unregisters_before_io(hub_environment):
    hub, registry = hub_environment
    hub.configure(registry)
    ws, incoming, _, started, release = await _register(hub, block_close=True)
    incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})
    await ws.receive()
    command = asyncio.create_task(
        hub.send_command_for_instance("Other@bbbbbbbb", "owned_query", {}, retry_on_reload=False)
    )
    try:
        await asyncio.wait_for(started.wait(), 0.5)
        assert await registry.list_sessions() == {}, (
            "Stale registry state must be removed before close I/O"
        )
        done, _ = await asyncio.wait({command}, timeout=0.08)
        assert command in done, "Eviction must use the existing close timeout"
        assert command.result()["data"]["reason"] == "stale_connection"
        assert hub._connections == hub._pending == {}
    finally:
        release.set()
        await asyncio.gather(command, return_exceptions=True)
        await hub.shutdown()


@pytest.mark.asyncio
async def test_cancelled_stale_close_does_not_retain_registry_entry(hub_environment):
    hub, registry = hub_environment
    hub.configure(registry)
    ws, incoming, _, started, release = await _register(hub, block_close=True)
    incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})
    await ws.receive()
    command = asyncio.create_task(
        hub.send_command_for_instance("Other@bbbbbbbb", "owned_query", {}, retry_on_reload=False)
    )
    try:
        await asyncio.wait_for(started.wait(), 0.5)
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
        assert await registry.list_sessions() == {}
        assert hub._connections == hub._pending == {}
    finally:
        release.set()
        await asyncio.gather(command, return_exceptions=True)
        await hub.shutdown()
