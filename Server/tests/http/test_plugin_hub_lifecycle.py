"""Regression coverage for plugin sender identity and command lifetimes."""

import asyncio  # noqa: ANYIO_OK -- the hub owns asyncio futures and tasks.
from collections.abc import Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.fixture
def isolated_hub(monkeypatch: pytest.MonkeyPatch) -> PluginRegistry:
    monkeypatch.setattr(config, "http_remote_hosted", False)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    return PluginRegistry()


@pytest.fixture
def wire_client(isolated_hub: PluginRegistry) -> Iterator[TestClient]:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        PluginHub.configure(isolated_hub)
        yield

    app = Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)], lifespan=lifespan)
    with TestClient(app) as client:
        yield client


def test_unregistered_pongs_do_not_allocate_heartbeat_entries(wire_client: TestClient) -> None:
    # Given an accepted socket that has not registered a plugin session.
    with wire_client.websocket_connect("/hub/plugin") as ws:
        assert ws.receive_json()["type"] == "welcome"
        # When arbitrary session IDs are sent before registration.
        for index in range(3):
            ws.send_json({"type": "pong", "session_id": f"unknown-{index}"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        # Registration is now mandatory as the first message; unregistered
        # heartbeat senders cannot allocate state or retain an accepted socket.
        assert closed.value.code == 4400
        assert PluginHub._last_pong == {}


def test_foreign_result_does_not_complete_owner_command(wire_client: TestClient) -> None:
    # Given two registered sockets and a command owned by the first.
    with wire_client.websocket_connect("/hub/plugin") as owner:
        owner.receive_json()
        owner.send_json({"type": "register", "project_hash": "owner"})
        owner_id = owner.receive_json()["session_id"]

        async def add_pending() -> asyncio.Future:
            future = asyncio.get_running_loop().create_future()
            PluginHub._pending["known-command"] = {"future": future, "session_id": owner_id}
            return future

        future = wire_client.portal.call(add_pending)
        with wire_client.websocket_connect("/hub/plugin") as foreign:
            foreign.receive_json()
            foreign.send_json({"type": "register", "project_hash": "foreign"})
            foreign.receive_json()
            # When a foreign socket submits the owner's command UUID.
            foreign.send_json({"type": "command_result", "id": "known-command", "result": {"success": True}})
            foreign.send_json({"type": "register", "project_hash": "duplicate"})
            with pytest.raises(WebSocketDisconnect):
                foreign.receive_json()  # Processing barrier for command_result.
        # Then the owner's command remains pending.
        assert not future.done()
        wire_client.portal.call(future.cancel)


def test_owner_result_completes_its_pending_command(wire_client: TestClient) -> None:
    # Given a command pending on a registered plugin socket.
    with wire_client.websocket_connect("/hub/plugin") as ws:
        ws.receive_json()
        ws.send_json({"type": "register", "project_hash": "owner"})
        session_id = ws.receive_json()["session_id"]

        async def add_pending() -> asyncio.Future:
            future = asyncio.get_running_loop().create_future()
            PluginHub._pending["owner-command"] = {"future": future, "session_id": session_id}
            return future

        future = wire_client.portal.call(add_pending)
        # When the owning socket submits its result through the actual endpoint.
        ws.send_json({"type": "command_result", "id": "owner-command", "result": {"success": True}})
        ws.send_json({"type": "register", "project_hash": "duplicate"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
        # Then the result is accepted before the registration barrier closes.
        assert future.result() == {"success": True}


@pytest.mark.asyncio
async def test_cancelled_send_releases_pending_future(isolated_hub: PluginRegistry) -> None:
    # Given a command blocked while sending and its allocated reply future.
    PluginHub.configure(isolated_hub)
    entered = asyncio.Event()
    captured = []

    async def blocked_send(payload) -> None:
        captured.append(PluginHub._pending[payload["id"]]["future"])
        entered.set()
        await asyncio.Event().wait()

    ws = AsyncMock()
    ws.send_json.side_effect = blocked_send
    PluginHub._connections["session"] = ws
    command = asyncio.create_task(PluginHub.send_command("session", "manage_scene", {}))
    await entered.wait()
    # When the calling task cancels the command.
    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command
    # Then cancellation propagates and the pending future/map are released.
    assert captured[0].cancelled()
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_command_timeout_includes_blocked_send(isolated_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a socket whose send cannot finish and a short command timeout.
    PluginHub.configure(isolated_hub)
    entered = asyncio.Event()

    async def blocked_send(payload) -> None:
        entered.set()
        await asyncio.Event().wait()

    ws = AsyncMock()
    ws.send_json.side_effect = blocked_send
    PluginHub._connections["session"] = ws
    monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.02)
    command = asyncio.create_task(PluginHub.send_command("session", "ping", {}))
    await entered.wait()
    try:
        # When the deadline passes while the write remains blocked.
        done, _ = await asyncio.wait({command}, timeout=0.2)
        # Then the command returns its existing retry response and releases state.
        assert command in done, "Command deadline must cover socket writes"
        result = command.result()
        assert result["success"] is False
        assert result["hint"] == "retry"
        assert PluginHub._pending == {}
    finally:
        command.cancel()
        await asyncio.gather(command, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_send_does_not_leave_unconsumed_future(isolated_hub: PluginRegistry) -> None:
    # Given a socket write that fails before a result is awaited.
    PluginHub.configure(isolated_hub)
    ws = AsyncMock()
    captured = []

    async def failed_send(payload) -> None:
        captured.append(PluginHub._pending[payload["id"]]["future"])
        raise OSError("socket write failed")

    ws.send_json.side_effect = failed_send
    PluginHub._connections["session"] = ws
    observed = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: observed.append(context))
    try:
        # When the transport propagates the write failure.
        with pytest.raises(OSError, match="socket write failed"):
            await PluginHub.send_command("session", "manage_scene", {})
        await asyncio.sleep(0)
        # Then there is no orphaned Future exception reported to the loop.
        assert PluginHub._pending == {}
        future = captured[0]
        if future.done() and not future.cancelled():
            future.exception()  # Consume the baseline exception when the assertion fails.
        assert future.cancelled()
        assert observed == []
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_disconnect_interrupts_blocked_command_write(isolated_hub: PluginRegistry) -> None:
    PluginHub.configure(isolated_hub)
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked_send(payload) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    ws = AsyncMock()
    ws.send_json.side_effect = blocked_send
    PluginHub._connections["session"] = ws
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    command = asyncio.create_task(PluginHub.send_command("session", "manage_scene", {}))
    try:
        await entered.wait()
        await hub.on_disconnect(ws, 1001)
        done, _ = await asyncio.wait({command}, timeout=0.2)
        assert command in done, "Disconnect must interrupt a blocked write immediately"
        assert command.result()["hint"] == "retry"
        assert stopped.is_set()
        assert PluginHub._pending == {}
    finally:
        command.cancel()
        await asyncio.gather(command, return_exceptions=True)


@pytest.mark.asyncio
async def test_disconnect_before_pending_registration_does_not_send(
    isolated_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    PluginHub.configure(isolated_hub)
    ws = AsyncMock()
    PluginHub._connections["session"] = ws
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    lookup = PluginHub._get_connection

    async def disconnect_after_lookup(session_id):
        connection = await lookup(session_id)
        await hub.on_disconnect(connection, 1001)
        return connection

    monkeypatch.setattr(PluginHub, "_get_connection", disconnect_after_lookup)
    command = asyncio.create_task(PluginHub.send_command("session", "manage_scene", {}))
    try:
        done, _ = await asyncio.wait({command}, timeout=0.2)
        assert command in done, "A disconnected socket must not gain new pending commands"
        with pytest.raises(RuntimeError, match="not connected"):
            command.result()
        ws.send_json.assert_not_awaited()
        assert PluginHub._pending == {}
    finally:
        command.cancel()
        await asyncio.gather(command, return_exceptions=True)
