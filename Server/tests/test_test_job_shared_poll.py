"""Concurrent long waits share Unity reads without sharing caller lifetimes."""

import asyncio
import importlib
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

jobs = importlib.import_module("services.tools.run_tests")
RUNNING = {"success": True, "data": {"job_id": "job", "status": "running"}}
DONE = {"success": True, "data": {"job_id": "job", "status": "succeeded"}}


@pytest.fixture
def transport(monkeypatch):
    sender = AsyncMock(return_value=deepcopy(DONE))
    monkeypatch.setattr(jobs.config, "http_remote_hosted", False)
    monkeypatch.setattr(jobs.config, "transport_mode", "http")
    monkeypatch.setattr(jobs, "get_unity_instance_from_context", AsyncMock(return_value="Game@one"))
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", sender)
    return sender


def context():
    return SimpleNamespace(
        get_state=AsyncMock(
            side_effect=lambda key: {
                "unity_session_id": "owned-session",
            }.get(key)
        )
    )


@pytest.mark.asyncio
async def test_twenty_concurrent_waiters_share_two_polling_rounds(transport):
    started = time.monotonic()

    async def fetch(*args, **kwargs):
        first_round = time.monotonic() - started < 1
        await asyncio.sleep(0.02)
        return deepcopy(RUNNING if first_round else DONE)

    transport.side_effect = fetch
    replies = await asyncio.gather(
        *(jobs.get_test_job(context(), "job", wait_timeout=5) for _ in range(20))
    )
    assert all(reply.data.status == "succeeded" for reply in replies)
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_cancelling_one_waiter_keeps_other_waiter_fetch_alive(transport):
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        started.set()
        try:
            await release.wait()
            return deepcopy(DONE)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    transport.side_effect = fetch
    first = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=5))
    second = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=5))
    await started.wait()
    await asyncio.sleep(0.02)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not cancelled.is_set()
    release.set()
    assert (await second).data.status == "succeeded"
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_short_wait_timeout_does_not_cancel_long_waiter(transport):
    async def fetch(*args, **kwargs):
        await asyncio.sleep(0.15)
        return deepcopy(DONE)

    transport.side_effect = fetch
    short, long = await asyncio.gather(
        jobs.get_test_job(context(), "job", wait_timeout=0.04),
        jobs.get_test_job(context(), "job", wait_timeout=1),
    )
    assert short.success is False and "wait_timeout" in short.error
    assert long.success and long.data.status == "succeeded"
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_user_instance_job_and_detail_flags_are_isolated(transport, monkeypatch):
    monkeypatch.setattr(jobs.config, "http_remote_hosted", True)
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(side_effect=lambda ctx: ctx.instance)
    )

    async def fetch(*args, **kwargs):
        await asyncio.sleep(0.02)
        return deepcopy(DONE)

    transport.side_effect = fetch
    owner_a = SimpleNamespace(instance="Game@one", get_state=AsyncMock(return_value="a"))
    owner_b = SimpleNamespace(instance="Game@one", get_state=AsyncMock(return_value="b"))
    editor_b = SimpleNamespace(instance="Game@two", get_state=AsyncMock(return_value="a"))
    await asyncio.gather(
        jobs.get_test_job(owner_a, "job", wait_timeout=1),
        jobs.get_test_job(owner_a, "job", wait_timeout=1),
        jobs.get_test_job(owner_b, "job", wait_timeout=1),
        jobs.get_test_job(editor_b, "job", wait_timeout=1),
        jobs.get_test_job(owner_a, "other", wait_timeout=1),
        jobs.get_test_job(owner_a, "job", include_details=True, wait_timeout=1),
        jobs.get_test_job(owner_a, "job", include_failed_tests=True, wait_timeout=1),
    )
    assert transport.await_count == 6


