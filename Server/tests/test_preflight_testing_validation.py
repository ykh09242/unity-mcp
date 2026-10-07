"""Invalid test requests and failed mutations must not poll Editor readiness."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models import MCPResponse
from services.resources import editor_state
from services.tools import preflight, refresh_unity, run_tests


@pytest.fixture
def editor_boundary(monkeypatch):
    state = AsyncMock(
        return_value={
            "success": True,
            "data": {"advice": {"ready_for_tools": True}},
        }
    )
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {"job_id": "fixture-job", "status": "queued"},
        }
    )
    monkeypatch.setattr(preflight, "_in_pytest", lambda: False)
    monkeypatch.setattr(refresh_unity, "_in_pytest", lambda: False)
    monkeypatch.setattr(editor_state, "get_editor_state_authoritative", state)
    monkeypatch.setattr(run_tests.unity_transport, "send_with_unity_instance", send)
    ctx = SimpleNamespace(get_state=AsyncMock(return_value="Selected@fixture"))
    return ctx, state, send


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, 2**31])
async def test_invalid_initialization_timeout_rejected_before_readiness(editor_boundary, timeout):
    ctx, state, send = editor_boundary
    response = await run_tests.run_tests(ctx, init_timeout=timeout)
    assert response.success is False
    assert "init_timeout" in response.error
    state.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 1, 2**31 - 1])
async def test_valid_initialization_timeout_still_checks_readiness(editor_boundary, timeout):
    ctx, state, send = editor_boundary
    response = await run_tests.run_tests(ctx, init_timeout=timeout)
    assert response.success is True
    state.assert_awaited_once_with(ctx)
    params = send.await_args.args[3]
    assert params == (
        {"mode": "EditMode"}
        if timeout is None
        else {
            "mode": "EditMode",
            "initTimeout": timeout,
        }
    )


@pytest.mark.asyncio
async def test_clear_stuck_ignores_inapplicable_timeout_and_skips_readiness(editor_boundary):
    ctx, state, send = editor_boundary
    response = await run_tests.run_tests(ctx, clear_stuck=True, init_timeout=0)
    assert response.success is True
    state.assert_not_awaited()
    assert send.await_args.args[3] == {"clear_stuck": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("model_response", [False, True])
async def test_rejected_mutation_does_not_query_editor_state(editor_boundary, model_response):
    ctx, state, send = editor_boundary
    rejected = {"success": False, "error": "Invalid script edit parameters"}
    if model_response:
        rejected = MCPResponse(**rejected)
    send.return_value = rejected
    response = await refresh_unity.send_mutation(ctx, "Selected@fixture", "manage_script", {})
    assert response is rejected
    state.assert_not_awaited()
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_mutation_still_waits_for_readiness(editor_boundary):
    ctx, state, send = editor_boundary
    state.return_value = {"success": True, "data": {"advice": {"ready_for_tools": True}}}
    response = await refresh_unity.send_mutation(ctx, "Selected@fixture", "manage_script", {})
    assert response is send.return_value
    state.assert_awaited_once_with(ctx)


@pytest.mark.asyncio
async def test_reload_rejection_then_invalid_mutation_skips_second_wait(editor_boundary):
    ctx, state, send = editor_boundary
    state.return_value = {"success": True, "data": {"advice": {"ready_for_tools": True}}}
    rejected = {"success": False, "error": "Invalid script edit parameters"}
    send.side_effect = [
        {"success": False, "hint": "retry", "data": {"reason": "reloading"}},
        rejected,
    ]
    response = await refresh_unity.send_mutation(ctx, "Selected@fixture", "manage_script", {})
    assert response is rejected
    state.assert_awaited_once_with(ctx)
    assert send.await_count == 2


@pytest.mark.asyncio
async def test_disconnect_still_recovers_and_verifies_mutation(editor_boundary):
    ctx, state, send = editor_boundary
    state.return_value = {"success": True, "data": {"advice": {"ready_for_tools": True}}}
    send.return_value = {"success": False, "error": "Connection closed after sending"}
    verified = {"success": True, "message": "Mutation verified"}
    verify = AsyncMock(return_value=verified)
    response = await refresh_unity.send_mutation(
        ctx,
        "Selected@fixture",
        "manage_script",
        {},
        verify_after_disconnect=verify,
    )
    assert response is verified
    verify.assert_awaited_once_with()
    state.assert_awaited_once_with(ctx)
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_response", [False, True])
@pytest.mark.parametrize("error", ["Timeout receiving Unity response", "Unity command timed out"])
async def test_ambiguous_timeout_still_waits_for_readiness(editor_boundary, model_response, error):
    ctx, state, send = editor_boundary
    timed_out = {"success": False, "error": error}
    if model_response:
        timed_out = MCPResponse(**timed_out)
    send.return_value = timed_out
    response = await refresh_unity.send_mutation(ctx, "Selected@fixture", "manage_script", {})
    assert response is timed_out
    state.assert_awaited_once_with(ctx)
    send.assert_awaited_once()
