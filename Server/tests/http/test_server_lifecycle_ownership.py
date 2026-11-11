"""Server and registration lifetimes release their owned plugin resources."""

from tests.timing import await_signal, watchdog_timeout

import asyncio  # noqa: ANYIO_OK -- inspect hub-owned task lifetimes.
import threading
from types import SimpleNamespace
from tests.transport_socket import JsonSocket
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from starlette.websockets import WebSocketState

import main
from core.config import config
from tests.remote_credentials import remote_credentials
from transport.models import RegisterMessage, RegisterToolsMessage, TOOL_SETTINGS_CAPABILITY
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
    for name in (
        "_connections",
        "_pending",
        "_ping_tasks",
        "_last_pong",
        "_admitted",
        "_eviction_tasks",
    ):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    yield timer
    tasks = list(PluginHub._ping_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def socket_stub():
    return JsonSocket(
        state=SimpleNamespace(),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
        send_json=AsyncMock(),
        close=AsyncMock(),
    )


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
            await await_signal(sent, "sent", producers=(command,))
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
    pool.aclose = AsyncMock()
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
    pool.aclose.assert_awaited_once()
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
    pool = SimpleNamespace(aclose=AsyncMock())
    server._reference_graph_service = graph
    server._unity_session_state_store = state
    server._execution_receipts = receipts
    server._scene_load_inputs = scene
    monkeypatch.setattr(ApiKeyService, "is_initialized", lambda: True)
    monkeypatch.setattr(ApiKeyService, "get_instance", lambda: validator)
    monkeypatch.setattr(main, "_unity_connection_pool", pool)
    shutdown = AsyncMock()
    monkeypatch.setattr(PluginHub, "begin_shutdown", lambda: shutdown)
    with pytest.raises(RuntimeError, match=failure + " cleanup"):
        async with main.server_lifespan(server):
            pass
    graph.aclose.assert_awaited_once()
    state.clear.assert_called_once()
    receipts.close.assert_called_once()
    scene.close.assert_called_once()
    shutdown.assert_awaited_once()
    pool.aclose.assert_awaited_once()
    validator.aclose.assert_awaited_once()
    lifecycle_state.return_value.cancel.assert_called_once()
    assert main._plugin_registry is None and main._unity_connection_pool is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retire_early", [False, True])
async def test_server_cleanup_reserves_hub_before_unrelated_owner_suspends(
    lifecycle_state, monkeypatch, retire_early
):
    entered, release = asyncio.Event(), asyncio.Event()
    server = FastMCP("retiring-server")

    async def close_graph():
        entered.set()
        await release.wait()

    server._reference_graph_service = SimpleNamespace(aclose=close_graph)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old_socket, new_socket = socket_stub(), socket_stub()

    async def run():
        async with main.server_lifespan(server):
            await hub._handle_register(old_socket, RegisterMessage(project_hash="old"))

    shutdown = asyncio.create_task(run())
    try:
        await await_signal(entered, "prior graph cleanup", producers=(shutdown,))
        old_registry = PluginHub._registry
        new_registry = PluginRegistry()
        with pytest.raises(RuntimeError, match="still shutting down"):
            PluginHub.configure(new_registry)
        assert PluginHub._registry is old_registry
        assert PluginHub.is_configured()
        if retire_early:
            await PluginHub.shutdown()
        else:
            release.set()
            await asyncio.wait_for(shutdown, timeout=watchdog_timeout())
        PluginHub.configure(new_registry)
        monkeypatch.setattr(main, "_plugin_registry", new_registry)
        await hub._handle_register(new_socket, RegisterMessage(project_hash="new"))
        session_id = next(sid for sid, ws in PluginHub._connections.items() if ws is new_socket)
        ping_task = PluginHub._ping_tasks[session_id]
        release.set()
        await asyncio.wait_for(shutdown, timeout=watchdog_timeout())
        assert PluginHub._registry is new_registry
        assert main._plugin_registry is new_registry
        assert PluginHub._connections[session_id] is new_socket
        assert await new_registry.get_session(session_id) is not None
        assert not ping_task.done()
        new_socket.close.assert_not_awaited()
        old_socket.close.assert_awaited_once()
        assert await old_registry.list_sessions() == {}
    finally:
        release.set()
        await asyncio.gather(shutdown, return_exceptions=True)
        await PluginHub.shutdown()


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
    monkeypatch.setattr(PluginHub, "begin_shutdown", lambda: shutdown)

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
    monkeypatch.setattr(PluginHub, "begin_shutdown", lambda: shutdown)
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
    monkeypatch.setattr(PluginHub, "begin_shutdown", lambda: shutdown)
    async with main.server_lifespan(server):
        pass
    assert "owned work is still draining" in caplog.text
    shutdown.assert_awaited_once()
    lifecycle_state.return_value.cancel.assert_called_once()
    assert main._plugin_registry is None


@pytest.mark.asyncio
async def test_shutdown_owns_pool_created_after_skipped_startup(lifecycle_state, monkeypatch):
    from transport.legacy import unity_connection as connections

    monkeypatch.setattr(connections, "_unity_connection_pool", None)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def resync(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert PluginHub.is_configured(), "Resync outlived the catalog hub"
            stopped.set()

    monkeypatch.setattr(connections, "_resync_tools_after_reconnect", resync)
    async with main.server_lifespan(FastMCP("late-pool")):
        pool = connections.get_unity_connection_pool()
        conn = connections.UnityConnection(port=6400, instance_id="Main@deadbeef")
        pool._connections[conn.instance_id] = conn
        pool.schedule_tool_resync(conn)
        await await_signal(started, "resync started")
    assert stopped.is_set(), "Server shutdown left late-created resync running"
    assert connections._unity_connection_pool is None
    async with main.server_lifespan(FastMCP("next-generation")):
        assert connections.get_unity_connection_pool() is not pool


@pytest.mark.asyncio
async def test_http_startup_uses_websocket_hub_without_legacy_discovery(
    lifecycle_state, monkeypatch
):
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "0")
    monkeypatch.setattr(config, "transport_mode", "http")
    pool_factory = Mock(side_effect=AssertionError("HTTP must not discover legacy Editors"))
    monkeypatch.setattr(main, "get_unity_connection_pool", pool_factory)
    async with main.server_lifespan(FastMCP("http-startup")) as state:
        assert state["pool"] is None
        assert PluginHub.is_configured()
    pool_factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_phase", ["discover_all_instances", "get_connection"])
@pytest.mark.parametrize("ending", ["cancel", "deadline", "level_cancel"])
async def test_startup_worker_keeps_loop_responsive_and_drains_before_cleanup(
    lifecycle_state, monkeypatch, blocked_phase, ending
):
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "0")
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(main, "STARTUP_CONNECT_TIMEOUT", 0.02, raising=False)
    entered, expired = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    pool = Mock()
    pool.aclose = AsyncMock()
    pool.discover_all_instances.return_value = [SimpleNamespace(id="Fixture@hash")]

    def blocked(*args):
        # Fail directly on the original synchronous startup path, without hanging it.
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())
        return (
            [SimpleNamespace(id="Fixture@hash")]
            if blocked_phase == "discover_all_instances"
            else None
        )

    getattr(pool, blocked_phase).side_effect = blocked
    monkeypatch.setattr(main, "get_unity_connection_pool", Mock(return_value=pool))
    yielded = asyncio.Event()
    scopes = []

    async def start():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            async with main.server_lifespan(FastMCP("worker-startup")):
                yielded.set()

    startup = asyncio.create_task(start())
    loop.call_later(0.04, expired.set)
    try:
        await await_signal(entered, "startup worker", producers=(startup,))
        if ending == "cancel":
            startup.cancel()
        elif ending == "level_cancel":
            scopes[0].cancel()
        await await_signal(expired, "event-loop ticker")
        assert not startup.done(), "Actual blocking work must remain owned until it exits"
        assert not yielded.is_set()
        pool.aclose.assert_not_awaited()
        if ending == "cancel":
            startup.cancel()
        release.set()
        result = await asyncio.gather(startup, return_exceptions=True)
        if ending == "cancel":
            assert isinstance(result[0], asyncio.CancelledError)
            assert not yielded.is_set()
        elif ending == "deadline":
            assert result == [None]
            assert yielded.is_set()
        else:
            assert result == [None]
            assert not yielded.is_set()
        pool.aclose.assert_awaited_once()
        if blocked_phase == "discover_all_instances":
            pool.get_connection.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(startup, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["notification_close", "hub_lock"])
