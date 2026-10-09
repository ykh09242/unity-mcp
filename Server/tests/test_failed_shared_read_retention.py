"""Failed retained reads must not park exception payloads after their final lease."""

import asyncio
import gc
from types import SimpleNamespace
from unittest.mock import AsyncMock
import weakref

import pytest
import pytest_asyncio

from core.config import config
from services.resources import editor_state as editor
from services.tools.shared_read_budget import SharedReadBudget
from services.tools.shared_tool_reads import SharedToolReads
import services.tools.shared_tool_reads as sharing


class Snapshot(dict):
    """Add weakref observation without changing dict operations."""


@pytest_asyncio.fixture
async def cache(monkeypatch):
    budget = SharedReadBudget()
    reads = SharedToolReads(freshness_s=10, retention_s=10)
    monkeypatch.setattr(sharing, "shared_read_budget", budget)
    state = SimpleNamespace(reads=reads, budget=budget)
    try:
        yield state
    finally:
        entries = reads._loops.get(asyncio.get_running_loop(), {})
        for key in list(entries):
            await reads.invalidate(key)
        assert budget.retained_bytes == 0


async def collect():
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gc.collect()


def entries(reads):
    return reads._loops.get(asyncio.get_running_loop(), {})


@pytest.mark.asyncio
async def test_public_stdio_capacity_failure_does_not_cache_rejected_snapshots(cache, monkeypatch):
    refs = []
    cache.budget.max_bytes = 0
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(editor, "_stdio_state_reads", cache.reads)
    monkeypatch.setattr(
        editor, "get_unity_instance_from_context", AsyncMock(side_effect=lambda ctx: ctx.instance)
    )
    monkeypatch.setattr(
        editor, "get_authenticated_stdio_generation", AsyncMock(return_value="generation")
    )

    async def send(*args, **kwargs):
        value = Snapshot(success=True, data={"body": "x" * (1024 * 1024)})
        refs.append(weakref.ref(value))
        return value

    monkeypatch.setattr(editor.unity_transport, "send_with_unity_instance", send)
    for index in range(20):
        result = await editor.get_editor_state(SimpleNamespace(instance=f"Instance@{index:032x}"))
        assert not result.success and result.data == {"reason": "result_capacity"}
    await collect()
    assert cache.budget.retained_bytes == 0
    assert all(ref() is None for ref in refs)
    assert not entries(cache.reads)


@pytest.mark.asyncio
async def test_failed_flight_waits_for_last_lease_then_drops_traceback_payload(cache):
    began, finish = asyncio.Event(), asyncio.Event()
    failed = [asyncio.Event(), asyncio.Event()]
    leave = [asyncio.Event(), asyncio.Event()]
    refs, calls = [], 0

    async def fetch():
        nonlocal calls
        calls += 1
        began.set()
        value = Snapshot(body="x" * (1024 * 1024))
        refs.append(weakref.ref(value))
        await finish.wait()
        raise RuntimeError("failed query")

    async def caller(index):
        async with cache.reads.session("key") as read:
            try:
                await read.fetch(fetch)
            except RuntimeError:
                pass
            failed[index].set()
            await leave[index].wait()

    tasks = [asyncio.create_task(caller(index)) for index in range(2)]
    try:
        await began.wait()
        finish.set()
        await asyncio.gather(*(event.wait() for event in failed))
        assert calls == 1 and entries(cache.reads)["key"].callers == 2
        leave[0].set()
        await tasks[0]
        assert entries(cache.reads)["key"].callers == 1
        assert refs[0]() is not None
        leave[1].set()
        await tasks[1]
        await collect()
        assert refs[0]() is None and not entries(cache.reads)

        async def retry():
            nonlocal calls
            calls += 1
            return {"data": "fresh"}

        async with cache.reads.session("key") as read:
            assert await read.fetch(retry) == {"data": "fresh"}
        assert calls == 2
    finally:
        finish.set()
        for event in leave:
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_completed_flight_is_not_retained_for_ttl(cache):
    async def fetch():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        async with cache.reads.session("key") as read:
            await read.fetch(fetch)
    assert not entries(cache.reads) and read._flight is None


@pytest.mark.asyncio
async def test_cancelled_caller_preserves_inflight_dedup_and_success_ttl(cache):
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return {"data": "owned"}

    async def caller():
        async with cache.reads.session("key") as read:
            return await read.fetch(fetch)

    task = asyncio.create_task(caller())
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not entries(cache.reads)["key"]._flight.task.done()
        next_call = asyncio.create_task(caller())
        finish.set()
        assert await next_call == {"data": "owned"}
        assert await caller() == {"data": "owned"}
        assert calls == 1
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate", [False, True])
async def test_old_failed_lease_does_not_remove_replacement_success(cache, invalidate):
    failed, leave = asyncio.Event(), asyncio.Event()

    async def failure():
        raise RuntimeError("failed query")

    async def old_caller():
        async with cache.reads.session("key") as read:
            try:
                await read.fetch(failure)
            except RuntimeError:
                pass
            failed.set()
            await leave.wait()

    task = asyncio.create_task(old_caller())
    calls = 0

    async def success():
        nonlocal calls
        calls += 1
        return {"data": "replacement"}

    try:
        await failed.wait()
        if invalidate:
            await cache.reads.invalidate("key")
        async with cache.reads.session("key") as current:
            assert await current.fetch(success) == {"data": "replacement"}
        leave.set()
        await task
        assert entries(cache.reads)["key"] is current
        async with cache.reads.session("key") as read:
            assert await read.fetch(success) == {"data": "replacement"}
        assert calls == 1
    finally:
        leave.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["capacity", "factory"])
async def test_idle_inflight_failure_releases_late_payload_without_waiting_for_ttl(cache, failure):
    started, finish = asyncio.Event(), asyncio.Event()
    refs = []
    cache.budget.max_bytes = 0

    async def fetch():
        started.set()
        await finish.wait()
        value = Snapshot(body="x" * (1024 * 1024))
        refs.append(weakref.ref(value))
        if failure == "factory":
            raise RuntimeError("late failed query")
        return value

    async def caller():
        async with cache.reads.session("key") as read:
            await read.fetch(fetch)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    diagnostics = []
    # Python 3.14 shield reports late failures even when the owned task is drained.
    # Observe messages without letting a diagnostic log retain the exception graph.
    loop.set_exception_handler(lambda _loop, context: diagnostics.append(context["message"]))
    task = asyncio.create_task(caller())
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        read = entries(cache.reads)["key"]
        assert not read._flight.task.done()
        finish.set()
        await asyncio.gather(read._flight.task, return_exceptions=True)
        await collect()
        assert not entries(cache.reads) and read._flight is None
        assert all(ref() is None for ref in refs)
        assert cache.budget.retained_bytes == 0
        assert all("exception in shielded future" in message for message in diagnostics)
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        loop.set_exception_handler(previous_handler)
