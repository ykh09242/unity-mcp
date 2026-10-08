"""Slow legacy MCP readers cannot block Unity results or disconnect cleanup."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakSet

import anyio
import pytest
import pytest_asyncio
from mcp.server.connection import Connection
from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher

from core.config import config
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.tool_list_notifier import ToolListNotifier


@asynccontextmanager
async def sdk_connection() -> AsyncIterator[tuple[Connection, anyio.abc.ObjectReceiveStream]]:
    """Use the installed SDK's real zero-buffer notification output."""
    incoming_send, incoming_receive = anyio.create_memory_object_stream(0)
    outgoing_send, outgoing_receive = anyio.create_memory_object_stream(0)
    connection = Connection(
        JSONRPCDispatcher(incoming_receive, outgoing_send), protocol_version="2025-11-25"
    )
    async with incoming_send, incoming_receive, outgoing_send, outgoing_receive:
        try:
            yield connection, outgoing_receive
        finally:
            # Unblock the baseline implementation too, so failing tests clean up.
            outgoing_receive.close()
            await connection.exit_stack.aclose()


@pytest_asyncio.fixture
async def notification_hub(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PluginRegistry]:
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "_catalog_notifications", ToolListNotifier())
    monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet())
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    registry = PluginRegistry()
    PluginHub.configure(registry)
    yield registry
    await PluginHub.shutdown()


@asynccontextmanager
async def plugin_reader(registry: PluginRegistry, *, queue_catalog: bool):
    """Drive real dispatch/on_receive with an already admitted Unity connection."""
    incoming = asyncio.Queue()
    ready = asyncio.Event()

    class RegisteredHub(PluginHub):
        async def on_connect(self, websocket):
            await websocket.accept()
            await registry.register("unity", "Project", "hash", "test")
            websocket.state.plugin_registered = True
            websocket.state.plugin_session_id = "unity"
            websocket.state.plugin_generation = "generation"
            type(self)._connections["unity"] = websocket
            ready.set()

    async def send(message):
        if message["type"] != "websocket.send":
            return
        payload = json.loads(message["text"])
        if payload["type"] == "execute":
            if queue_catalog:
                incoming.put_nowait(
                    {
                        "type": "websocket.receive",
                        "text": json.dumps({"type": "register_tools", "tools": []}),
                    }
                )
            incoming.put_nowait(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {"type": "command_result", "id": payload["id"], "result": {"success": True}}
                    ),
                }
            )

    incoming.put_nowait({"type": "websocket.connect"})
    endpoint = RegisteredHub({"type": "websocket", "path": "/hub/plugin"}, incoming.get, send)
    task = asyncio.create_task(endpoint.dispatch())
    await ready.wait()
    try:
        yield incoming, task
    finally:
        incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        done, _ = await asyncio.wait({task}, timeout=2)
        if task not in done:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_queued_unity_result_survives_stalled_sdk_notification(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a real SDK channel with no reader and an admitted Unity socket.
    monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.15)
    async with plugin_reader(notification_hub, queue_catalog=True):
        async with sdk_connection() as (connection, _):
            monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet([connection]))
            # When Unity queues a catalog update immediately ahead of its result.
            result = await PluginHub.send_command("unity", "ping", {})
            # Then the result is read within the existing command deadline.
            assert result == {"success": True}


@pytest.mark.asyncio
async def test_disconnect_finishes_while_sdk_notification_is_stalled(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a registered plugin and a real SDK channel with no reader.
    async with plugin_reader(notification_hub, queue_catalog=False) as (incoming, reader):
        async with sdk_connection() as (connection, _):
            monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet([connection]))
            # When the plugin disconnects and changes the catalog.
            incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
            done, _ = await asyncio.wait({reader}, timeout=0.15)
            # Then routing cleanup and the shielded dispatch finally block complete.
            assert reader in done
            assert notification_hub._sessions == {}


