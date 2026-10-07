"""Lifecycle and capacity bounds for reads shared by batch and test-job callers."""

import asyncio

import pytest

from services.tools.shared_tool_reads import SharedToolReads


@pytest.mark.asyncio
async def test_active_session_reuses_only_fresh_values_and_releases_all_state():
    reads = SharedToolReads[int](freshness_s=0.02)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return calls

    async with reads.session("job") as first:
        assert await first.fetch(fetch) == 1
        async with reads.session("job") as second:
            assert await second.fetch(fetch) == 1
        await asyncio.sleep(0.03)
        assert await first.fetch(fetch) == 2
    assert reads._loops[asyncio.get_running_loop()] == {}
    async with reads.session("job") as fresh:
        assert await fresh.fetch(fetch) == 3


@pytest.mark.asyncio
async def test_capacity_uses_private_reads_without_evicting_live_shared_job():
    reads = SharedToolReads[int](freshness_s=1, max_entries=1)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return calls

    async with reads.session("tracked") as tracked:
        await tracked.fetch(fetch)
        async with reads.session("overflow") as first, reads.session("overflow") as second:
            await asyncio.gather(first.fetch(fetch), second.fetch(fetch))
            assert calls == 3
            assert len(reads._loops[asyncio.get_running_loop()]) == 1
        assert await tracked.fetch(fetch) == 1
    assert reads._loops[asyncio.get_running_loop()] == {}


@pytest.mark.asyncio
async def test_last_cancelled_waiter_cancels_and_drains_orphan_then_can_retry():
    reads = SharedToolReads[int]()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def fetch():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def wait():
        async with reads.session("job") as read:
            return await read.fetch(fetch)

    waiter = asyncio.create_task(wait())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cancelled.is_set()
    assert reads._loops[asyncio.get_running_loop()] == {}
    async with reads.session("job") as fresh:

        async def retry():
            return 7

        assert await fresh.fetch(retry) == 7


@pytest.mark.asyncio
async def test_failed_shared_fetch_is_drained_and_does_not_poison_next_session():
    reads = SharedToolReads[int]()

    async def fail():
        await asyncio.sleep(0.01)
        raise OSError("fake transport failure")

    async def wait():
        async with reads.session("job") as read:
            return await read.fetch(fail)

    replies = await asyncio.gather(wait(), wait(), return_exceptions=True)
    assert all(isinstance(reply, OSError) for reply in replies)
    assert reads._loops[asyncio.get_running_loop()] == {}
    async with reads.session("job") as fresh:

        async def retry():
            return 7

        assert await fresh.fetch(retry) == 7


def test_pool_can_be_reused_after_event_loop_replacement():
    reads = SharedToolReads[int](freshness_s=1)
    calls = 0

    async def run():
        async def fetch():
            nonlocal calls
            calls += 1
            return calls

        async with reads.session("job") as read:
            return await read.fetch(fetch)

    assert asyncio.run(run()) == 1
    assert asyncio.run(run()) == 2
