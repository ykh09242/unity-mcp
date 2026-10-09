"""Catalog delivery owns lifecycle context, independent of finished SDK requests."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import gc
from weakref import ref

import anyio
from fastmcp import Client, Context, FastMCP
from fastmcp.client.messages import MessageHandler
from mcp.server.connection import Connection
from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher
import pytest

from transport.tool_list_notifier import ToolListNotifier


@asynccontextmanager
async def sdk_connection():
    incoming_send, incoming_receive = anyio.create_memory_object_stream(0)
    outgoing_send, outgoing_receive = anyio.create_memory_object_stream(0)
    connection = Connection(
        JSONRPCDispatcher(incoming_receive, outgoing_send), protocol_version="2025-11-25"
    )
    async with incoming_send, incoming_receive, outgoing_send, outgoing_receive:
        try:
            yield connection
        finally:
            outgoing_receive.close()
            await connection.exit_stack.aclose()


class ToolChangeHandler(MessageHandler):
    def __init__(self):
        self.received = asyncio.Event()

    async def on_tool_list_changed(self, message):
        self.received.set()


@pytest.mark.asyncio
async def test_finished_request_released_while_real_notification_is_stalled(monkeypatch):
    notifier = ToolListNotifier()
    entered, release = asyncio.Event(), asyncio.Event()
    contexts, requests = [], []
    original = Connection.send_tool_list_changed

    async def stalled(connection):
        entered.set()
        await release.wait()
        await original(connection)

    monkeypatch.setattr(Connection, "send_tool_list_changed", stalled)
    server = FastMCP("catalog-context-lifetime")
    handler = ToolChangeHandler()

    @server.tool
    async def invoke(payload: str, ctx: Context) -> int:
        contexts.append(ref(ctx))
        requests.append(ref(ctx.request_context))
        notifier.publish([ctx.request_context.session._connection])
        return len(payload)

    try:
        async with Client(server, mode="legacy", message_handler=handler) as client:
            result = await client.call_tool(
                "invoke",
                {"payload": "x" * (1024 * 1024)},
                meta={"probe_payload": "y" * (1024 * 1024)},
            )
            assert not result.is_error
            await asyncio.wait_for(entered.wait(), 2)
            # Advance the SDK beyond the last request before observing production ownership.
            await client.list_tools()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            gc.collect()
            assert contexts[0]() is None
            assert requests[0]() is None
            assert len(notifier._tasks) == 1
            release.set()
            await asyncio.wait_for(handler.received.wait(), 2)
    finally:
        release.set()
        await notifier.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", [False, True])
async def test_delivery_preserves_lifecycle_context_and_isolates_each_worker(monkeypatch, reset):
    marker = ContextVar("catalog_lifecycle_marker", default="outside")
    token = marker.set("lifecycle")
    try:
        notifier = ToolListNotifier()
        if reset:
            notifier.reset()
    finally:
        marker.reset(token)
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def stalled(connection):
        observed.append(marker.get())
        marker.set("worker mutation")
        if len(observed) == 2:
            entered.set()
        await release.wait()

    monkeypatch.setattr(Connection, "send_tool_list_changed", stalled)
    try:
        async with sdk_connection() as first, sdk_connection() as second:
            token = marker.set("request")
            try:
                notifier.publish([first, second])
            finally:
                marker.reset(token)
            await asyncio.wait_for(entered.wait(), 2)
            assert observed == ["lifecycle", "lifecycle"]
            assert marker.get() == "outside"
    finally:
        release.set()
        await notifier.close()


@pytest.mark.asyncio
async def test_reset_drains_old_context_and_close_releases_lifecycle_snapshot(monkeypatch):
    class Lifetime:
        pass

    marker = ContextVar("catalog_snapshot_owner", default=None)
    old, new = Lifetime(), Lifetime()
    old_ref, new_ref = ref(old), ref(new)
    token = marker.set(old)
    try:
        notifier = ToolListNotifier()
    finally:
        marker.reset(token)
    entered = asyncio.Event()
    seen = []

    async def stalled(connection):
        seen.append("old" if marker.get() is old_ref() else "new")
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Connection, "send_tool_list_changed", stalled)
    try:
        async with sdk_connection() as first, sdk_connection() as second:
            notifier.publish([first])
            await asyncio.wait_for(entered.wait(), 2)
            old_tasks = tuple(notifier._tasks)
            token = marker.set(new)
            try:
                notifier.reset()
            finally:
                marker.reset(token)
            await asyncio.gather(*old_tasks, return_exceptions=True)
            assert all(task.cancelled() for task in old_tasks)
            del old_tasks, old
            await asyncio.sleep(0)
            gc.collect()
            assert old_ref() is None
            entered.clear()
            notifier.publish([second])
            await asyncio.wait_for(entered.wait(), 2)
            assert seen == ["old", "new"]
            del new
            await notifier.close()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            gc.collect()
            assert new_ref() is None
            assert not notifier._tasks
    finally:
        await notifier.close()
