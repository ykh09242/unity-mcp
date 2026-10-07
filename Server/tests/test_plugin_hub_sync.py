"""The synchronous adapter owns coroutines until a live loop accepts them."""

import asyncio  # noqa: ANYIO_OK -- exercise the hub's asyncio loop boundary.
from contextlib import closing
import inspect
import threading

from anyio.from_thread import start_blocking_portal
import pytest

from transport.plugin_hub import PluginHub


async def completed_value() -> str:
    return "accepted"


def test_unconfigured_sync_call_closes_rejected_coroutine(monkeypatch: pytest.MonkeyPatch):
    # Given a coroutine with no configured hub loop.
    monkeypatch.setattr(PluginHub, "_loop", None)
    with closing(completed_value()) as operation:
        # When synchronous admission rejects it.
        with pytest.raises(RuntimeError):
            PluginHub._run_coroutine_sync(operation)
        # Then the rejected operation no longer retains a coroutine frame.
        assert inspect.getcoroutinestate(operation) == inspect.CORO_CLOSED


@pytest.mark.asyncio
async def test_same_loop_sync_call_closes_rejected_coroutine(monkeypatch: pytest.MonkeyPatch):
    # Given a caller on the hub's own loop, where blocking would deadlock.
    monkeypatch.setattr(PluginHub, "_loop", asyncio.get_running_loop())
    with closing(completed_value()) as operation:
        # When synchronous admission rejects it.
        with pytest.raises(RuntimeError):
            PluginHub._run_coroutine_sync(operation)
        # Then rejecting it also releases its frame.
        assert inspect.getcoroutinestate(operation) == inspect.CORO_CLOSED


def test_closed_loop_sync_call_closes_rejected_coroutine(monkeypatch: pytest.MonkeyPatch):
    # Given a closed loop retained by a stopped hub.
    loop = asyncio.new_event_loop()
    loop.close()
    monkeypatch.setattr(PluginHub, "_loop", loop)
    with closing(completed_value()) as operation:
        # When admission fails before the loop can accept the operation.
        with pytest.raises(RuntimeError):
            PluginHub._run_coroutine_sync(operation)
        # Then the rejected coroutine is released.
        assert inspect.getcoroutinestate(operation) == inspect.CORO_CLOSED


def test_stopped_loop_sync_call_rejects_without_waiting(monkeypatch: pytest.MonkeyPatch):
    # Given an open loop that is not running and cannot execute submitted work.
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(PluginHub, "_loop", loop)
    finished = threading.Event()
    settled = asyncio.Event()
    failures: list[RuntimeError] = []

    def invoke() -> None:
        try:
            PluginHub._run_coroutine_sync(operation)
        except RuntimeError as error:
            failures.append(error)
        finally:
            finished.set()
            loop.call_soon_threadsafe(settled.set)

    with closing(completed_value()) as operation:
        caller = threading.Thread(target=invoke, daemon=True)
        try:
            # When a worker makes the synchronous call, it must reject, not hang.
            caller.start()
            assert finished.wait(2), "A stopped loop left the synchronous caller blocked"
            assert len(failures) == 1
            assert inspect.getcoroutinestate(operation) == inspect.CORO_CLOSED
        finally:
            # Also unblock and clean up the unfixed implementation after a RED failure.
            loop.run_until_complete(asyncio.wait_for(settled.wait(), timeout=2))
            caller.join(2)
            loop.close()
            assert not caller.is_alive()


def test_live_loop_sync_call_returns_the_actual_result(monkeypatch: pytest.MonkeyPatch):
    # Given a real loop owned by another thread.
    with start_blocking_portal() as portal:
        monkeypatch.setattr(PluginHub, "_loop", portal.call(asyncio.get_running_loop))
        # When the coroutine is accepted, then its actual result is returned.
        assert PluginHub._run_coroutine_sync(completed_value()) == "accepted"


def test_live_loop_sync_call_preserves_operation_error(monkeypatch: pytest.MonkeyPatch):
    # Given an operation which fails after the loop accepts it.
    failure = LookupError("owned operation failed")

    async def reject() -> None:
        raise failure

    with start_blocking_portal() as portal:
        monkeypatch.setattr(PluginHub, "_loop", portal.call(asyncio.get_running_loop))
        # When the operation fails, then the caller receives its original exception.
        with pytest.raises(LookupError) as actual:
            PluginHub._run_coroutine_sync(reject())
        assert actual.value is failure
