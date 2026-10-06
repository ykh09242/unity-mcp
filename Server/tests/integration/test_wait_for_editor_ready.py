import asyncio
import os
import pytest

from services.tools.refresh_unity import is_reloading_rejection
from .test_helpers import DummyContext


@pytest.mark.asyncio
async def test_returns_immediately_in_pytest(monkeypatch):
    """_in_pytest() detects PYTEST_CURRENT_TEST and returns (True, 0.0) immediately."""
    # PYTEST_CURRENT_TEST is set by pytest automatically, so this should short-circuit.
    from services.tools.refresh_unity import wait_for_editor_ready

    ctx = DummyContext()
    ready, elapsed = await wait_for_editor_ready(ctx, timeout_s=5.0)
    assert ready is True
    assert elapsed == 0.0


@pytest.mark.asyncio
async def test_polls_until_ready(monkeypatch):
    """The helper polls authoritative editor state until ready_for_tools."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from services.tools import refresh_unity as mod

    call_count = 0

    async def fake_get_editor_state(ctx):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return {"data": {"advice": {"ready_for_tools": False, "blocking_reasons": ["compiling"]}}}
        return {"data": {"advice": {"ready_for_tools": True, "blocking_reasons": []}}}

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", fake_get_editor_state)

    ctx = DummyContext()
    ready, elapsed = await mod.wait_for_editor_ready(ctx, timeout_s=10.0)
    assert ready is True
    assert call_count >= 3
    assert elapsed > 0


@pytest.mark.asyncio
async def test_timeout_returns_false(monkeypatch):
    """When editor never becomes ready, returns (False, ~timeout)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from services.tools import refresh_unity as mod

    async def fake_get_editor_state(ctx):
        return {"data": {"advice": {"ready_for_tools": False, "blocking_reasons": ["compiling"]}}}

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", fake_get_editor_state)

    ctx = DummyContext()
    ready, elapsed = await mod.wait_for_editor_ready(ctx, timeout_s=0.6)
    assert ready is False
    assert elapsed >= 0.5


@pytest.mark.asyncio
async def test_stale_only_treated_as_ready(monkeypatch):
    """If the only blocking reason is stale_status, consider ready."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from services.tools import refresh_unity as mod

    async def fake_get_editor_state(ctx):
        return {"data": {"advice": {"ready_for_tools": False, "blocking_reasons": ["stale_status"]}}}

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", fake_get_editor_state)

    ctx = DummyContext()
    ready, elapsed = await mod.wait_for_editor_ready(ctx, timeout_s=5.0)
    assert ready is True


@pytest.mark.asyncio
async def test_blocked_state_request_is_cancelled_at_readiness_timeout(monkeypatch):
    # Given: the state request blocks indefinitely but cooperates with cancellation.
    from services.tools import refresh_unity as mod
    import asyncio
    monkeypatch.setattr(mod, "_in_pytest", lambda: False)
    cancelled = []

    async def blocked_state(ctx):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", blocked_state)
    # When: readiness is allowed twenty milliseconds, with a separate test watchdog.
    ready, elapsed = await asyncio.wait_for(
        mod.wait_for_editor_ready(DummyContext(), timeout_s=0.02), timeout=1,
    )
    # Then: the bounded wait returns its normal result and cleans up the request.
    assert ready is False
    assert elapsed >= 0.02
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_state_ready_after_deadline_is_not_accepted(monkeypatch):
    # Given: a state request completes with readiness after the caller's deadline.
    from services.tools import refresh_unity as mod
    from types import SimpleNamespace
    monkeypatch.setattr(mod, "_in_pytest", lambda: False)
    now = [0.0]
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def delayed_state(ctx):
        now[0] = 2.0
        return {"data": {"advice": {"ready_for_tools": True}}}

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", delayed_state)
    # When: readiness is requested with a one-second deadline.
    ready, elapsed = await mod.wait_for_editor_ready(DummyContext(), timeout_s=1)
    # Then: a late response cannot turn an expired wait into success.
    assert ready is False
    assert elapsed == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [-1, 0])
async def test_nonpositive_readiness_timeout_does_not_poll(monkeypatch, timeout):
    # Given: the caller's wait budget is already exhausted.
    from services.tools import refresh_unity as mod
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    monkeypatch.setattr(mod, "_in_pytest", lambda: False)
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: 0.0))
    state = AsyncMock()
    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", state)
    # When: readiness is requested with a nonpositive timeout.
    result = await mod.wait_for_editor_ready(DummyContext(), timeout_s=timeout)
    # Then: preserve the immediate timeout result without dispatching state I/O.
    assert result == (False, 0.0)
    state.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_caller_cancellation_reaches_state_request(monkeypatch):
    # Given: a readiness request is awaiting the editor state.
    from services.tools import refresh_unity as mod
    import asyncio
    monkeypatch.setattr(mod, "_in_pytest", lambda: False)
    entered = asyncio.Event()
    cancelled = []

    async def blocked_state(ctx):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", blocked_state)
    task = asyncio.create_task(mod.wait_for_editor_ready(DummyContext(), timeout_s=30))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        # When: the caller cancels its own readiness request.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Then: cancellation propagates through the in-flight state request.
        assert cancelled == [True]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_exception_during_poll_keeps_trying(monkeypatch):
    """If authoritative state throws, the helper keeps polling until ready."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from services.tools import refresh_unity as mod

    call_count = 0

    async def fake_get_editor_state(ctx):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Unity disconnected")
        return {"data": {"advice": {"ready_for_tools": True, "blocking_reasons": []}}}

    monkeypatch.setattr(mod.editor_state, "get_editor_state_authoritative", fake_get_editor_state)

    ctx = DummyContext()
    ready, elapsed = await mod.wait_for_editor_ready(ctx, timeout_s=10.0)
    assert ready is True
    assert call_count >= 3


