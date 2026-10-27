"""Owned stress-client streams close within an independent cleanup budget."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import stress_mcp as stress


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stalled", "reset", "abort-stalled"])
async def test_writer_cleanup_aborts_stalled_peer_without_masking_operation(monkeypatch, mode):
    # Given: a close Future that stalls, fails, or ignores transport abort.
    aborted = asyncio.Event()
    closed = asyncio.get_running_loop().create_future()
    if mode == "reset":
        closed.set_exception(ConnectionResetError("peer reset"))

    def abort():
        aborted.set()
        if mode == "stalled" and not closed.done():
            closed.set_result(None)

    class Writer:
        transport = SimpleNamespace(abort=abort)
        _protocol = SimpleNamespace(_get_close_waiter=lambda _: closed)

        def close(self):
            pass

    monkeypatch.setattr(stress, "CLEANUP_TIMEOUT", 0.05)
    stats = {}
    # When: cleanup runs during propagation of an operation failure.
    with pytest.raises(ValueError, match="original operation"):
        try:
            raise ValueError("original operation")
        finally:
            await asyncio.wait_for(stress.close_writer(Writer(), stats), 1)
    # Then: original failure survives; no owned coroutine waits beyond the budget.
    assert aborted.is_set() and closed.done()
    expected = {
        "stalled": {"cleanup_timeouts": 1},
        "reset": {"cleanup_errors": 1},
        "abort-stalled": {"cleanup_timeouts": 1, "cleanup_errors": 1},
    }
    assert stats == expected[mode]
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


@pytest.mark.asyncio
async def test_writer_ordinary_close_completes_without_cleanup_failure():
    # Given: an ordinary close Future already completed by the protocol.
    closed = asyncio.get_running_loop().create_future()
    closed.set_result(None)

    class Writer:
        _protocol = SimpleNamespace(_get_close_waiter=lambda _: closed)

        def close(self):
            self.closed = True

    writer = Writer()
    stats = {}
    # When / Then: cleanup succeeds without reporting a timeout or error.
    await stress.close_writer(writer, stats)
    assert writer.closed and stats == {}


@pytest.mark.asyncio
async def test_native_stream_peer_that_stops_reading_cannot_hold_cleanup(monkeypatch):
    # Given: an owned loopback peer that stops consuming after connect.
    accepted = asyncio.Event()
    peers = []

    def accept(_reader, writer):
        writer.transport.pause_reading()
        peers.append(writer)
        accepted.set()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    monkeypatch.setattr(stress, "CLEANUP_TIMEOUT", 0.1)
    stats = {}
    writer = None
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        await accepted.wait()
        # Two writes also exercise Proactor's queued-buffer backpressure state.
        writer.write(b"owned-data" * 1000000)
        writer.write(b"owned-data" * 1000000)
        # When: the actual native StreamWriter closes against that owned peer.
        # Windows loopback may accept all bytes before backpressure is visible;
        # the stalled-Future tests deterministically cover the timeout branch.
        await asyncio.wait_for(stress.close_writer(writer, stats), 1)
        # Then: either normal close or bounded abort confirms closing.
        assert stats.get("cleanup_errors", 0) == 0
        assert writer.transport.is_closing()
    finally:
        if writer is not None:
            writer.transport.abort()
        for peer in peers:
            peer.transport.abort()
            await stress.close_writer(peer, {})
        server.close()
        await asyncio.wait_for(server.wait_closed(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["missing", "invalid", "coroutine"])
async def test_unsupported_stream_close_protocol_aborts_and_reports_failure(shape):
    # Given: the writer cannot supply the verified native close-Future shape.
    aborted = asyncio.Event()

    async def invalid_coroutine():
        await asyncio.Future()

    writer = SimpleNamespace(close=lambda: None, transport=SimpleNamespace(abort=aborted.set))
    if shape == "invalid":
        writer._protocol = SimpleNamespace(_get_close_waiter=lambda _: None)
    elif shape == "coroutine":
        writer._protocol = SimpleNamespace(_get_close_waiter=lambda _: invalid_coroutine())
    stats = {}
    # When: compatibility cannot be established.
    await stress.close_writer(writer, stats)
    # Then: no coroutine is spawned; cleanup is explicitly failed and aborted.
    assert aborted.is_set() and stats == {"cleanup_errors": 1}
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


@pytest.mark.asyncio
async def test_stream_close_cancellation_aborts_owned_transport_without_residual_task():
    # Given: a close Future that remains pending.
    started, aborted = asyncio.Event(), asyncio.Event()
    closed = asyncio.get_running_loop().create_future()
    writer = SimpleNamespace(
        close=started.set,
        transport=SimpleNamespace(abort=aborted.set),
        _protocol=SimpleNamespace(_get_close_waiter=lambda _: closed),
    )
    stats = {}
    task = asyncio.create_task(stress.close_writer(writer, stats))
    await started.wait()
    # When: the operation is cancelled while waiting for close completion.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    # Then: cancellation survives, the owned transport aborts, no waiter task remains.
    assert aborted.is_set() and closed.cancelled()
    assert not [item for item in asyncio.all_tasks() if item is not asyncio.current_task()]
