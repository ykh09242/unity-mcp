"""Real SDK notification writes are cancelled/joined at foreground exit.

Only the owned memory transport's real server writer is wrapped with a
progress-only backpressure gate. Context, ServerSession, dispatcher, client
and all other messages remain real.
"""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK -- actual SDK cancellation owner loop.
import threading
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import FastMCPTransport
import fastmcp.client.transports.memory as memory_transport
from fastmcp.server.middleware import Middleware
from mcp import MCPError
from mcp.server.session import ServerSession
from mcp.shared.memory import create_client_server_memory_streams

from services.command_progress import (
    CommandProgressUpdate,
    ProgressSource,
    claim_command_progress,
    foreground_progress,
)
from services.execution_receipts import ExecutionReceiptStore
from tests.test_execution_receipts import admit


class _ProgressWriteGate:
    """Owned test transport backpressure below the real SDK notification path."""

    def __init__(self, inner):
        self.inner = inner
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()
        self.release = asyncio.Event()
        self.attempts = []
        self.tasks = []
        self.active = 0
        self.cancelled = 0
        self.forwarded = 0

    async def send(self, item):
        payload = item.message.model_dump(by_alias=True, mode="json")
        if payload.get("method") != "notifications/progress":
            await self.inner.send(item)
            return
        self.attempts.append(payload)
        self.tasks.append(asyncio.current_task())
        self.active += 1
        self.entered.set()
        try:
            await self.release.wait()
            await self.inner.send(item)
            self.forwarded += 1
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
            self.exited.set()

    async def aclose(self):
        await self.inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "timeout_code", "timeout_message"),
    [("legacy", 0, ""), ("2026-07-28", -32603, "Internal server error")],
    ids=["2025-11-25", "2026-07-28"],
)
@pytest.mark.parametrize("ending", ["cancel", "deadline"])
async def test_real_sdk_pending_notification_joins_before_foreground_exit(
    tmp_path, monkeypatch, mode, ending, timeout_code, timeout_message
):
    # Given: actual public FastMCP Context+session with a generated notification
    # held by its real transport writer, not a substituted report_progress.
    gates = []
    transport_closed = asyncio.Event()
    foreground_exited = asyncio.Event()
    late_offer = threading.Event()
    late_attempted = threading.Event()
    release_worker = threading.Event()
    notifications = []
    workers = []
    timeouts = []
    handler_calls = []

    @asynccontextmanager
    async def owned_streams():
        try:
            async with create_client_server_memory_streams() as (client, server):
                server_read, server_write = server
                gate = _ProgressWriteGate(server_write)
                gates.append(gate)
                yield client, (server_read, gate)
        finally:
            transport_closed.set()

    # Test-only public stream-factory seam; no private dispatcher/kernel changes.
    monkeypatch.setattr(memory_transport, "create_client_server_memory_streams", owned_streams)
    server = FastMCP("owned-sdk-progress-cancellation")

    class ProgressScope(Middleware):
        async def on_call_tool(self, context, call_next):
            deadline = asyncio.timeout_at(asyncio.get_running_loop().time() + 30)
            timeouts.append(deadline)
            try:
                async with deadline:
                    async with foreground_progress(context.fastmcp_context):
                        return await call_next(context)
            finally:
                foreground_exited.set()

    server.add_middleware(ProgressScope())

    @server.tool(name="owned_blocked_operation")
    async def operation(ctx: Context) -> dict:
        assert isinstance(ctx, Context)
        assert isinstance(ctx.session, ServerSession)
        handler_calls.append(1)
        assert invocation is not None
        invocation.enter_handler()

        sink = claim_command_progress(total=2)

        async def authorized():
            deadline = timeouts[-1].when()
            return not foreground_exited.is_set() and (
                deadline is None or asyncio.get_running_loop().time() < deadline
            )

        assert sink is not None
        assert sink.bind(
            ProgressSource(
                transport="owned-memory",
                command_id="owned-command",
                session_id="owned-session",
                generation="owned-generation",
                user_id=None,
                expires_at=timeouts[-1].when(),
                guard=authorized,
            )
        )

        pin = invocation.acquire_worker()

        def retained_work():
            try:
                if late_offer.wait(5):
                    sink.offer_from_thread(CommandProgressUpdate(2, 2, "batch_items"))
                    late_attempted.set()
                release_worker.wait(5)
            finally:
                pin.close()

        thread = threading.Thread(target=retained_work, name="owned-core06-receipt-worker")
        workers.append(thread)
        try:
            thread.start()
        except BaseException:
            workers.remove(thread)
            pin.close()
            raise
        assert sink.offer(CommandProgressUpdate(1, 2, "batch_items"))
        await asyncio.Event().wait()
        raise AssertionError("Owned held handler unexpectedly resumed")

    async def on_progress(progress, total, message):
        notifications.append((progress, total, message))

    call = None
    store = ExecutionReceiptStore(tmp_path / "receipts", private_root=True)
    invocation = None
    settled = False
    try:
        invocation = admit(store)
        async with Client(
            FastMCPTransport(server), mode=mode, timeout=5, progress_handler=on_progress
        ) as client:
            try:
                call = asyncio.create_task(client.call_tool_mcp("owned_blocked_operation", {}))
                assert len(gates) == 1
                gate = gates[0]
                await asyncio.wait_for(gate.entered.wait(), 2)
                assert gate.active == 1 and not gate.exited.is_set()
                assert not call.done()
                assert gate.attempts[0]["params"]["progress"] == 1
                assert gate.attempts[0]["params"]["total"] == 2
                assert gate.attempts[0]["params"]["progressToken"] is not None
                # When: cancellation/timeout occurs while the real SDK write is pending.
                if ending == "cancel":
                    call.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await call
                else:
                    # Shorten its original scope after send entry, deterministically.
                    # No elapsed-time wait or replacement deadline is introduced.
                    timeouts[-1].reschedule(asyncio.get_running_loop().time())
                    # Plain middleware TimeoutError is an era-specific public
                    # JSON-RPC failure, not FastMCP's ToolError result conversion.
                    with pytest.raises(MCPError) as timeout:
                        await asyncio.wait_for(call, 2)
                    assert timeout.value.code == timeout_code
                    assert timeout.value.message == timeout_message
                    assert timeout.value.data is None
                await asyncio.wait_for(foreground_exited.wait(), 2)
                # Then: send tasks are joined BEFORE client/session teardown while
                # actual receipt worker ownership remains alive.
                assert gate.exited.is_set() and gate.active == 0
                assert gate.cancelled == 1 and gate.forwarded == 0
                assert all(task.done() for task in gate.tasks)
                invocation.finish("unknown", cause="wrapper_cancelled")
                settled = True
                assert store.active_count == 1
                assert workers[0].is_alive()
                gate.release.set()
                late_offer.set()
                assert await asyncio.wait_for(asyncio.to_thread(late_attempted.wait, 1), 2)
                tools = await asyncio.wait_for(client.list_tools(), 2)
                assert any(tool.name == "owned_blocked_operation" for tool in tools)
                assert handler_calls == [1]
                assert len(gate.attempts) == 1 and gate.forwarded == 0
                assert notifications == []
            finally:
                # A failed join assertion must not leave test backpressure
                # blocking the real SDK/client transport teardown.
                for gate in gates:
                    gate.release.set()
        assert transport_closed.is_set()
        assert all(task.done() for task in gates[0].tasks)
    finally:
        release_worker.set()
        late_offer.set()
        for gate in gates:
            gate.release.set()
        if call is not None and not call.done():
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
        for thread in workers:
            await asyncio.wait_for(asyncio.to_thread(thread.join, 2), 3)
            assert not thread.is_alive()
        if invocation is not None and not settled:
            if handler_calls:
                invocation.finish("unknown", cause="wrapper_cancelled")
            else:
                invocation.finish("not_dispatched")
        store.close()
