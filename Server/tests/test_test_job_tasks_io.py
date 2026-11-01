"""Journal result adoption must not move cancellation beyond its owning scope."""

import asyncio
import threading

import pytest

from tests.timing import watchdog_timeout
from transport.test_job_tasks import TestJobTasks


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [False, True])
@pytest.mark.parametrize("prior_cancellation", [False, True])
@pytest.mark.parametrize("followup", ["exit", "checkpoint", "io"])
@pytest.mark.parametrize("extra_cancellations", [0, 1, 2])
async def test_journal_io_redelivers_only_cancellation_not_consumed_by_owner(
    deadline, prior_cancellation, followup, extra_cancellations
):
    runtime = TestJobTasks.__new__(TestJobTasks)
    runtime._io_owners = 0
    runtime._close_if_idle = lambda: None
    entered, release = threading.Event(), threading.Event()
    adopted = []
    guard = asyncio.timeout(None)

    def commit():
        entered.set()
        assert release.wait(watchdog_timeout()), "Owned journal thread was not released"
        return "committed"

    async def request():
        if prior_cancellation:
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass

        async def adopt():
            adopted.append(await runtime._io(commit))
            if followup == "io":
                adopted.append(await runtime._io(lambda: "followup"))
            if followup != "exit":
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        outcome = "caller survived"
        if deadline:
            try:
                async with guard:
                    await adopt()
            except TimeoutError:
                outcome = "timed out"
        else:
            await adopt()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return outcome

    task = asyncio.create_task(request())
    try:
        async with asyncio.timeout(watchdog_timeout()):
            while not entered.is_set():
                await asyncio.sleep(0)
            if deadline:
                guard.reschedule(asyncio.get_running_loop().time())
            else:
                task.cancel()
            while task.cancelling() <= int(prior_cancellation):
                await asyncio.sleep(0)
            for _ in range(extra_cancellations):
                task.cancel()
            # The journal still owns its thread and result when cancellation arrives.
            assert runtime._io_owners == 1 and not task.done()
            release.set()
            if deadline and not extra_cancellations:
                assert await task == ("caller survived" if followup == "exit" else "timed out")
                assert guard.expired()
                assert task.cancelling() == int(prior_cancellation)
            else:
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert task.cancelling() == int(prior_cancellation) + extra_cancellations + (
                    not deadline
                )
        assert adopted == (["committed", "followup"] if followup == "io" else ["committed"])
        assert runtime._io_owners == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_cancellation", [False, True])
async def test_journal_io_preserves_nested_timeout_cancellation_counts(prior_cancellation):
    runtime = TestJobTasks.__new__(TestJobTasks)
    runtime._io_owners = 0
    runtime._close_if_idle = lambda: None
    entered, release = threading.Event(), threading.Event()
    outer, inner = asyncio.timeout(None), asyncio.timeout(None)
    adopted = []

    def commit():
        entered.set()
        assert release.wait(watchdog_timeout())
        return "committed"

    async def request():
        if prior_cancellation:
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        with pytest.raises(TimeoutError):
            async with outer, inner:
                adopted.append(await runtime._io(commit))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return asyncio.current_task().cancelling()

    task = asyncio.create_task(request())
    try:
        async with asyncio.timeout(watchdog_timeout()):
            while not entered.is_set():
                await asyncio.sleep(0)
            now = asyncio.get_running_loop().time()
            outer.reschedule(now)
            inner.reschedule(now)
            while task.cancelling() < int(prior_cancellation) + 2:
                await asyncio.sleep(0)
            assert runtime._io_owners == 1 and not task.done()
            release.set()
            assert await task == int(prior_cancellation)
        assert outer.expired() and inner.expired()
        assert adopted == ["committed"] and runtime._io_owners == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_journal_io_redelivery_does_not_change_an_already_completed_task():
    runtime = TestJobTasks.__new__(TestJobTasks)
    runtime._io_owners = 0
    runtime._close_if_idle = lambda: None
    entered, release = threading.Event(), threading.Event()

    def commit():
        entered.set()
        assert release.wait(watchdog_timeout())
        return "committed"

    async def request():
        return await runtime._io(commit)

    task = asyncio.create_task(request())
    try:
        async with asyncio.timeout(watchdog_timeout()):
            while not entered.is_set():
                await asyncio.sleep(0)
            task.cancel()
            release.set()
            assert await task == "committed"
            await asyncio.sleep(0)
            assert task.cancelling() == 1 and not task.cancelled()
            assert runtime._io_owners == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