@pytest.mark.asyncio
async def test_healthy_sdk_connection_receives_change_despite_stalled_peer(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given two real SDK clients, with the first broadcast target left unread.
    async with sdk_connection() as first, sdk_connection() as second:
        streams = {first[0]: first[1], second[0]: second[1]}
        connections = WeakSet(streams)
        _, healthy = list(connections)
        monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", connections)
        notification = asyncio.create_task(PluginHub._notify_mcp_tool_list_changed())
        try:
            # When a catalog refresh is broadcast while one peer is stalled.
            with anyio.fail_after(0.15):
                message = await streams[healthy].receive()
            # Then the independent healthy SDK output contains a valid notification.
            assert message.message.method == "notifications/tools/list_changed"
        finally:
            for stream in streams.values():
                stream.close()
            await notification


@pytest.mark.asyncio
async def test_repeated_catalog_changes_coalesce_and_deliver_final_refresh(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an SDK send already stalled on its zero-buffer output.
    async with sdk_connection() as (connection, output):
        monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet([connection]))
        await PluginHub._notify_mcp_tool_list_changed()
        await anyio.wait_all_tasks_blocked()
        notifier = PluginHub._catalog_notifications
        task = next(iter(notifier._tasks))
        # When many catalog changes arrive while that send is in flight.
        for _ in range(10_000):
            await PluginHub._notify_mcp_tool_list_changed()
        # Then there is one worker and one final invalidation, without queued copies.
        assert notifier._tasks == {task}
        assert len(notifier._deliveries) == 1
        assert output.statistics().tasks_waiting_send == 1
        with anyio.fail_after(2):
            first = await output.receive()
            final = await output.receive()
        assert first.message.method == final.message.method == "notifications/tools/list_changed"
        await anyio.wait_all_tasks_blocked()
        assert notifier._tasks == set()
        assert output.statistics().tasks_waiting_send == 0


@pytest.mark.asyncio
async def test_connection_teardown_drains_send_and_rejects_raced_refresh(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a closing SDK connection still visible until its final teardown callback.
    async with sdk_connection() as (connection, output):
        closing = anyio.Event()
        finish = anyio.Event()

        async def teardown_barrier():
            closing.set()
            await finish.wait()

        connection.exit_stack.push_async_callback(teardown_barrier)
        monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet([connection]))
        await PluginHub._notify_mcp_tool_list_changed()
        await anyio.wait_all_tasks_blocked()
        close = asyncio.create_task(connection.exit_stack.aclose())
        try:
            # When a catalog change races with SDK teardown after its worker drained.
            with anyio.fail_after(2):
                await closing.wait()
            await PluginHub._notify_mcp_tool_list_changed()
            # Then the closing connection cannot acquire another delivery worker.
            assert PluginHub._catalog_notifications._tasks == set()
            assert output.statistics().tasks_waiting_send == 0
        finally:
            finish.set()
            await close


@pytest.mark.asyncio
async def test_shutdown_drains_stalled_delivery_and_rejects_late_refresh(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a stalled real SDK send with another refresh already coalesced behind it.
    async with sdk_connection() as (connection, output):
        monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet([connection]))
        await PluginHub._notify_mcp_tool_list_changed()
        await anyio.wait_all_tasks_blocked()
        await PluginHub._notify_mcp_tool_list_changed()
        notifier = PluginHub._catalog_notifications
        workers = set(notifier._tasks)
        # When shutdown races with more catalog refreshes.
        shutdown = asyncio.create_task(PluginHub.shutdown())
        refresh = asyncio.create_task(PluginHub._notify_mcp_tool_list_changed())
        with anyio.fail_after(2):
            await asyncio.gather(shutdown, refresh)
        await PluginHub._notify_mcp_tool_list_changed()
        # Then shutdown has drained every worker and no late work survives it.
        assert all(worker.done() for worker in workers)
        assert notifier._tasks == set()
        assert len(notifier._deliveries) == 0
        assert output.statistics().tasks_waiting_send == 0


@pytest.mark.asyncio
async def test_shutdown_before_worker_start_does_not_leave_pending_task(
    notification_hub: PluginRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a refresh queued without yielding to the delivery coroutine.
    async with sdk_connection() as (connection, output):
        monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", WeakSet([connection]))
        await PluginHub._notify_mcp_tool_list_changed()
        workers = set(PluginHub._catalog_notifications._tasks)
        # When the hub shuts down before the new worker starts running.
        await PluginHub.shutdown()
        # Then cancellation is drained even when the worker never entered its finally.
        assert all(worker.cancelled() for worker in workers)
        assert PluginHub._catalog_notifications._tasks == set()
        assert output.statistics().tasks_waiting_send == 0
