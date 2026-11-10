"""The synchronous adapter owns coroutines until a live loop accepts them."""

from tests.timing import watchdog_timeout

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
            assert finished.wait(watchdog_timeout()), (
                "A stopped loop left the synchronous caller blocked"
            )
            assert len(failures) == 1
            assert inspect.getcoroutinestate(operation) == inspect.CORO_CLOSED
        finally:
            # Also unblock and clean up the unfixed implementation after a RED failure.
            loop.run_until_complete(asyncio.wait_for(settled.wait(), timeout=watchdog_timeout()))
            caller.join(watchdog_timeout())
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


@pytest.mark.parametrize("replace_registry", [False, True])
def test_live_sync_wait_has_deadline_and_generation_fence(monkeypatch, replace_registry):
    released = threading.Event()

    async def operation():
        try:
            if replace_registry:
                monkeypatch.setattr(PluginHub, "_registry", object())
            await asyncio.Event().wait()
        finally:
            released.set()

    with start_blocking_portal() as portal:
        monkeypatch.setattr(PluginHub, "_loop", portal.call(asyncio.get_running_loop))
        with pytest.raises(RuntimeError, match="replaced" if replace_registry else "deadline"):
            PluginHub._run_coroutine_sync(operation(), timeout_s=0.2)
        assert released.wait(watchdog_timeout())


def test_sync_wait_preserves_operation_timeout_error(monkeypatch):
    failure = TimeoutError("operation timed out")

    async def operation():
        raise failure

    with start_blocking_portal() as portal:
        monkeypatch.setattr(PluginHub, "_loop", portal.call(asyncio.get_running_loop))
        with pytest.raises(TimeoutError) as actual:
            PluginHub._run_coroutine_sync(operation())
        assert actual.value is failure


def test_replacement_before_loop_acceptance_cannot_start_operation(monkeypatch):
    invoked = []
    original_submit = asyncio.run_coroutine_threadsafe

    async def operation():
        invoked.append(True)
        return "unexpected dispatch"

    def submit(coro, loop):
        monkeypatch.setattr(PluginHub, "_registry", object())
        future = original_submit(coro, loop)
        # Force loop acceptance before the caller can perform its next check.
        try:
            future.result(timeout=watchdog_timeout())
        except RuntimeError:
            pass
        return future

    with start_blocking_portal() as portal:
        monkeypatch.setattr(PluginHub, "_loop", portal.call(asyncio.get_running_loop))
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
        with closing(operation()) as coro:
            with pytest.raises(RuntimeError, match="replaced"):
                PluginHub._run_coroutine_sync(coro)
            assert invoked == []
            assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED


@pytest.mark.parametrize("stop_before_submission", [True, False])
@pytest.mark.parametrize("command_call", [False, True])
def test_loop_stop_race_releases_sync_caller_without_restart(
    monkeypatch: pytest.MonkeyPatch, stop_before_submission: bool, command_call: bool
):
    loop = asyncio.new_event_loop()
    ready, stopped, finished = threading.Event(), threading.Event(), threading.Event()
    failures: list[BaseException] = []
    results = []
    invoked: list[bool] = []

    def run_loop() -> None:
        loop.call_soon(ready.set)
        loop.run_forever()
        stopped.set()

    async def operation() -> None:
        invoked.append(True)
        loop.stop()
        await asyncio.Event().wait()

    original_submit = asyncio.run_coroutine_threadsafe
    submitted = []

    def submit(coro, target):
        if stop_before_submission:
            target.call_soon_threadsafe(target.stop)
            assert stopped.wait(watchdog_timeout())
        future = original_submit(coro, target)
        submitted.append(future)
        return future

    coro = operation()

    def command_operation(*args):
        assert args == ("owned-project", "manage_scene", {"action": "save"})
        return coro

    def invoke() -> None:
        try:
            if command_call:
                results.append(
                    PluginHub.send_command_blocking(
                        "owned-project", "manage_scene", {"action": "save"}
                    )
                )
            else:
                PluginHub._run_coroutine_sync(coro)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    monkeypatch.setattr(PluginHub, "_loop", loop)
    monkeypatch.setattr(PluginHub, "send_command_for_instance", command_operation)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
    owner = threading.Thread(target=run_loop, daemon=True)
    caller = threading.Thread(target=invoke, daemon=True)
    owner.start()
    assert ready.wait(watchdog_timeout())
    caller.start()
    try:
        assert stopped.wait(watchdog_timeout())
        assert finished.wait(0.5), "Stopped loop stranded the synchronous caller"
        if command_call:
            assert failures == []
            assert len(results) == 1
            if stop_before_submission:
                assert results[0]["data"]["dispatch"] == "not_dispatched"
            else:
                assert results[0]["data"]["reason"] == "outcome_unknown"
                assert results[0]["hint"] == "inspect_state_before_retry"
                assert invoked == [True]
        else:
            assert len(failures) == 1
            assert isinstance(failures[0], RuntimeError)
        if stop_before_submission:
            assert invoked == []
            assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
    finally:
        owner.join(watchdog_timeout())
        for future in submitted:
            future.cancel()

        async def drain() -> None:
            # Give even the unfixed submission a turn, then dispose its task.
            await asyncio.sleep(0)
            for task in asyncio.all_tasks(loop):
                if task is not asyncio.current_task():
                    task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        # The operation can stop the cleanup loop on its first turn in RED.
        cleanup = loop.create_task(drain())
        while not cleanup.done():
            loop.call_soon(loop.stop)
            loop.run_forever()
        loop.call_soon(loop.stop)
        loop.run_forever()
        caller.join(watchdog_timeout())
        loop.close()
        coro.close()
        assert not caller.is_alive()