@pytest.mark.asyncio
async def test_completed_results_are_not_retained_between_wait_sessions(transport):
    assert (await jobs.get_test_job(context(), "job", wait_timeout=1)).data.status == "succeeded"
    transport.return_value = deepcopy(RUNNING)
    assert (await jobs.get_test_job(context(), "job", wait_timeout=0.04)).data.status == "running"
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_shared_response_mutations_are_private_to_each_caller(transport, monkeypatch):
    async def fetch(*args, **kwargs):
        await asyncio.sleep(0.02)
        return deepcopy(DONE)

    async def observe(instance, user_id, job_id, data, **kwargs):
        assert "error" not in data
        data["error"] = "caller observation"

    transport.side_effect = fetch
    monkeypatch.setattr(jobs, "_update_job_nudge", observe)
    await asyncio.gather(*(jobs.get_test_job(context(), "job", wait_timeout=1) for _ in range(5)))
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_staggered_waiter_reuses_running_snapshot_with_same_observation_order(
    transport, monkeypatch
):
    # Given: polling deadlines and snapshot freshness use one controlled clock.
    received = asyncio.Event()
    loop = asyncio.get_running_loop()
    now = loop.time()
    sleeping = {}
    parked = asyncio.Queue()

    async def controlled_sleep(delay):
        future = loop.create_future()
        task = asyncio.current_task()
        sleeping[task] = (future, now + delay)
        parked.put_nowait(task)
        await future

    monkeypatch.setattr(loop, "time", lambda: now)
    monkeypatch.setattr(jobs.asyncio, "sleep", controlled_sleep)

    async def fetch(*args, **kwargs):
        received.set()
        return deepcopy(RUNNING)

    transport.side_effect = fetch
    first = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=0.2))
    second = None
    try:
        await received.wait()
        assert await parked.get() is first
        # When: a staggered second waiter observes the same still-fresh snapshot.
        now += 0.02
        second = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=0.1))
        assert await parked.get() is second
        now = sleeping[second][1]
        sleeping[second][0].set_result(None)
        assert (await second).data.status == "running"
        now = sleeping[first][1]
        sleeping[first][0].set_result(None)
        assert (await first).data.status == "running"
    finally:
        callers = [first, second] if second is not None else [first]
        for caller in callers:
            caller.cancel()
        await asyncio.gather(*callers, return_exceptions=True)
    # Then: each waiter observes once, with exactly one backend observation.
    assert transport.await_count == 1
    observations = [
        call.kwargs["observation_order"] for call in jobs._update_job_nudge.call_args_list
    ]
    assert len(observations) == 2 and len(set(observations)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_identity", ["instance", "owner"])
async def test_unknown_selection_or_remote_owner_keeps_reads_private(
    transport, monkeypatch, missing_identity
):
    if missing_identity == "instance":
        monkeypatch.setattr(jobs, "get_unity_instance_from_context", AsyncMock(return_value=None))
    else:
        monkeypatch.setattr(jobs.config, "http_remote_hosted", True)

    async def fetch(*args, **kwargs):
        await asyncio.sleep(0.02)
        return deepcopy(DONE)

    transport.side_effect = fetch
    ctx = SimpleNamespace(get_state=AsyncMock(return_value=None))
    await asyncio.gather(*(jobs.get_test_job(ctx, "job", wait_timeout=1) for _ in range(2)))
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_replacement_http_session_does_not_join_previous_job_snapshot(transport, monkeypatch):
    monkeypatch.setattr(jobs.config, "http_remote_hosted", True)
    monkeypatch.setattr(jobs.config, "transport_mode", "http")
    received = asyncio.Event()
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(len(calls))
        if len(calls) == 1:
            received.set()
            return deepcopy(RUNNING)
        return {"success": False, "error": "Job not found in replacement editor"}

    def context(session):
        return SimpleNamespace(
            get_state=AsyncMock(
                side_effect=lambda key: {
                    "user_id": "owned-user",
                    "unity_session_id": session,
                }.get(key)
            )
        )

    transport.side_effect = fetch
    old = asyncio.create_task(jobs.get_test_job(context("old-session"), "job", wait_timeout=0.2))
    await received.wait()
    fresh = await jobs.get_test_job(context("new-session"), "job", wait_timeout=0.05)
    assert fresh.success is False and "replacement" in fresh.error
    assert (await old).data.status == "running"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_unknown_http_session_keeps_waits_private(transport, monkeypatch):
    monkeypatch.setattr(jobs.config, "transport_mode", "http")

    async def fetch(*args, **kwargs):
        await asyncio.sleep(0.02)
        return deepcopy(DONE)

    transport.side_effect = fetch
    ctx = SimpleNamespace(get_state=AsyncMock(return_value=None))
    await asyncio.gather(*(jobs.get_test_job(ctx, "job", wait_timeout=1) for _ in range(2)))
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_copy_capacity_returns_bounded_error_without_transport_retry(transport, monkeypatch):
    from services.tools.shared_tool_reads import SharedRead, SharedReadCapacityError

    monkeypatch.setattr(SharedRead, "fetch", AsyncMock(side_effect=SharedReadCapacityError()))
    reply = await jobs.get_test_job(context(), "job", wait_timeout=1)
    assert reply.success is False and reply.data == {"reason": "result_capacity"}
    transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_stdio_replacement_alias_never_joins_old_job_snapshot(transport, monkeypatch):
    monkeypatch.setattr(jobs.config, "transport_mode", "stdio")
    entered = asyncio.Event()

    async def fetch(*args, **kwargs):
        if transport.await_count == 1:
            entered.set()
            return deepcopy(RUNNING)
        return {"success": False, "error": "Job not found after stdio reconnect"}

    transport.side_effect = fetch
    old = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=0.2))
    await entered.wait()
    fresh = await jobs.get_test_job(context(), "job", wait_timeout=0.05)
    assert fresh.success is False and "reconnect" in fresh.error
    assert (await old).data.status == "running"
    assert transport.await_count == 2
