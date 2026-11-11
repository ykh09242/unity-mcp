"""The synchronous adapter owns coroutines until a live loop accepts them."""

from tests.timing import watchdog_timeout

import asyncio  # noqa: ANYIO_OK -- exercise the hub's asyncio loop boundary.
from contextlib import closing
from concurrent.futures import CancelledError as ThreadCancelledError
from contextvars import ContextVar
import inspect
import threading
import weakref
from types import SimpleNamespace

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

    async def operation():
        invoked.append(True)
        return "unexpected dispatch"

    with start_blocking_portal() as portal:
        loop = portal.call(asyncio.get_running_loop)
        monkeypatch.setattr(PluginHub, "_loop", loop)
        original_schedule = loop.call_soon_threadsafe

        def schedule(callback, *args, **kwargs):
            def replace_before_admission():
                monkeypatch.setattr(PluginHub, "_registry", object())
                callback(*args)

            return original_schedule(replace_before_admission, **kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(loop, "call_soon_threadsafe", schedule)
            with closing(operation()) as coro:
                with pytest.raises(RuntimeError, match="replaced"):
                    PluginHub._run_coroutine_sync(coro)
                assert invoked == []
                assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED


def test_abandonment_while_loop_enters_ownership_returns_not_dispatched(monkeypatch):
    loop = asyncio.new_event_loop()
    callback_entered, callback_release = threading.Event(), threading.Event()
    loop_at_ownership, finished, submitted = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    raw_lock = threading.Lock()
    failures, results, invoked, outstanding = [], [], [], []

    class ContendedOwnership:
        """Coordinate the real thread lock without replacing its synchronization."""

        def __enter__(self):
            if threading.current_thread() is caller:
                raw_lock.acquire()
                if submitted.is_set():
                    callback_release.set()
                    assert loop_at_ownership.wait(watchdog_timeout())
            else:
                loop_at_ownership.set()
                raw_lock.acquire()
            return self

        def __exit__(self, *_args):
            raw_lock.release()

    def block_loop():
        callback_entered.set()
        assert callback_release.wait(watchdog_timeout())

    async def operation() -> None:
        invoked.append(True)

    coro = operation()

    def invoke():
        try:
            results.append(PluginHub.send_command_blocking("hash", "manage_scene", {}))
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    original_schedule = loop.call_soon_threadsafe

    def schedule(callback, *args, **kwargs):
        handle = original_schedule(callback, *args, **kwargs)
        if threading.current_thread() is caller and not submitted.is_set():
            monkeypatch.setattr(PluginHub, "_registry", object())
            submitted.set()
        return handle

    monkeypatch.setattr(PluginHub, "_loop", loop)
    monkeypatch.setattr(PluginHub, "send_command_for_instance", lambda *_args: coro)
    monkeypatch.setattr("transport.plugin_hub.threading", SimpleNamespace(Lock=ContendedOwnership))
    monkeypatch.setattr(loop, "call_soon_threadsafe", schedule)
    owner = threading.Thread(target=loop.run_forever, daemon=True)
    caller = threading.Thread(target=invoke, daemon=True)
    loop.call_soon(block_loop)
    owner.start()
    assert callback_entered.wait(watchdog_timeout())
    caller.start()
    try:
        assert finished.wait(watchdog_timeout())
        assert failures == []
        assert results[0]["data"]["dispatch"] == "not_dispatched"
        assert invoked == []
        assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
    finally:
        callback_release.set()

        def stop_when_idle():
            outstanding.extend(asyncio.all_tasks(loop))
            loop.stop()

        original_schedule(stop_when_idle)
        caller.join(watchdog_timeout())
        owner.join(watchdog_timeout())
        loop.close()
        coro.close()
        assert not caller.is_alive() and not owner.is_alive()
        assert outstanding == []


@pytest.mark.parametrize("boundary", ["schedule", "schedule_after_queue", "create_task"])
def test_sync_admission_failure_settles_caller_and_closes_unaccepted_coroutine(
    monkeypatch, boundary
):
    failure = LookupError("controlled admission failure")
    rejected, invoked = [], []

    async def should_not_start():
        invoked.append(True)
        return "accepted"

    def reject(candidate, *_args, **_kwargs):
        if inspect.iscoroutine(candidate):
            rejected.append(candidate)
        if boundary == "schedule_after_queue":
            original_schedule(candidate, *_args, **_kwargs)
        raise failure

    with start_blocking_portal() as portal:
        loop = portal.call(asyncio.get_running_loop)
        original_schedule = loop.call_soon_threadsafe
        monkeypatch.setattr(PluginHub, "_loop", loop)
        with closing(should_not_start()) as operation:
            try:
                with monkeypatch.context() as scoped:
                    scoped.setattr(
                        loop,
                        "create_task" if boundary == "create_task" else "call_soon_threadsafe",
                        reject,
                    )
                    scoped.setattr(loop, "call_exception_handler", lambda _context: None)
                    with pytest.raises(LookupError) as actual:
                        PluginHub._run_coroutine_sync(operation)
                    assert actual.value is failure
                    assert inspect.getcoroutinestate(operation) == inspect.CORO_CLOSED
            finally:
                # Dispose the baseline implementation's rejected wrapper after RED.
                for candidate in rejected:
                    candidate.close()
        portal.call(lambda: None)
        assert invoked == []


@pytest.mark.parametrize("eager", [False, True])
@pytest.mark.parametrize("outcome", ["value", "error", "cancel"])
def test_sync_task_settlement_preserves_context_and_outcome(monkeypatch, eager, outcome):
    if eager and not hasattr(asyncio, "eager_task_factory"):
        pytest.skip("Eager task factories require Python 3.12 or newer")
    context = ContextVar("sync_admission_test_context")
    marker, failure = object(), LookupError("owned operation error")

    async def operation():
        assert context.get() is marker
        if outcome == "error":
            raise failure
        if outcome == "cancel":
            raise asyncio.CancelledError
        return marker

    with start_blocking_portal() as portal:
        loop = portal.call(asyncio.get_running_loop)
        monkeypatch.setattr(PluginHub, "_loop", loop)
        previous = portal.call(loop.get_task_factory)
        if eager:
            portal.call(loop.set_task_factory, asyncio.eager_task_factory)
        token = context.set(marker)
        try:
            if outcome == "value":
                assert PluginHub._run_coroutine_sync(operation()) is marker
            elif outcome == "error":
                with pytest.raises(LookupError) as actual:
                    PluginHub._run_coroutine_sync(operation())
                assert actual.value is failure
            else:
                with pytest.raises(ThreadCancelledError):
                    PluginHub._run_coroutine_sync(operation())
        finally:
            context.reset(token)
            portal.call(loop.set_task_factory, previous)


def test_slow_task_creation_cannot_hold_the_sync_caller_past_its_deadline(monkeypatch):
    loop = asyncio.new_event_loop()
    ready, creating, release, finished = (threading.Event() for _ in range(4))
    invoked, results, failures = [], [], []

    async def operation():
        invoked.append(True)
        return {"success": True}

    coro = operation()
    original_create = loop.create_task

    def held_create(candidate, **kwargs):
        if candidate is coro:
            creating.set()
            assert release.wait(watchdog_timeout())
        return original_create(candidate, **kwargs)

    def invoke():
        try:
            results.append(PluginHub.send_command_blocking("hash", "manage_scene", {}))
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    monkeypatch.setattr(PluginHub, "_loop", loop)
    monkeypatch.setattr(PluginHub, "send_command_for_instance", lambda *_args: coro)
    monkeypatch.setattr(PluginHub, "_command_timeouts", lambda *_args: (0.1, 0.1))
    monkeypatch.setattr(PluginHub, "CANCEL_TIMEOUT", 0)
    monkeypatch.setattr(loop, "create_task", held_create)
    owner = threading.Thread(target=loop.run_forever, daemon=True)
    caller = threading.Thread(target=invoke, daemon=True)
    loop.call_soon(ready.set)
    owner.start()
    assert ready.wait(watchdog_timeout())
    caller.start()
    try:
        assert creating.wait(watchdog_timeout())
        assert finished.wait(0.5), "Task creation retained the caller's ownership lock"
        assert failures == []
        assert results[0]["data"]["reason"] == "outcome_unknown"
        assert invoked == []
    finally:
        release.set()
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(watchdog_timeout())
        loop.call_soon_threadsafe(loop.stop)
        caller.join(watchdog_timeout())
        owner.join(watchdog_timeout())
        loop.close()
        assert not caller.is_alive() and not owner.is_alive()
    assert invoked == []
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED


def test_abandoned_submission_releases_coroutine_payload_without_loop_restart(monkeypatch):
    loop = asyncio.new_event_loop()
    ready, stopped = threading.Event(), threading.Event()

    class Payload:
        pass

    async def operation(payload):
        raise AssertionError(f"Abandoned payload executed: {type(payload)}")

    payload = Payload()
    retained = weakref.ref(payload)
    coro = operation(payload)
    retained_coroutine = weakref.ref(coro)
    del payload

    def run_loop():
        loop.call_soon(ready.set)
        loop.run_forever()
        stopped.set()

    original_schedule = loop.call_soon_threadsafe

    def stop_before_admission(callback, *args, **kwargs):
        original_schedule(loop.stop)
        assert stopped.wait(watchdog_timeout())
        return original_schedule(callback, *args, **kwargs)

    monkeypatch.setattr(PluginHub, "_loop", loop)
    monkeypatch.setattr(loop, "call_soon_threadsafe", stop_before_admission)
    owner = threading.Thread(target=run_loop, daemon=True)
    owner.start()
    assert ready.wait(watchdog_timeout())
    try:
        with pytest.raises(RuntimeError, match="stopped"):
            PluginHub._run_coroutine_sync(coro)
        assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
        # A closed coroutine can retain its arguments on supported runtimes.
        # Release caller ownership before checking the queued admission's owner.
        coro = None
        assert retained_coroutine() is None
        assert retained() is None
    finally:
        owner.join(watchdog_timeout())
        loop.close()  # No restart or cleanup turn is required for unaccepted work.
        if coro is not None:
            coro.close()
        assert not owner.is_alive()


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

    original_schedule = loop.call_soon_threadsafe
    submitted = threading.Event()

    def schedule(callback, *args, **kwargs):
        if stop_before_submission and not submitted.is_set():
            original_schedule(loop.stop)
            assert stopped.wait(watchdog_timeout())
        submitted.set()
        return original_schedule(callback, *args, **kwargs)

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
    monkeypatch.setattr(loop, "call_soon_threadsafe", schedule)
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
        if stop_before_submission:
            assert invoked == [], "Abandoned admission executed when the loop restarted"