async def test_old_hub_shutdown_preserves_every_new_generation_owner(
    lifecycle_state, monkeypatch, barrier
):
    old_registry = PluginRegistry()
    PluginHub.configure(old_registry)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old_socket = socket_stub()
    await hub._handle_register(old_socket, RegisterMessage(project_hash="old"))
    old_ping = next(iter(PluginHub._ping_tasks.values()))
    old_pending = asyncio.get_running_loop().create_future()
    PluginHub._pending["old-command"] = {"future": old_pending, "session_id": "old"}
    old_notifier = PluginHub._catalog_notifications
    old_lock = PluginHub._lock
    entered, release, new_eviction_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    old_eviction_release, notification_started = asyncio.Event(), asyncio.Event()
    notification_release = asyncio.Event()
    old_eviction = asyncio.create_task(old_eviction_release.wait())
    PluginHub._eviction_tasks[(id(old_registry), "old")] = old_eviction
    original_close = old_notifier.close

    async def close_notifications():
        entered.set()
        if barrier == "notification_close":
            await release.wait()
        await original_close()

    async def send_notification():
        notification_started.set()
        await notification_release.wait()

    monkeypatch.setattr(old_notifier, "close", close_notifications)
    if barrier == "hub_lock":
        await old_lock.acquire()
    shutdown = asyncio.create_task(PluginHub.shutdown())
    new_eviction = None
    new_pending = asyncio.get_running_loop().create_future()
    try:
        await await_signal(entered, "old generation cleanup", producers=(shutdown,))
        new_registry = PluginRegistry()
        server = FastMCP("new-hub")
        PluginHub.configure(new_registry, mcp=server)
        connection = Mock(send_tool_list_changed=AsyncMock(side_effect=send_notification))
        PluginHub._catalog_notifications.publish([connection])
        await await_signal(notification_started, "new generation notification")
        notification_task = next(iter(PluginHub._catalog_notifications._tasks))
        new_socket = socket_stub()
        await hub._handle_register(new_socket, RegisterMessage(project_hash="new"))
        session_id = next(sid for sid, ws in PluginHub._connections.items() if ws is new_socket)
        PluginHub._pending["new-command"] = {"future": new_pending, "session_id": session_id}
        new_eviction = asyncio.create_task(new_eviction_release.wait())
        PluginHub._eviction_tasks[(id(new_registry), session_id)] = new_eviction
        owners = {
            name: getattr(PluginHub, name)
            for name in (
                "_registry",
                "_mcp",
                "_loop",
                "_lock",
                "_connections",
                "_pending",
                "_admitted",
                "_ping_tasks",
                "_last_pong",
                "_eviction_tasks",
                "_catalog_notifications",
                "_editor_states",
                "_large_results",
                "_raw_results",
                "_readiness_reads",
                "_ordinary_state_reads",
                "_ordinary_state_flights",
            )
        }
        release.set()
        if old_lock.locked():
            old_lock.release()
        await asyncio.sleep(0)
        assert not shutdown.done(), "Old eviction cleanup must retain its original owner"
        old_eviction_release.set()
        done, _ = await asyncio.wait({shutdown}, timeout=watchdog_timeout(2.0))
        assert shutdown in done, "Old cleanup waited on a newly configured eviction owner"
        shutdown.result()
        assert PluginHub.is_configured()
        for name, owner in owners.items():
            assert getattr(PluginHub, name) is owner, name
        assert await new_registry.get_session(session_id) is not None
        assert PluginHub._connections[session_id] is new_socket
        assert not new_pending.done()
        assert not PluginHub._ping_tasks[session_id].done()
        assert not new_eviction.done()
        assert not notification_task.done()
        new_socket.close.assert_not_awaited()
        old_socket.close.assert_awaited_once()
        assert old_ping.done()
        assert isinstance(old_pending.exception(), Exception)
        assert await old_registry.list_sessions() == {}
    finally:
        release.set()
        if old_lock.locked():
            old_lock.release()
        new_eviction_release.set()
        old_eviction_release.set()
        notification_release.set()
        if new_eviction is not None:
            await new_eviction
        new_pending.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)
        for future in (old_pending, new_pending):
            if future.done() and not future.cancelled():
                future.exception()
        await PluginHub.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["register", "register_tools_for_session", "unregister"])