def test_is_reloading_rejection_true():
    """Detects a reloading rejection response."""
    resp = {"success": False, "error": "Unity is reloading", "data": {"reason": "reloading"}, "hint": "retry"}
    assert is_reloading_rejection(resp) is True


def test_is_reloading_rejection_false_on_success():
    assert is_reloading_rejection({"success": True, "data": {"reason": "reloading"}, "hint": "retry"}) is False


def test_is_reloading_rejection_false_on_other_error():
    assert is_reloading_rejection({"success": False, "error": "timeout", "data": {}, "hint": "retry"}) is False


def test_is_reloading_rejection_false_on_non_dict():
    assert is_reloading_rejection("some string") is False
    assert is_reloading_rejection(None) is False


@pytest.mark.parametrize(
    "success,data,hint,expected",
    [
        (False, {"reason": "reloading"}, "retry", True),
        (True, {"reason": "reloading"}, "retry", False),
        (False, {"reason": "stale_connection"}, "retry", False),
        (False, {"reason": "reloading"}, None, False),
        (False, None, "retry", False),
        (False, [{"reason": "reloading"}], "retry", False),
    ],
)
def test_model_reloading_rejection_requires_exact_no_execution_marker(
    success, data, hint, expected,
):
    from models import MCPResponse

    response = MCPResponse(success=success, data=data, hint=hint)
    assert is_reloading_rejection(response) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("response_type", ["dict", "model"])
@pytest.mark.parametrize("second_rejected", [False, True])
async def test_reload_recovery_retries_once_and_preserves_last_response(
    monkeypatch, response_type, second_rejected,
):
    from unittest.mock import AsyncMock
    from models import MCPResponse
    from services.tools import refresh_unity as mod

    refused = {
        "success": False, "error": "Unity is reloading; please retry",
        "hint": "retry", "data": {"reason": "reloading", "retry_after_ms": 500},
    }
    first = MCPResponse(**refused) if response_type == "model" else refused
    second = first if second_rejected else {"success": True, "data": {"created": "Canvas"}}
    send = AsyncMock(side_effect=[first, second])
    ready = AsyncMock(return_value=(True, 0.0))
    verify = AsyncMock()
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(mod, "wait_for_editor_ready", ready)

    result = await mod.send_mutation(
        DummyContext(), "Safe@sixsafe", "manage_ugui", {"action": "create"},
        verify_after_disconnect=verify,
    )

    assert result is second
    assert send.await_count == 2
    assert ready.await_count == 2
    assert all(call.kwargs == {"retry_on_reload": False} for call in send.await_args_list)
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_ordinary_error_returns_without_replay_or_readiness_wait(monkeypatch):
    from unittest.mock import AsyncMock
    from models import MCPResponse
    from services.tools import refresh_unity as mod

    response = MCPResponse(
        success=False, error="Unity connection unavailable", hint="retry",
        data={"reason": "stale_connection", "retry_after_ms": 500},
    )
    send = AsyncMock(return_value=response)
    ready = AsyncMock(return_value=(True, 0.0))
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(mod, "wait_for_editor_ready", ready)

    result = await mod.send_mutation(
        DummyContext(), "Safe@sixsafe", "manage_ugui", {"action": "create"},
    )

    assert result is response
    send.assert_awaited_once()
    ready.assert_not_awaited()


