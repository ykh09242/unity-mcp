"""Concurrent long waits share Unity reads without sharing caller lifetimes."""

import asyncio
import importlib
import time
from copy import deepcopy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.timing import await_completion, await_signal, watchdog_timeout

from services.api_key_service import ApiKeyService, ValidationResult
from services.credential_lease import CredentialLeaseError

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


@asynccontextmanager
async def remote_callers(monkeypatch, *principals):
    """Authenticate only the explicitly remote scenarios with real service leases."""
    monkeypatch.setattr(ApiKeyService, "_instance", None)
    service = ApiKeyService("https://fixture.invalid/validate")
    allowed = {f"requester:{principal}": principal for principal in principals}

    async def validate(key):
        return ValidationResult(valid=key in allowed, user_id=allowed.get(key))

    monkeypatch.setattr(service, "_validate_external", validate)
    try:
        leases = {}
        for key, principal in allowed.items():
            lease = (await service.validate(key)).lease
            assert lease is not None and lease.user_id == principal
            leases[principal] = lease

        async def call(principal, ctx, job_id="job", **kwargs):
            async with leases[principal].own():
                return await jobs.get_test_job(ctx, job_id, **kwargs)

        yield call
    finally:
        await service.aclose()


def context():
    return SimpleNamespace(
        get_state=AsyncMock(
            side_effect=lambda key: {
                "unity_session_id": "owned-session",
            }.get(key)
        )
    )


@pytest.mark.asyncio
async def test_paged_wait_returns_completed_page_after_running_status(transport):
    page = {"items": [{"fullName": "Suite.Failed", "state": "Failed"}], "next_cursor": None}
    transport.side_effect = [
        deepcopy(RUNNING),
        {"success": True, "data": {"job_id": "job", "status": "failed", "result_page": page}},
    ]
    response = await jobs.get_test_job(
        context(), "job", include_failed_tests=True, page_size=12, wait_timeout=5
    )
    assert response.success and response.data.status == "failed"
    assert response.data.result_page == page
    assert transport.await_count == 2
    assert all(
        call.args[3] == {"job_id": "job", "includeFailedTests": True, "page_size": 12}
        for call in transport.await_args_list
    )


@pytest.mark.asyncio
async def test_paged_waits_share_only_identical_page_selection(transport):
    async def fetch(_sender, _instance, _command, params, **kwargs):
        await asyncio.sleep(0.02)
        return {
            "success": True,
            "data": {
                "job_id": "job",
                "status": "succeeded",
                "result_page": {"selection": [params.get("page_size"), params.get("cursor")]},
            },
        }

    transport.side_effect = fetch
    selections = [(12, None), (12, None), (24, None), (12, "next"), (None, None)]
    replies = await asyncio.gather(
        *(
            jobs.get_test_job(
                context(),
                "job",
                include_details=True,
                page_size=size,
                cursor=cursor,
                wait_timeout=1,
            )
            for size, cursor in selections
        )
    )
    assert [reply.data.result_page["selection"] for reply in replies] == [
        list(x) for x in selections
    ]
    assert transport.await_count == 4


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
    try:
        await await_signal(started, "poll started", producers=(first, second))
        await asyncio.sleep(0.02)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not cancelled.is_set()
        release.set()
        assert (await second).data.status == "succeeded"
        assert transport.await_count == 1
    finally:
        release.set()
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


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
    async with remote_callers(monkeypatch, "a", "b") as call:
        await asyncio.gather(
            call("a", owner_a, "job", wait_timeout=1),
            call("a", owner_a, "job", wait_timeout=1),
            call("b", owner_b, "job", wait_timeout=1),
            call("a", editor_b, "job", wait_timeout=1),
            call("a", owner_a, "other", wait_timeout=1),
            call("a", owner_a, "job", include_details=True, wait_timeout=1),
            call("a", owner_a, "job", include_failed_tests=True, wait_timeout=1),
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

    import services.tools.test_job_wait as waits
    import services.tools.shared_tool_reads as sharing

    class ProductLoop:
        def time(self):
            return now

        def __getattr__(self, name):
            return getattr(loop, name)

    product_loop = ProductLoop()

    class ProductAsyncio:
        def get_event_loop(self):
            return product_loop

        def get_running_loop(self):
            return product_loop

        def __getattr__(self, name):
            return getattr(asyncio, name)

    clock = ProductAsyncio()
    clock.sleep = controlled_sleep
    monkeypatch.setattr(waits, "asyncio", clock)
    monkeypatch.setattr(sharing, "asyncio", ProductAsyncio())
    # A missing product phase must still expire while this product clock is frozen.
    with pytest.raises(AssertionError, match="frozen product clock observer"):
        await await_signal(asyncio.Event(), "frozen product clock observer", timeout=0.001)

    async def fetch(*args, **kwargs):
        received.set()
        return deepcopy(RUNNING)

    transport.side_effect = fetch
    first = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=0.2))
    second = None
    try:
        await await_signal(received, "first poll received", producers=(first,))
        parked_first = asyncio.create_task(parked.get())
        try:
            assert (
                await await_completion(parked_first, "first poll parked", producers=(first,))
                is first
            )
        finally:
            parked_first.cancel()
            await asyncio.gather(parked_first, return_exceptions=True)
        # When: a staggered second waiter observes the same still-fresh snapshot.
        now += 0.02
        second = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=0.1))
        parked_second = asyncio.create_task(parked.get())
        try:
            assert (
                await await_completion(parked_second, "second poll parked", producers=(second,))
                is second
            )
        finally:
            parked_second.cancel()
            await asyncio.gather(parked_second, return_exceptions=True)
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
    replies = await asyncio.gather(
        *(jobs.get_test_job(ctx, "job", wait_timeout=1) for _ in range(2)),
        return_exceptions=True,
    )
    if missing_identity == "owner":
        assert all(isinstance(reply, CredentialLeaseError) for reply in replies)
        transport.assert_not_awaited()
    else:
        assert all(reply.data.status == "succeeded" for reply in replies)
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
    async with remote_callers(monkeypatch, "owned-user") as call:
        old = asyncio.create_task(
            call("owned-user", context("old-session"), wait_timeout=watchdog_timeout())
        )
        try:
            await await_signal(received, "old HTTP poll received", producers=(old,))
            fresh = await call(
                "owned-user", context("new-session"), wait_timeout=watchdog_timeout()
            )
            assert fresh.success is False and "replacement" in fresh.error
            old.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old
            assert len(calls) == 2
        finally:
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)


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
    # Admission is not the subject of this identity-isolation regression.
    old = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=watchdog_timeout()))
    try:
        await await_signal(entered, "old stdio poll entered", producers=(old,))
        fresh = await jobs.get_test_job(context(), "job", wait_timeout=watchdog_timeout())
        assert fresh.success is False and "reconnect" in fresh.error
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        assert transport.await_count == 2
    finally:
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
