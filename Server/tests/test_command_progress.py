"""Foreground progress closes independently of native/receipt work.

Source-first contract tests. The HTTP suite can expose missing reporting without
this module's future-helper prerequisite; a missing helper is not RED evidence.
"""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK -- exercise the existing transport's owner loop.
import importlib
import importlib.util
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def progress_api():
    """Resolve the planned API at test time, never fail collection on a new module."""
    assert importlib.util.find_spec("services.command_progress") is not None, (
        "Reporter lifecycle prerequisite missing: implement the approved command_progress "
        "module before classifying these cases as behavioral RED/GREEN."
    )
    return importlib.import_module("services.command_progress")


class RecordingContext:
    """Record public SDK calls; mutation deliberately models a slow async boundary."""

    def __init__(self, token="foreground-token", *, blocked=False, broken=False):
        self.request_context = SimpleNamespace(meta={"progressToken": token})
        self.calls = []
        self.started_at = []
        self.received = asyncio.Queue()
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()
        self.release = asyncio.Event()
        self.blocked = blocked
        self.broken = broken
        self.active = 0
        self.peak_active = 0

    async def report_progress(self, progress, total=None, message=None):
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        self.calls.append((progress, total, message, threading.get_ident()))
        self.started_at.append(asyncio.get_running_loop().time())
        self.received.put_nowait((progress, total, message))
        self.entered.set()
        try:
            if self.broken:
                raise OSError("owned optional notification failed")
            if self.blocked:
                await self.release.wait()
        finally:
            self.active -= 1
            self.exited.set()


async def allowed():
    return True


def source(api, *, guard=allowed, expires_at=None):
    """Use explicit admitted evidence, not a request ContextVar in the send task."""
    return api.ProgressSource(
        transport="http",
        command_id="owned-command",
        session_id="owned-session",
        generation="owned-generation",
        user_id=None,
        expires_at=(asyncio.get_running_loop().time() + 5 if expires_at is None else expires_at),
        guard=guard,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, True, False, "x" * 257, 2**53, -(2**53)])
async def test_ineligible_optional_token_leaves_call_without_reporter(progress_api, token):
    # Given: an absent or out-of-bound optional token, not malformed tool metadata.
    ctx = RecordingContext(token)
    # When: the ordinary foreground scope and producer admission run.
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
    # Then: work remains callable and no reporting resource is admitted.
    assert sink is None
    assert ctx.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [None, SimpleNamespace(request_context=None)])
async def test_absent_sdk_request_context_is_optional_noop(progress_api, ctx):
    # Given/When: a non-SDK caller enters the optional outer scope.
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
    # Then: existing context-free callers can continue normally.
    assert sink is None


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["request-token", 0, -(2**53) + 1])
async def test_bound_counter_reports_on_original_loop_before_foreground_finishes(
    progress_api, token
):
    # Given: a valid request whose actual command has been admitted.
    ctx = RecordingContext(token)
    owner_thread = threading.get_ident()
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
        assert sink is not None
        assert sink.bind(source(progress_api))
        # When: an actual settled-item observation arrives while the request is open.
        assert sink.offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))

        reported = await asyncio.wait_for(ctx.received.get(), 1)
        # Then: the first real count is delivered on its original async owner.
        assert reported[:2] == (1, 2)
        assert ctx.calls[0][3] == owner_thread
    assert ctx.active == 0


@pytest.mark.asyncio
async def test_only_one_producer_claims_a_foreground_request(progress_api):
    # Given/When: settings or another child tries to acquire a second producer.
    ctx = RecordingContext()
    async with progress_api.foreground_progress(ctx):
        first = progress_api.claim_command_progress(total=2)
        second = progress_api.claim_command_progress(total=2)
    # Then: there is one native producer, without a synthetic start notification.
    assert first is not None and second is None
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_scope_exit_rejects_late_offers_and_clears_producer_context(progress_api):
    # Given: an admitted request that completes before another optional update.
    ctx = RecordingContext()
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
        assert sink.bind(source(progress_api))
    # When: old native work attempts another report.
    accepted = sink.offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))
    # Then: completion is a permanent reporter boundary, not work continuation.
    assert not accepted
    assert progress_api.claim_command_progress(total=2) is None
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_slow_optional_send_is_cancelled_and_joined_on_scope_exit(progress_api):
    # Given: public SDK notification I/O that has begun but cannot complete.
    ctx = RecordingContext(blocked=True)

    async def request():
        async with progress_api.foreground_progress(ctx):
            sink = progress_api.claim_command_progress(total=100)
            assert sink.bind(source(progress_api))
            sink.offer(progress_api.CommandProgressUpdate(1, 100, "batch_items"))
            await asyncio.wait_for(ctx.entered.wait(), 1)
            # When: a flood arrives and the public request returns.
            for completed in range(2, 101):
                sink.offer(progress_api.CommandProgressUpdate(completed, 100, "batch_items"))
        return sink

    sink = await asyncio.wait_for(request(), 1)
    # Then: at most one SDK send ran and none survives foreground teardown.
    assert ctx.peak_active == 1
    assert ctx.active == 0 and ctx.exited.is_set()
    assert len(ctx.calls) == 1
    assert not sink.offer(progress_api.CommandProgressUpdate(100, 100, "batch_items"))


