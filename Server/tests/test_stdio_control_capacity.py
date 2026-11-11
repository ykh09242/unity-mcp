"""Cancellation retains bounded independent workers and its original control budget."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace
import weakref

import pytest

from tests.timing import await_signal, watchdog_timeout
from transport.command_resume import CommandResumeInvocation, CommandResumeOwner
from transport.legacy import unity_connection as uc


@pytest.fixture
def control_owner(monkeypatch):
    pool = uc.UnityConnectionPool()
    connection = uc.UnityConnection(port=6400, instance_id="Control@deadbeef")
    connection._pool_owner = weakref.ref(pool)
    pool._connections[connection.instance_id] = connection
    monkeypatch.setattr(uc, "_unity_connection_pool", pool)
    owner = CommandResumeOwner(time.monotonic() + 60)
    owner.invocation = CommandResumeInvocation("a" * 32, "b" * 32, 123456789)
    owner.snapshot({"action": "save"})
    owner.cancel()
    return pool, connection, owner


@pytest.mark.asyncio
async def test_cancel_reaches_control_when_business_executor_is_saturated(
    control_owner, monkeypatch
):
    # Given: every default executor worker is occupied by a business reader.
    pool, connection, owner = control_owner
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    sent = []
    run_in_executor = loop.run_in_executor

    def business_reader():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())

    def send(_connection, command, params, **options):
        sent.append((command, params, options))

    monkeypatch.setattr(uc.UnityConnection, "send_command", send)
    with ThreadPoolExecutor(max_workers=1) as business:
        monkeypatch.setattr(
            loop,
            "run_in_executor",
            lambda executor, function, *args: run_in_executor(
                business if executor is None else executor, function, *args
            ),
        )
        reader = loop.run_in_executor(None, business_reader)
        try:
            await await_signal(entered, "business reader started")
            # When: cancellation must run while that reader remains occupied.
            await uc._cancel_stdio_resume(connection, owner, "manage_scene", {"action": "changed"})
            # Then: the original immutable parameters and resume identity reach control.
            assert len(sent) == 1
            command, params, options = sent[0]
            assert command == "manage_scene"
            assert params == {"action": "save"}
            assert options["_resume"].to_wire() == owner.invocation.to_wire()
            assert options["_resume"] is not owner.invocation
        finally:
            release.set()
            await reader
            await pool.aclose()


def queued_control(monkeypatch):
    """Hold submitted physical work without changing its awaiting Future."""
    loop = asyncio.get_running_loop()
    submitted = asyncio.Event()
    jobs = []
    clock = [100.0]
    monkeypatch.setattr(uc, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def submit(_loop, _executor, function, *args):
        future = loop.create_future()
        jobs.append((function, args, future))
        submitted.set()
        return future

    monkeypatch.setattr(uc, "submit_owned_executor", submit)
    return submitted, jobs, clock


def finish_queued(jobs):
    for function, args, future in jobs:
        try:
            future.set_result(function(*args))
        except Exception as error:
            future.set_exception(error)


@pytest.mark.asyncio
async def test_queued_cancel_does_not_send_after_its_control_deadline(control_owner, monkeypatch):
    # Given: cancellation has been submitted but no worker has started it.
    pool, connection, owner = control_owner
    submitted, jobs, clock = queued_control(monkeypatch)
    sent = []
    monkeypatch.setattr(
        uc.UnityConnection, "send_command", lambda *args, **kwargs: sent.append(args)
    )
    pending = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
    try:
        await await_signal(submitted, "control queued", producers=(pending,))
        # When: the queued worker starts only after its original I/O budget elapsed.
        clock[0] = 102.0
        finish_queued(jobs)
        await pending
        # Then: it performs no socket dispatch with a newly extended deadline.
        assert sent == []
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_control_deadline_includes_queue_wait_without_rebasing_resume(
    control_owner, monkeypatch
):
    # Given: a control request spends part of its fixed I/O budget queued.
    pool, connection, owner = control_owner
    submitted, jobs, clock = queued_control(monkeypatch)
    sent = []
    monkeypatch.setattr(
        uc.UnityConnection, "send_command", lambda *args, **options: sent.append(options)
    )
    pending = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
    try:
        await await_signal(submitted, "control queued", producers=(pending,))
        # When: the worker starts before expiry, after 0.75 seconds of queue time.
        clock[0] = 100.75
        finish_queued(jobs)
        await pending
        # Then: only the remaining control budget is used; native identity is unchanged.
        assert sent[0]["deadline"] == 101.0
        assert sent[0]["_resume"].to_wire() == owner.invocation.to_wire()
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_pool_teardown_retires_queued_control_before_dispatch(control_owner, monkeypatch):
    # Given: a pool owns cancellation that has not entered its worker yet.
    pool, connection, owner = control_owner
    submitted, jobs, _clock = queued_control(monkeypatch)
    sent = []
    monkeypatch.setattr(
        uc.UnityConnection, "send_command", lambda *args, **kwargs: sent.append(args)
    )
    pending = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
    await await_signal(submitted, "control queued", producers=(pending,))
    # When: pool retirement wins before the queued worker starts.
    closing = asyncio.create_task(pool.aclose())
    await asyncio.sleep(0)
    assert pool._retired
    assert not closing.done()
    finish_queued(jobs)
    await asyncio.gather(pending, closing)
    # Then: teardown settles ownership without starting a new native cancellation.
    assert sent == []
    assert not pool._resync_workers


@pytest.mark.asyncio
async def test_sync_reset_retires_queued_control_before_dispatch(control_owner, monkeypatch):
    # Given: a reusable pool owns a cancellation queued before its reset.
    pool, connection, owner = control_owner
    submitted, jobs, _clock = queued_control(monkeypatch)
    sent = []
    monkeypatch.setattr(
        uc.UnityConnection, "send_command", lambda *args, **kwargs: sent.append(args)
    )
    pending = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
    await await_signal(submitted, "control queued", producers=(pending,))
    # When: synchronous disposal replaces the discovery/connection generation.
    pool.disconnect_all()
    finish_queued(jobs)
    await pending
    # Then: pending work is fenced even though the reusable pool is not retired.
    assert not pool._retired
    assert sent == []
    await pool.aclose()


@pytest.mark.asyncio
async def test_sync_reset_keeps_control_fenced_while_waiting_for_discovery(
    control_owner, monkeypatch
):
    # Given: reset has retired controls but must wait for a discovery publication lock.
    pool, connection, owner = control_owner
    retired = pool._control_cancelled
    sent = []
    monkeypatch.setattr(
        uc.UnityConnection, "send_command", lambda *args, **kwargs: sent.append(args)
    )
    pool._scan_lock.acquire()
    resetting = asyncio.create_task(asyncio.to_thread(pool.disconnect_all))
    try:
        assert await asyncio.to_thread(retired.wait, watchdog_timeout())
        # When: another cancellation arrives before old connections are retired.
        await uc._cancel_stdio_resume(connection, owner, "manage_scene", {})
        # Then: reset cannot grant that stale connection a fresh control fence.
        assert sent == []
    finally:
        pool._scan_lock.release()
        await resetting
        await pool.aclose()


@pytest.mark.asyncio
async def test_cancelled_waiter_keeps_bounded_control_capacity_until_worker_exit(
    control_owner, monkeypatch
):
    # Given: one physical control worker and one queued control fill admission.
    pool, connection, owner = control_owner
    pool._control_executor.shutdown()
    pool._control_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-control")
    pool._control_capacity = threading.BoundedSemaphore(2)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    queued = asyncio.Event()
    release = threading.Event()
    sent = []
    submit = uc.submit_owned_executor
    submitted = []

    def observe_submit(*args):
        future = submit(*args)
        submitted.append(future)
        if len(submitted) == 2:
            queued.set()
        return future

    def send(_connection, _command, params, **_options):
        sent.append(params)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())

    monkeypatch.setattr(uc, "submit_owned_executor", observe_submit)
    monkeypatch.setattr(uc.UnityConnection, "send_command", send)
    first = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
    second = None
    try:
        await await_signal(entered, "control reader started", producers=(first,))
        second = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
        await await_signal(queued, "second control queued", producers=(second,))
        # When: the first async waiter exits while its physical worker remains occupied.
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await uc._cancel_stdio_resume(connection, owner, "manage_scene", {})
        # Then: cancellation cannot free a slot or add an unbounded queued worker.
        assert len(submitted) == 2
        assert len(sent) == 1
        assert len(pool._resync_workers) == 2
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )
        await pool.aclose()
    assert not pool._resync_workers
    assert all(not thread.is_alive() for thread in pool._control_executor._threads)


@pytest.mark.asyncio
async def test_pool_close_waits_for_physical_control_after_waiter_cancellation(
    control_owner, monkeypatch
):
    # Given: the caller has left a still-running control worker owned by its pool.
    pool, connection, owner = control_owner
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    def send(*_args, **_options):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(watchdog_timeout())
        finally:
            finished.set()

    monkeypatch.setattr(uc.UnityConnection, "send_command", send)
    pending = asyncio.create_task(uc._cancel_stdio_resume(connection, owner, "manage_scene", {}))
    closing = None
    try:
        await await_signal(entered, "control reader started", producers=(pending,))
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        # When: teardown retires the pool while physical control work is still running.
        closing = asyncio.create_task(pool.aclose())
        await asyncio.sleep(0)
        # Then: teardown still owns the worker and cannot dispose its executor early.
        assert pool._retired
        assert not closing.done()
        assert not finished.is_set()
        assert pool._resync_workers
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await (closing if closing is not None else pool.aclose())
    assert finished.is_set()
    assert not pool._resync_workers
    assert all(not thread.is_alive() for thread in pool._control_executor._threads)