# --- is_connection_lost_after_send tests ---

from services.tools.refresh_unity import is_connection_lost_after_send


def test_connection_lost_on_connection_closed():
    resp = {"success": False, "error": "Connection closed before reading expected bytes"}
    assert is_connection_lost_after_send(resp) is True


def test_connection_lost_on_disconnected():
    resp = {"success": False, "error": "Unity disconnected"}
    assert is_connection_lost_after_send(resp) is True


def test_connection_lost_on_aborted():
    resp = {"success": False, "error": "Connection aborted"}
    assert is_connection_lost_after_send(resp) is True


def test_connection_lost_false_on_success():
    resp = {"success": True, "error": "Connection closed before reading expected bytes"}
    assert is_connection_lost_after_send(resp) is False


def test_connection_lost_false_on_other_error():
    resp = {"success": False, "error": "timeout"}
    assert is_connection_lost_after_send(resp) is False


def test_connection_lost_false_on_non_dict():
    assert is_connection_lost_after_send("some string") is False
    assert is_connection_lost_after_send(None) is False


# --- send_mutation tests ---

from services.tools.refresh_unity import send_mutation


@pytest.mark.asyncio
async def test_send_mutation_returns_success_directly(monkeypatch):
    """Normal success response is returned as-is."""
    from services.tools import refresh_unity as mod

    async def fake_send(*args, **kwargs):
        return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", fake_send)

    ctx = DummyContext()
    resp = await send_mutation(ctx, None, "manage_script", {"action": "create"})
    assert resp == {"success": True, "data": {"ok": True}}


@pytest.mark.asyncio
async def test_send_mutation_retries_on_reloading_rejection(monkeypatch):
    """Reloading rejection triggers one retry after wait."""
    from services.tools import refresh_unity as mod

    call_count = 0

    async def fake_send(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"success": False, "data": {"reason": "reloading"}, "hint": "retry"}
        return {"success": True, "data": {"retried": True}}

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", fake_send)

    ctx = DummyContext()
    resp = await send_mutation(ctx, None, "manage_script", {"action": "create"})
    assert resp.get("success") is True
    assert call_count == 2


@pytest.mark.asyncio
async def test_send_mutation_calls_verify_on_connection_lost(monkeypatch):
    """Connection lost triggers verify callback."""
    from services.tools import refresh_unity as mod

    async def fake_send(*args, **kwargs):
        return {"success": False, "error": "Connection closed before reading expected bytes"}

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", fake_send)

    verify_called = False

    async def fake_verify():
        nonlocal verify_called
        verify_called = True
        return {"success": True, "message": "Verified!"}

    ctx = DummyContext()
    resp = await send_mutation(ctx, None, "manage_script", {}, verify_after_disconnect=fake_verify)
    assert verify_called
    assert resp == {"success": True, "message": "Verified!"}


@pytest.mark.asyncio
async def test_send_mutation_keeps_error_when_verify_returns_none(monkeypatch):
    """When verify callback returns None, original error is preserved."""
    from services.tools import refresh_unity as mod

    async def fake_send(*args, **kwargs):
        return {"success": False, "error": "Connection closed before reading expected bytes"}

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", fake_send)

    async def fake_verify():
        return None

    ctx = DummyContext()
    resp = await send_mutation(ctx, None, "manage_script", {}, verify_after_disconnect=fake_verify)
    assert resp.get("success") is False