@pytest.mark.asyncio
async def test_optional_sdk_failure_does_not_replace_the_command_result(progress_api):
    # Given: notification I/O fails independently of the command.
    ctx = RecordingContext(broken=True)
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
        assert sink.bind(source(progress_api))
        # When: reporting fails before the ordinary handler returns.
        sink.offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))
        await asyncio.wait_for(ctx.exited.wait(), 1)
        result = {"success": True, "data": {"applied": 2}}
    # Then: optional failure has neither become a retry nor a domain failure.
    assert result == {"success": True, "data": {"applied": 2}}
    assert ctx.active == 0


@pytest.mark.asyncio
async def test_thread_offer_marshals_public_sdk_call_to_original_loop(progress_api):
    # Given: the stdio-facing opaque sink is passed to an ordinary worker thread.
    ctx = RecordingContext()
    owner_thread = threading.get_ident()
    worker_threads = []
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
        assert sink.bind(source(progress_api))

        def observe():
            worker_threads.append(threading.get_ident())
            sink.offer_from_thread(progress_api.CommandProgressUpdate(1, 2, "batch_items"))

        # When: a worker offers one actual observation.
        await asyncio.to_thread(observe)
        await asyncio.wait_for(ctx.received.get(), 1)
    # Then: no SDK Context method ran on that worker thread.
    assert worker_threads[0] != owner_thread
    assert [call[3] for call in ctx.calls] == [owner_thread]


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True], ids=["bound-deadline", "bound-auth"])
async def test_outer_reporter_uses_explicit_inner_binding_after_context_reset(progress_api, revoke):
    from transport.plugin_hub import _CommandBudget, _HttpRequestBudget, http_request_budget

    # Given: the reporter task predates the inner routing budget/auth context.
    ctx = RecordingContext()
    checked, release = asyncio.Event(), asyncio.Event()
    authorized = [True]

    async def bound_guard():
        checked.set()
        await release.wait()
        return authorized[0]

    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
        now = asyncio.get_running_loop().time()
        inner = _CommandBudget(now + 30)
        token = http_request_budget.set(
            _HttpRequestBudget(now, inner, asyncio.timeout_at(now + 30))
        )
        try:
            bound = sink.bind(
                source(
                    progress_api,
                    guard=bound_guard,
                    expires_at=now + 30 if revoke else now - 1,
                )
            )
            if revoke:
                assert bound
        finally:
            http_request_budget.reset(token)
        # When: a previously scheduled send sees reset ambient state and revoked
        # captured authority, or an already-expired captured absolute deadline.
        sink.offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))
        if revoke:
            await asyncio.wait_for(checked.wait(), 1)
            authorized[0] = False
        release.set()
        # Scheduler barriers let an already-ready sender run while the request
        # remains open; an immediate scope close would hide a missing fence.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    # Then: reset/absent ambient state must not grant a fresh deadline/authority.
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_receipt_worker_pin_outlives_cancelled_foreground_without_late_progress(
    progress_api, tmp_path
):
    from services.execution_receipts import ExecutionReceiptStore
    from tests.test_execution_receipts import admit

    # Given: actual receipt ownership retains a native worker independently.
    ctx = RecordingContext()
    store = ExecutionReceiptStore(tmp_path / "receipts", private_root=True)
    try:
        invocation = admit(store)
        invocation.enter_handler()
        worker = invocation.acquire_worker()
        settled = False
        ready = asyncio.Queue(maxsize=1)

        async def request():
            async with progress_api.foreground_progress(ctx):
                sink = progress_api.claim_command_progress(total=2)
                assert sink.bind(source(progress_api))
                ready.put_nowait(sink)
                await asyncio.Event().wait()

        request_task = asyncio.create_task(request())
        try:
            sink = await asyncio.wait_for(ready.get(), 1)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
            invocation.finish("unknown", cause="wrapper_cancelled")
            settled = True
            assert store.active_count == 1
            # When: still-owned work offers after the request/report scope closed.
            await asyncio.to_thread(
                sink.offer_from_thread,
                progress_api.CommandProgressUpdate(1, 2, "batch_items"),
            )
            # Then: reporting stops without releasing the worker's receipt/key charge.
            assert ctx.calls == []
            assert store.active_count == 1
        finally:
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
            if not settled:
                invocation.finish("unknown", cause="wrapper_cancelled")
            worker.close()
        assert store.active_count == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_monotonic_counts_reject_duplicate_and_mismatched_total(progress_api):
    # Given: one actual settled item has already been delivered.
    ctx = RecordingContext()
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=2)
        assert sink.bind(source(progress_api))
        sink.offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))
        await asyncio.wait_for(ctx.received.get(), 1)
        # When: a duplicate and an observation for another total precede item two.
        assert not sink.offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))
        assert not sink.offer(progress_api.CommandProgressUpdate(2, 3, "batch_items"))
        assert sink.offer(progress_api.CommandProgressUpdate(2, 2, "batch_items"))
        second = await asyncio.wait_for(ctx.received.get(), 1)
        # Then: invalid observations cannot add/reset or corrupt the real sequence.
        assert second[:2] == (2, 2)
        assert [call[:2] for call in ctx.calls] == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_update_flood_coalesces_to_latest_without_exceeding_send_rate(progress_api):
    # Given: item one is already delivered; later observations share one rate slot.
    ctx = RecordingContext()
    async with progress_api.foreground_progress(ctx):
        sink = progress_api.claim_command_progress(total=100)
        assert sink.bind(source(progress_api))
        sink.offer(progress_api.CommandProgressUpdate(1, 100, "batch_items"))
        await asyncio.wait_for(ctx.received.get(), 1)
        # When: all subsequent settled counts arrive before another event-loop turn.
        for completed in range(2, 101):
            sink.offer(progress_api.CommandProgressUpdate(completed, 100, "batch_items"))
        last = await asyncio.wait_for(ctx.received.get(), 1)
        # Then: bounded coalescing keeps the latest count and enforces the 10Hz cap.
        assert last[:2] == (100, 100)
        assert [call[:2] for call in ctx.calls] == [(1, 100), (100, 100)]
        assert ctx.started_at[1] - ctx.started_at[0] >= 0.099
        assert ctx.peak_active == 1


