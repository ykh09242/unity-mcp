"""Test runner watchdogs are separate from production deadline semantics."""

import asyncio
import pytest


def test_watchdog_defaults_and_slow_runner_override(monkeypatch):
    from tests.timing import watchdog_timeout

    monkeypatch.delenv("UNITY_MCP_TEST_TIMEOUT_SCALE", raising=False)
    assert watchdog_timeout() == 60
    assert watchdog_timeout(900) == 900
    monkeypatch.setenv("UNITY_MCP_TEST_TIMEOUT_SCALE", "2.5")
    assert watchdog_timeout() == 150
    assert watchdog_timeout(900) == 2250


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "1e309", "oops", ""])
def test_invalid_watchdog_override_fails_explicitly(monkeypatch, value):
    from tests.timing import watchdog_timeout

    monkeypatch.setenv("UNITY_MCP_TEST_TIMEOUT_SCALE", value)
    with pytest.raises(ValueError, match="UNITY_MCP_TEST_TIMEOUT_SCALE"):
        watchdog_timeout()


@pytest.mark.asyncio
async def test_completion_does_not_sleep_until_watchdog(monkeypatch):
    from tests.timing import await_completion

    monkeypatch.setenv("UNITY_MCP_TEST_TIMEOUT_SCALE", "10")
    future = asyncio.get_running_loop().create_future()
    future.set_result("finished")
    assert await await_completion(future, "fixture result") == "finished"


@pytest.mark.asyncio
async def test_watchdog_failure_keeps_owned_operation_available_for_inspection():
    from tests.timing import await_completion

    future = asyncio.get_running_loop().create_future()
    with pytest.raises(AssertionError, match="fixture stalled"):
        await await_completion(future, "fixture stalled", timeout=0)
    assert not future.done()
    future.set_result("late completion")
    assert await await_completion(future, "late fixture") == "late completion"


@pytest.mark.asyncio
async def test_completion_preserves_failures_and_cancellation():
    from tests.timing import await_completion

    loop = asyncio.get_running_loop()
    failed = loop.create_future()
    failed.set_exception(RuntimeError("owned failure"))
    with pytest.raises(RuntimeError, match="owned failure"):
        await await_completion(failed, "fixture failure")
    cancelled = loop.create_future()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await await_completion(cancelled, "fixture cancellation")


@pytest.mark.asyncio
async def test_signal_waiter_is_released_on_timeout():
    from tests.timing import await_signal

    event = asyncio.Event()
    before = asyncio.all_tasks()
    with pytest.raises(AssertionError, match="missing signal"):
        await await_signal(event, "missing signal", timeout=0)
    assert asyncio.all_tasks() == before
    event.set()
    await await_signal(event, "ready signal")


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_cancel_observed_operation():
    from tests.timing import await_completion

    future = asyncio.get_running_loop().create_future()
    waiter = asyncio.create_task(await_completion(future, "caller cancelled"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not future.done()
    future.cancel()


@pytest.mark.asyncio
async def test_pending_completion_wakes_when_result_arrives():
    from tests.timing import await_completion

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    loop.call_soon(future.set_result, "completed after observation started")
    assert not future.done()
    assert (
        await await_completion(future, "pending fixture") == "completed after observation started"
    )


@pytest.mark.asyncio
async def test_cancelled_signal_observer_releases_its_waiter():
    from tests.timing import await_signal

    entered = asyncio.Event()

    class ObservedEvent(asyncio.Event):
        async def wait(self):
            entered.set()
            return await super().wait()

    event = ObservedEvent()
    before = asyncio.all_tasks()
    observer = asyncio.create_task(await_signal(event, "cancelled signal"))
    await await_signal(entered, "signal observation started")
    observer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await observer
    assert asyncio.all_tasks() == before
    assert not event.is_set()


@pytest.mark.asyncio
async def test_signal_reports_successful_producer_exit_instead_of_waiting_for_missing_phase():
    from tests.timing import await_signal

    event = asyncio.Event()
    producer = asyncio.get_running_loop().create_future()
    producer.set_result({"reason": "deadline_before_send"})
    before = asyncio.all_tasks()
    with pytest.raises(AssertionError, match="before fixture send.*deadline_before_send"):
        await await_signal(event, "fixture send", producers=(producer,))
    assert asyncio.all_tasks() == before
    assert producer.result() == {"reason": "deadline_before_send"}
    assert not event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_signal_preserves_early_producer_failure_and_cancellation(cancelled):
    from tests.timing import await_signal

    producer = asyncio.get_running_loop().create_future()
    if cancelled:
        producer.cancel()
    else:
        producer.set_exception(RuntimeError("owned producer failed"))
    before = asyncio.all_tasks()
    expected = asyncio.CancelledError if cancelled else RuntimeError
    with pytest.raises(expected):
        await await_signal(asyncio.Event(), "never entered", producers=(producer,))
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_phase_completion_wins_without_cancelling_other_producers():
    from tests.timing import await_completion, await_signal

    loop = asyncio.get_running_loop()
    phase, producer, other = (loop.create_future() for _ in range(3))
    phase.set_result("phase finished")
    producer.set_result("producer finished too")
    assert await await_completion(phase, "ready", producers=(producer, other)) == "phase finished"
    event = asyncio.Event()
    event.set()
    await await_signal(event, "already entered", producers=(producer, other))
    assert not other.done()
    other.cancel()


@pytest.mark.asyncio
async def test_completion_observes_all_producers_and_preserves_pending_work():
    from tests.timing import await_completion

    loop = asyncio.get_running_loop()
    phase, pending, failed = (loop.create_future() for _ in range(3))
    loop.call_soon(failed.set_exception, RuntimeError("second producer failed"))
    with pytest.raises(RuntimeError, match="second producer failed"):
        await await_completion(phase, "queue item", producers=(pending, failed))
    assert not phase.done() and not pending.done()
    phase.cancel()
    pending.cancel()


@pytest.mark.asyncio
async def test_phase_timeout_and_observer_cancellation_leave_producer_untouched():
    from tests.timing import await_signal

    producer = asyncio.get_running_loop().create_future()
    before = asyncio.all_tasks()
    with pytest.raises(AssertionError, match="missing phase"):
        await await_signal(asyncio.Event(), "missing phase", timeout=0, producers=(producer,))
    assert not producer.done()
    assert asyncio.all_tasks() == before
    observer = asyncio.create_task(
        await_signal(asyncio.Event(), "cancelled phase", producers=(producer,))
    )
    await asyncio.sleep(0)
    observer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await observer
    assert not producer.done()
    assert asyncio.all_tasks() == before
    producer.cancel()
