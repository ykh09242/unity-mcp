"""Server and registration lifetimes release their owned plugin resources."""

import asyncio  # noqa: ANYIO_OK -- inspect hub-owned task lifetimes.
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP

import main
from core.config import config
from transport.models import RegisterMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def lifecycle_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    monkeypatch.setattr(main.threading, "Timer", Mock())
    monkeypatch.setattr(main, "_plugin_registry", None)
    monkeypatch.setattr(main, "_unity_connection_pool", None)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    yield
    tasks = list(PluginHub._ping_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def socket_stub():
    return SimpleNamespace(state=SimpleNamespace(), send_json=AsyncMock(), close=AsyncMock())


@pytest.mark.asyncio
async def test_server_shutdown_releases_plugin_commands_and_ping_tasks(lifecycle_state) -> None:
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    ws = socket_stub()
    sent = asyncio.Event()

    async def send(payload):
        if payload.get("name") == "manage_scene":
            sent.set()

    ws.send_json.side_effect = send
    command = None
    try:
        async with main.server_lifespan(FastMCP("shutdown")) as state:
            registry = state["plugin_registry"]
            await hub._handle_register(ws, RegisterMessage(project_hash="project"))
            session_id = next(iter(PluginHub._connections))
            ping_task = PluginHub._ping_tasks[session_id]
            command = asyncio.create_task(PluginHub.send_command(session_id, "manage_scene", {}))
            await asyncio.wait_for(sent.wait(), timeout=1)
        assert PluginHub._connections == {}
        assert PluginHub._ping_tasks == {}
        assert PluginHub._pending == {}
        assert PluginHub._last_pong == {}
        assert ping_task.done()
        assert await registry.list_sessions() == {}
        assert not PluginHub.is_configured()
        ws.close.assert_awaited_once()
        done, _ = await asyncio.wait({command}, timeout=0.2)
        assert command in done
        assert command.result()["hint"] == "retry"
    finally:
        if command is not None:
            command.cancel()
            await asyncio.gather(command, return_exceptions=True)


@pytest.mark.asyncio
async def test_next_lifespan_rebinds_hub_to_current_server(lifecycle_state) -> None:
    first = FastMCP("first")
    second = FastMCP("second")
    async with main.server_lifespan(first):
        assert PluginHub._mcp is first
    async with main.server_lifespan(second):
        assert PluginHub._mcp is second


@pytest.mark.asyncio
async def test_failed_replacement_ack_still_closes_evicted_socket(lifecycle_state) -> None:
    registry = PluginRegistry()
    PluginHub.configure(registry)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old = socket_stub()
    new = socket_stub()
    await hub._handle_register(old, RegisterMessage(project_hash="same"))
    new.send_json.side_effect = OSError("ack failed")
    try:
        with pytest.raises(OSError, match="ack failed"):
            await hub._handle_register(new, RegisterMessage(project_hash="same"))
        old.close.assert_awaited_once()
    finally:
        # Match WebSocketEndpoint.dispatch's disconnect cleanup on handler failure.
        await hub.on_disconnect(new, 1001)


@pytest.mark.asyncio
async def test_cancelled_startup_releases_configured_hub_and_pool(lifecycle_state, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "0")
    monkeypatch.setattr(config, "transport_mode", "stdio")
    pool = Mock()
    pool.discover_all_instances.return_value = [SimpleNamespace(id="Fixture@hash")]
    monkeypatch.setattr(main, "get_unity_connection_pool", Mock(return_value=pool))
    entered = asyncio.Event()

    async def blocked_sync(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("services.tools.sync_tool_visibility_from_unity", blocked_sync)

    async def start():
        async with main.server_lifespan(FastMCP("cancel-startup")):
            pytest.fail("Blocked startup must not yield")

    startup = asyncio.create_task(start())
    await asyncio.wait_for(entered.wait(), timeout=1)
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup
    assert not PluginHub.is_configured()
    pool.disconnect_all.assert_called_once()
    assert main._unity_connection_pool is None
    main.threading.Timer.return_value.cancel.assert_called()


@pytest.mark.asyncio
async def test_cancelled_replacement_ack_still_closes_evicted_socket(lifecycle_state) -> None:
    PluginHub.configure(PluginRegistry())
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old, new = socket_stub(), socket_stub()
    await hub._handle_register(old, RegisterMessage(project_hash="same"))
    entered = asyncio.Event()

    async def blocked_ack(payload):
        entered.set()
        await asyncio.Event().wait()

    new.send_json.side_effect = blocked_ack
    registration = asyncio.create_task(hub._handle_register(new, RegisterMessage(project_hash="same")))
    await asyncio.wait_for(entered.wait(), timeout=1)
    registration.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await registration
        old.close.assert_awaited_once()
    finally:
        await hub.on_disconnect(new, 1001)


@pytest.mark.asyncio
async def test_shutdown_bounds_socket_close_and_is_idempotent(lifecycle_state, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = PluginRegistry()
    PluginHub.configure(registry)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    ws = socket_stub()
    stopped = asyncio.Event()

    async def blocked_close(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    ws.close.side_effect = blocked_close
    monkeypatch.setattr(PluginHub, "CLOSE_TIMEOUT", 0.02)
    await hub._handle_register(ws, RegisterMessage(project_hash="bounded"))
    await asyncio.wait_for(PluginHub.shutdown(), timeout=0.2)
    assert stopped.is_set()
    assert not PluginHub.is_configured()
    assert await registry.list_sessions() == {}
    await PluginHub.shutdown()
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_fastmcp_client_lifespan_releases_plugin_resources(lifecycle_state) -> None:
    server = FastMCP("real-lifecycle", lifespan=main.server_lifespan)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    ws = socket_stub()
    async with Client(server):
        await hub._handle_register(ws, RegisterMessage(project_hash="real-sdk"))
        assert PluginHub._connections
    assert PluginHub._connections == {}
    assert PluginHub._ping_tasks == {}
    assert main._plugin_registry is None
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_shutdown_clears_all_tenants_without_unscoped_listing(lifecycle_state, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "http_remote_hosted", True)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    sockets = []
    async with main.server_lifespan(FastMCP("tenant-shutdown")) as state:
        registry = state["plugin_registry"]
        for user in ("alice", "bob"):
            ws = socket_stub()
            ws.state.user_id = user
            sockets.append(ws)
            await hub._handle_register(ws, RegisterMessage(project_hash="shared"))
    for user, ws in zip(("alice", "bob"), sockets):
        assert await registry.list_sessions(user_id=user) == {}
        assert await registry.get_session_id_by_hash("shared", user_id=user) is None
        ws.close.assert_awaited_once()
