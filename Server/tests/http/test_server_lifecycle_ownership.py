"""Server and registration lifetimes release their owned plugin resources."""

from tests.timing import await_signal, watchdog_timeout

import asyncio  # noqa: ANYIO_OK -- inspect hub-owned task lifetimes.
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP

import main
from core.config import config
from tests.remote_credentials import remote_credentials
from transport.models import RegisterMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def lifecycle_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    timer = Mock()
    monkeypatch.setattr(main.threading, "Timer", timer)
    monkeypatch.setattr(main, "_plugin_registry", None)
    monkeypatch.setattr(main, "_unity_connection_pool", None)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    yield timer
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
            await await_signal(sent, "sent")
        assert PluginHub._connections == {}
        assert PluginHub._ping_tasks == {}
        assert PluginHub._pending == {}
        assert PluginHub._last_pong == {}
        assert ping_task.done()
        assert await registry.list_sessions() == {}
        assert not PluginHub.is_configured()
        ws.close.assert_awaited_once()
        done, _ = await asyncio.wait({command}, timeout=watchdog_timeout())
        assert command in done
        assert command.result()["hint"] == "inspect_state_before_retry"
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
async def test_cancelled_startup_releases_configured_hub_and_pool(
    lifecycle_state, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    await await_signal(entered, "entered")
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup
    assert not PluginHub.is_configured()
    pool.disconnect_all.assert_called_once()
    assert main._unity_connection_pool is None
    lifecycle_state.return_value.cancel.assert_called()


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
    registration = asyncio.create_task(
        hub._handle_register(new, RegisterMessage(project_hash="same"))
    )
    await await_signal(entered, "entered")
    registration.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await registration
        old.close.assert_awaited_once()
    finally:
        await hub.on_disconnect(new, 1001)


@pytest.mark.asyncio
async def test_level_cancelled_replacement_releases_evicted_socket_admission(
    lifecycle_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PluginHub, "_admitted", {})
    PluginHub.configure(PluginRegistry())
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old, new = socket_stub(), socket_stub()
    await hub._handle_register(old, RegisterMessage(project_hash="same"))
    PluginHub._admitted[id(old)] = (old, None)
    old_closed = anyio.Event()
    ack_started = anyio.Event()
    cancelled = anyio.Event()

    async def close_old(**kwargs) -> None:
        await anyio.lowlevel.checkpoint()
        await hub.on_disconnect(old, 1001)
        old_closed.set()

    async def blocked_ack(payload) -> None:
        ack_started.set()
        await anyio.sleep_forever()

    async def register_replacement() -> None:
        try:
            await hub._handle_register(new, RegisterMessage(project_hash="same"))
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    old.close.side_effect = close_old
    new.send_json.side_effect = blocked_ack
    try:
        with anyio.fail_after(watchdog_timeout()):
            async with anyio.create_task_group() as group:
                group.start_soon(register_replacement)
                await ack_started.wait()
                group.cancel_scope.cancel()
        assert old_closed.is_set(), "Replacement cancellation must finish evicted socket closure"
        assert id(old) not in PluginHub._admitted
        assert cancelled.is_set(), "Cleanup must propagate registration cancellation"
    finally:
        await hub.on_disconnect(new, 1001)


@pytest.mark.asyncio
async def test_level_cancelled_socket_close_remains_bounded(
    lifecycle_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = socket_stub()
    stopped = anyio.Event()
    monkeypatch.setattr(PluginHub, "CLOSE_TIMEOUT", 0.02)

    async def blocked_close(**kwargs) -> None:
        try:
            await anyio.sleep_forever()
        finally:
            stopped.set()

    ws.close.side_effect = blocked_close
    with anyio.fail_after(watchdog_timeout()):
        with anyio.CancelScope() as scope:
            scope.cancel()
            await PluginHub._close_websocket(ws)
    assert stopped.is_set(), "Shielded close must still cancel blocked I/O at its deadline"


@pytest.mark.asyncio
async def test_shutdown_bounds_socket_close_and_is_idempotent(
    lifecycle_state, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    await asyncio.wait_for(PluginHub.shutdown(), timeout=watchdog_timeout())
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
async def test_remote_shutdown_clears_all_tenants_without_unscoped_listing(
    lifecycle_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "http_remote_hosted", True)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    sockets = []
    async with (
        remote_credentials({"plugin:alice": "alice", "plugin:bob": "bob"}) as auth,
        main.server_lifespan(FastMCP("tenant-shutdown")) as state,
    ):
        registry = state["plugin_registry"]
        for user in ("alice", "bob"):
            ws = socket_stub()
            ws.state.user_id = user
            await auth.attach_socket(ws, f"plugin:{user}")
            sockets.append(ws)
            await hub._handle_register(ws, RegisterMessage(project_hash="shared"))
            assert await registry.get_session_id_by_hash("shared", user_id=user) is not None
    for user, ws in zip(("alice", "bob"), sockets):
        assert await registry.list_sessions(user_id=user) == {}
        assert await registry.get_session_id_by_hash("shared", user_id=user) is None
        ws.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["graph", "state", "receipts", "scene"])
async def test_cleanup_failure_still_releases_every_independent_owner(
    lifecycle_state, monkeypatch, failure
):
    from contextlib import asynccontextmanager
    from services.api_key_service import ApiKeyService

    @asynccontextmanager
    async def lifetime():
        yield

    def fail(name):
        return RuntimeError(name + " cleanup") if failure == name else None

    server = FastMCP("independent-cleanup")
    graph = SimpleNamespace(aclose=AsyncMock(side_effect=fail("graph")))
    state = SimpleNamespace(lifespan=lifetime, clear=Mock(side_effect=fail("state")))
    receipts = SimpleNamespace(start=AsyncMock(), close=Mock(side_effect=fail("receipts")))
    scene = SimpleNamespace(close=Mock(side_effect=fail("scene")))
    validator = SimpleNamespace(aclose=AsyncMock())
    pool = SimpleNamespace(disconnect_all=Mock())
    server._reference_graph_service = graph
    server._unity_session_state_store = state
    server._execution_receipts = receipts
    server._scene_load_inputs = scene
    monkeypatch.setattr(ApiKeyService, "is_initialized", lambda: True)
    monkeypatch.setattr(ApiKeyService, "get_instance", lambda: validator)
    monkeypatch.setattr(main, "_unity_connection_pool", pool)
    shutdown = AsyncMock()
    monkeypatch.setattr(PluginHub, "shutdown", shutdown)
    with pytest.raises(RuntimeError, match=failure + " cleanup"):
        async with main.server_lifespan(server):
            pass
    graph.aclose.assert_awaited_once()
    state.clear.assert_called_once()
    receipts.close.assert_called_once()
    scene.close.assert_called_once()
    shutdown.assert_awaited_once()
    pool.disconnect_all.assert_called_once()
    validator.aclose.assert_awaited_once()
    lifecycle_state.return_value.cancel.assert_called_once()
    assert main._plugin_registry is None and main._unity_connection_pool is None


@pytest.mark.asyncio
async def test_repeated_shutdown_cancellation_cannot_abandon_independent_cleanup(
    lifecycle_state, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    server = FastMCP("cancel-cleanup")

    async def close_graph():
        entered.set()
        await release.wait()

    server._reference_graph_service = SimpleNamespace(aclose=close_graph)
    server._scene_load_inputs = SimpleNamespace(close=Mock())
    shutdown = AsyncMock()
    monkeypatch.setattr(PluginHub, "shutdown", shutdown)

    async def run():
        async with main.server_lifespan(server):
            pass

    pending = asyncio.create_task(run())
    try:
        await await_signal(entered, "graph cleanup")
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done(), "The caller must retain the actual cleanup after cancellation"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
    assert pending.cancelled()
    server._scene_load_inputs.close.assert_called_once()
    shutdown.assert_awaited_once()
    lifecycle_state.return_value.cancel.assert_called_once()
    assert main._plugin_registry is None


@pytest.mark.asyncio
async def test_level_cancelled_server_lifespan_finishes_owned_cleanup(lifecycle_state, monkeypatch):
    server = FastMCP("level-cancel-cleanup")
    finished = anyio.Event()

    async def close_graph():
        await anyio.lowlevel.checkpoint()
        finished.set()
        return True

    server._reference_graph_service = SimpleNamespace(aclose=close_graph)
    shutdown = AsyncMock()
    monkeypatch.setattr(PluginHub, "shutdown", shutdown)
    with anyio.fail_after(watchdog_timeout()):
        with anyio.CancelScope() as scope:
            async with main.server_lifespan(server):
                scope.cancel()
                await anyio.sleep_forever()
    assert finished.is_set()
    shutdown.assert_awaited_once()
    lifecycle_state.return_value.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_unsettled_graph_diagnostic_does_not_skip_server_cleanup(
    lifecycle_state, monkeypatch, caplog
):
    server = FastMCP("pending-graph-cleanup")
    graph = SimpleNamespace(
        aclose=AsyncMock(return_value=False),
        shutdown_status={
            "state": "draining",
            "pending_resolutions": 1,
            "pending_drains": 2,
        },
    )
    server._reference_graph_service = graph
    shutdown = AsyncMock()
    monkeypatch.setattr(PluginHub, "shutdown", shutdown)
    async with main.server_lifespan(server):
        pass
    assert "owned work is still draining" in caplog.text
    shutdown.assert_awaited_once()
    lifecycle_state.return_value.cancel.assert_called_once()
    assert main._plugin_registry is None