@pytest.mark.asyncio
async def test_send_mutation_returns_selection_failure_without_readiness_wait(monkeypatch):
    # Given: selection failed before dispatch, so no Unity instance can become ready.
    from services.tools import refresh_unity as mod
    from unittest.mock import AsyncMock

    response = {
        "success": False,
        "error": "Multiple Unity instances are connected.",
        "hint": "select_instance",
        "data": {"reason": "instance_selection_required", "available_instances": ["A", "B"]},
    }
    send = AsyncMock(return_value=response)
    ready = AsyncMock(return_value=(False, 0.0))
    verify = AsyncMock()
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(mod, "wait_for_editor_ready", ready)
    # When
    result = await send_mutation(
        DummyContext(), None, "manage_ugui", {"action": "create"},
        verify_after_disconnect=verify,
    )
    # Then: return the exact payload after one attempt, without recovery or mutation replay.
    assert result is response
    assert send.await_count == 1
    assert send.await_args.kwargs["retry_on_reload"] is False
    ready.assert_not_awaited()
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_mutation_keeps_initial_reload_wait_when_retry_requires_selection(monkeypatch):
    # Given: a safe reload rejection is followed by a selection refusal before replay.
    from services.tools import refresh_unity as mod
    from unittest.mock import AsyncMock

    response = {
        "success": False,
        "hint": "select_instance",
        "data": {"reason": "instance_selection_required", "available_instances": ["A", "B"]},
    }
    send = AsyncMock(side_effect=[
        {"success": False, "hint": "retry", "data": {"reason": "reloading"}},
        response,
    ])
    ready = AsyncMock(return_value=(True, 0.0))
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(mod, "wait_for_editor_ready", ready)
    # When
    ctx = DummyContext()
    result = await send_mutation(ctx, None, "manage_ugui", {"action": "create"})
    # Then: retain the first recovery wait, but no further wait or third dispatch.
    assert result is response
    assert send.await_count == 2
    assert all(call.kwargs["retry_on_reload"] is False for call in send.await_args_list)
    ready.assert_awaited_once_with(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,should_wait",
    [
        ({"success": True}, True),
        ({"success": False, "error": "busy", "hint": "retry", "data": {"reason": "tests_running"}}, False),
        ({"success": False, "error": "timeout", "hint": "retry"}, True),
        ({"success": False, "message": "Timed out waiting for response"}, True),
        ({"success": False, "error": "Connection closed"}, True),
        ({"success": False, "hint": "retry", "data": {"reason": "instance_selection_required"}}, False),
        ({"success": False, "hint": "select_instance"}, False),
        ({"success": False, "hint": "select_instance", "data": {"reason": "other"}}, False),
        ({"success": True, "hint": "select_instance", "data": {"reason": "instance_selection_required"}}, True),
    ],
)
async def test_send_mutation_waits_only_when_completion_may_be_pending(monkeypatch, response, should_wait):
    # Given: definitive errors need no readiness poll; successful or uncertain mutations do.
    from services.tools import refresh_unity as mod
    from unittest.mock import AsyncMock

    send = AsyncMock(return_value=response)
    ready = AsyncMock(return_value=(True, 0.0))
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(mod, "wait_for_editor_ready", ready)
    # When
    ctx = DummyContext()
    result = await send_mutation(ctx, None, "manage_ugui", {"action": "create"})
    # Then: preserve the response without replay and wait only when recovery may be needed.
    assert result is response
    assert send.await_count == 1
    if should_wait:
        ready.assert_awaited_once_with(ctx)
    else:
        ready.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("initially_ready", [True, False])
async def test_send_mutation_reuses_confirmed_readiness_after_readonly_verification(monkeypatch, initially_ready):
    # Given: a connection loss may follow a successful mutation and still needs verification.
    from services.tools import refresh_unity as mod
    from unittest.mock import AsyncMock

    response = {"success": True, "message": "Verified!"}
    send = AsyncMock(return_value={"success": False, "error": "Connection closed"})
    ready = AsyncMock(side_effect=[(initially_ready, 0.0), (True, 0.0)])
    verify = AsyncMock(return_value=response)
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(mod, "wait_for_editor_ready", ready)
    # When
    result = await send_mutation(
        DummyContext(), None, "manage_script", {}, verify_after_disconnect=verify,
    )
    # A confirmed ready snapshot and a successful read verification need no extra RPC.
    # If the first readiness wait expired, retain the final readiness check.
    assert result is response
    assert send.await_count == 1
    assert ready.await_count == (1 if initially_ready else 2)
    verify.assert_awaited_once_with()