async def test_registration_waiting_on_old_registry_cannot_publish_into_replacement(
    lifecycle_state, monkeypatch, operation
):
    old_registry = PluginRegistry()
    PluginHub.configure(old_registry)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old_socket = socket_stub()
    PluginHub._admitted[id(old_socket)] = (old_socket, None)
    if operation != "register":
        await hub._handle_register(old_socket, RegisterMessage(project_hash="old"))
    entered = asyncio.Event()
    mutate = getattr(old_registry, operation)

    async def blocked_register(*args, **kwargs):
        entered.set()
        return await mutate(*args, **kwargs)

    monkeypatch.setattr(old_registry, operation, blocked_register)
    await old_registry._lock.acquire()
    if operation == "register":
        mutation = hub._handle_register(old_socket, RegisterMessage(project_hash="old"))
    elif operation == "register_tools_for_session":
        mutation = hub._handle_register_tools(old_socket, RegisterToolsMessage(tools=[]))
    else:
        mutation = PluginHub._evict_connection_owned(old_socket.state.plugin_session_id, None)
    registration = asyncio.create_task(mutation)
    shutdown = None
    try:
        await await_signal(entered, "registry lock", producers=(registration,))
        shutdown = asyncio.create_task(PluginHub.shutdown())
        await asyncio.sleep(0)
        assert not PluginHub.is_configured()
        new_registry = PluginRegistry()
        PluginHub.configure(new_registry)
        new_socket = socket_stub()
        await hub._handle_register(new_socket, RegisterMessage(project_hash="new"))
        expected_connections = dict(PluginHub._connections)
        expected_pings = dict(PluginHub._ping_tasks)
        publish = Mock(wraps=PluginHub._publish_session_catalog)
        monkeypatch.setattr(PluginHub, "_publish_session_catalog", publish)
        old_registry._lock.release()
        await asyncio.gather(registration, return_exceptions=True)
        await asyncio.wait_for(shutdown, timeout=watchdog_timeout())
        assert PluginHub._connections == expected_connections
        assert PluginHub._ping_tasks == expected_pings
        assert len(await new_registry.list_sessions()) == 1
        publish.assert_not_called()
        new_socket.close.assert_not_awaited()
    finally:
        if old_registry._lock.locked():
            old_registry._lock.release()
        await asyncio.gather(registration, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        await PluginHub.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["registration_ack", "tool_settings_receipt"])