@pytest.mark.asyncio
async def test_concurrent_requests_keep_opaque_sinks_on_their_original_reporters(progress_api):
    # Given: two distinct foreground contexts overlap on the same event loop.
    contexts = [RecordingContext("first-token"), RecordingContext("second-token")]
    admitted = asyncio.Queue(maxsize=2)
    release = asyncio.Event()

    async def request(index):
        async with progress_api.foreground_progress(contexts[index]):
            sink = progress_api.claim_command_progress(total=2)
            assert sink.bind(source(progress_api))
            admitted.put_nowait((index, sink))
            await release.wait()

    tasks = [asyncio.create_task(request(index)) for index in range(2)]
    try:
        sinks = dict([await asyncio.wait_for(admitted.get(), 1) for _ in range(2)])
        # When: a caller with neither request context offers to both opaque handles.
        sinks[0].offer(progress_api.CommandProgressUpdate(1, 2, "batch_items"))
        sinks[1].offer(progress_api.CommandProgressUpdate(2, 2, "batch_items"))
        first = await asyncio.wait_for(contexts[0].received.get(), 1)
        second = await asyncio.wait_for(contexts[1].received.get(), 1)
        # Then: one request's token/context is never borrowed by the other.
        assert first[:2] == (1, 2)
        assert second[:2] == (2, 2)
        assert len(contexts[0].calls) == len(contexts[1].calls) == 1
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_reporter_capacity_drops_optional_reporting_and_recovers_after_close(progress_api):
    # Given: the fixed process-wide reporter admission budget is occupied.
    admitted = asyncio.Queue(maxsize=257)
    release = asyncio.Event()

    async def hold(index):
        async with progress_api.foreground_progress(RecordingContext(f"owner-{index}")):
            admitted.put_nowait(progress_api.claim_command_progress(total=2))
            await release.wait()

    tasks = [asyncio.create_task(hold(index)) for index in range(256)]
    try:
        for _ in range(256):
            assert await asyncio.wait_for(admitted.get(), 2) is not None
        # When: another otherwise valid foreground request arrives at capacity.
        async with progress_api.foreground_progress(RecordingContext("overflow")):
            overflow = progress_api.claim_command_progress(total=2)
        # Then: it can run without reporting; releasing owners restores admission.
        assert overflow is None
    finally:
        release.set()
        await asyncio.gather(*tasks)
    async with progress_api.foreground_progress(RecordingContext("recovered")):
        assert progress_api.claim_command_progress(total=2) is not None
