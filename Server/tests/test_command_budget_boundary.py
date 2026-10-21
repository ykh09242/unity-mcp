"""Characterize the request clock and detached evidence before moving their owner."""

import asyncio
import dataclasses
import pytest

from transport import plugin_hub as budgets


@pytest.mark.asyncio
async def test_later_poll_retains_prior_mutation_outcome_and_original_clock():
    now = asyncio.get_running_loop().time()
    async with asyncio.timeout_at(now + 100) as scope:
        request = budgets._HttpRequestBudget(now, budgets._CommandBudget(now + 100), scope)
        mutation = request.command_budget("apply", 100)
        mutation.dispatched = True
        mutation.command_id = "mutation"
        mutation.session_id = "session"
        mutation.phase = "response"
        request.command_budget("poll", 1)
        reply = request.budget.timeout_response("poll")
        assert reply["data"] == {
            "reason": "outcome_unknown",
            "command": "apply",
            "phase": "response",
            "session_id": "session",
            "command_id": "mutation",
            "cause": "timeout",
        }
        assert request.budget.expires_at == now + 100
        assert request.budget.prior_dispatch is mutation


@pytest.mark.asyncio
async def test_snapshot_copies_scalars_and_detached_scope_restores_after_cancellation():
    now = asyncio.get_running_loop().time()
    original = budgets._HttpRequestBudget(
        now, budgets._CommandBudget(now + 100), asyncio.timeout(None)
    )
    token = budgets.http_request_budget.set(original)
    try:
        snapshot = budgets.capture_http_request_budget()
        original.budget.phase = "changed_after_capture"
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout_at(snapshot.expires_at) as scope:
                with budgets.detached_http_request_budget(snapshot, scope) as owned:
                    assert budgets.http_request_budget.get() is owned
                    assert owned is not original and owned.budget is not original.budget
                    assert owned.budget.phase == "session_selection"
                    budgets.extend_http_request_deadline(10000)
                    assert owned.budget.expires_at == snapshot.expires_at
                    assert scope.when() == snapshot.expires_at
                    raise asyncio.CancelledError
        assert budgets.http_request_budget.get() is original
        assert all(
            not isinstance(getattr(snapshot, f.name), budgets._CommandBudget)
            for f in dataclasses.fields(snapshot)
        )
    finally:
        budgets.http_request_budget.reset(token)


@pytest.mark.asyncio
async def test_context_is_task_local_and_restored_without_a_request():
    assert budgets.capture_http_request_budget() is None
    entered, release = asyncio.Event(), asyncio.Event()

    async def child():
        now = asyncio.get_running_loop().time()
        request = budgets._HttpRequestBudget(
            now, budgets._CommandBudget(now + 100), asyncio.timeout(None)
        )
        token = budgets.http_request_budget.set(request)
        try:
            entered.set()
            await release.wait()
            assert budgets.http_request_budget.get() is request
        finally:
            budgets.http_request_budget.reset(token)

    task = asyncio.create_task(child())
    try:
        await entered.wait()
        assert budgets.capture_http_request_budget() is None
        budgets.extend_http_request_deadline(10000)
    finally:
        release.set()
        await task
    assert budgets.capture_http_request_budget() is None


def test_facade_and_consumers_share_one_context_and_types():
    from transport import command_budget
    from transport import test_wait_binding, unity_transport
    from services.tools import refresh_unity, script_apply_edits
    from services import custom_tool_service

    for name in (
        "_CommandBudget",
        "_HttpRequestBudget",
        "CommandEvidenceSnapshot",
        "HttpBudgetSnapshot",
        "http_request_budget",
        "command_outcome_unknown",
        "capture_http_request_budget",
        "detached_http_request_budget",
        "extend_http_request_deadline",
    ):
        assert getattr(budgets, name) is getattr(command_budget, name)
    assert (
        test_wait_binding.capture_http_request_budget is command_budget.capture_http_request_budget
    )
    assert (
        test_wait_binding.detached_http_request_budget
        is command_budget.detached_http_request_budget
    )
    assert (
        script_apply_edits.capture_http_request_budget is command_budget.capture_http_request_budget
    )
    assert refresh_unity.extend_http_request_deadline is command_budget.extend_http_request_deadline
    assert (
        custom_tool_service.extend_http_request_deadline
        is command_budget.extend_http_request_deadline
    )
    assert unity_transport.command_outcome_unknown is command_budget.command_outcome_unknown