@pytest.mark.parametrize("ack_failure", [False, True])
async def test_catalog_notification_after_ack_cannot_use_replacement_generation(
    lifecycle_state, monkeypatch, operation, ack_failure
):
    old_registry = PluginRegistry()
    PluginHub.configure(old_registry)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    old_socket = socket_stub()
    if operation == "tool_settings_receipt":
        await hub._handle_register(old_socket, RegisterMessage(project_hash="old"))
        old_socket.state.plugin_features = frozenset({TOOL_SETTINGS_CAPABILITY})

    # Model a committed catalog delta that requires its owning clients to refresh.
    monkeypatch.setattr(
        PluginHub,
        "_publish_session_catalog",
        Mock(return_value={"changed": True, "global_component_changed": False}),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_ack(payload):
        entered.set()
        await release.wait()
        if ack_failure:
            raise OSError("fixture ACK failed")

    old_socket.send_json.side_effect = blocked_ack
    if operation == "registration_ack":
        mutation = hub._handle_register(old_socket, RegisterMessage(project_hash="old"))
    else:
        mutation = hub._handle_register_tools(
            old_socket,
            RegisterToolsMessage(tools=[], settings_epoch="a" * 32, desired_revision=1),
        )
    pending = asyncio.create_task(mutation)
    try:
        await await_signal(entered, "committed registration ACK", producers=(pending,))
        assert len(await old_registry.list_sessions()) == 1
        await PluginHub.shutdown()
        new_registry = PluginRegistry()
        new_server = FastMCP("replacement-notifications")
        new_server._catalog_subscriptions = Mock()
        PluginHub.configure(new_registry, mcp=new_server)
        new_socket = socket_stub()
        await hub._handle_register(new_socket, RegisterMessage(project_hash="new"))
        new_server._catalog_subscriptions.reset_mock()
        notifications = Mock(wraps=PluginHub._catalog_notifications.publish)
        monkeypatch.setattr(PluginHub._catalog_notifications, "publish", notifications)
        expected_connections = dict(PluginHub._connections)
        release.set()
        if operation == "registration_ack" and ack_failure:
            with pytest.raises(OSError, match="fixture ACK failed"):
                await pending
        else:
            await pending
        notifications.assert_not_called()
        new_server._catalog_subscriptions.publish.assert_not_called()
        assert PluginHub._connections == expected_connections
        assert len(await new_registry.list_sessions()) == 1
        new_socket.close.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await PluginHub.shutdown()
