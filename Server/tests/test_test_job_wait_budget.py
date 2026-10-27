"""Test-job long polling must obey the caller's complete wait budget."""

import asyncio
import importlib
from collections import OrderedDict
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.timing import await_completion, await_signal, watchdog_timeout

jobs = importlib.import_module("services.tools.run_tests")
RUNNING = {
    "success": True,
    "data": {
        "job_id": "job",
        "status": "running",
        "last_update_unix_ms": 1,
        "progress": {"editor_is_focused": False},
    },
}


@pytest.fixture
def job_transport(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_ENABLE_FOCUS_NUDGE", "1")
    monkeypatch.delenv("UNITY_MCP_DISABLE_FOCUS_NUDGE", raising=False)
    monkeypatch.setattr(jobs.config, "transport_mode", "http")
    monkeypatch.setattr(jobs.config, "http_remote_hosted", False)
    sender = AsyncMock(return_value=deepcopy(RUNNING))
    monkeypatch.setattr(jobs, "_nudge_states", OrderedDict())
    monkeypatch.setattr(jobs, "_terminal_nudge_jobs", OrderedDict())
    monkeypatch.setattr(jobs, "_background_tasks", set())
    monkeypatch.setattr(jobs, "_active_nudge_task", None)
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Project@aaaa")
    )
    monkeypatch.setattr(jobs, "_get_unity_project_path", AsyncMock(return_value="/project"))
    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(jobs, "should_nudge", lambda **kwargs: False)
    return sender


@pytest.fixture
def wait_budgets(monkeypatch):
    """Observe budget propagation while retaining real asyncio cancellation semantics."""
    budgets = []
    wait_for = asyncio.wait_for

    async def observe(operation, timeout):
        budgets.append(timeout)
        return await wait_for(operation, timeout)

    monkeypatch.setattr(asyncio, "wait_for", observe)
    return budgets


@pytest.mark.asyncio
async def test_wait_does_not_start_another_fetch_at_deadline(job_transport):
    # Given: a running job with no completion during the one-second wait.
    async def fetch(*args, **kwargs):
        await asyncio.sleep(0.05)
        return deepcopy(RUNNING)

    job_transport.side_effect = fetch
    # When: the tool waits to its deadline.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: it returns the latest status without starting another transport request.
    assert response.success and response.data.status == "running"
    assert job_transport.await_count == 1


@pytest.mark.asyncio
async def test_slow_first_fetch_is_bounded_by_wait_timeout(job_transport, wait_budgets):
    # Given: a fetch whose normal transport timeout exceeds the caller's budget.
    entered, cancelled, release_cleanup, cleaned = (asyncio.Event() for _ in range(4))
    fetch_tasks = []

    async def fetch(*args, **kwargs):
        fetch_tasks.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finally:
            await release_cleanup.wait()
            cleaned.set()

    job_transport.side_effect = fetch
    # When: the tool waits for at most one second.
    task = asyncio.create_task(jobs.get_test_job(AsyncMock(), "job", wait_timeout=1))
    try:
        await await_signal(entered, "first status fetch", timeout=watchdog_timeout(10))
        await await_signal(cancelled, "fetch budget cancellation", timeout=watchdog_timeout(10))
        # Then: expiration cancels the fetch, but delivery still waits for owned cleanup.
        assert not task.done() and not cleaned.is_set()
        release_cleanup.set()
        response = await await_completion(task, "expired status wait", timeout=watchdog_timeout(10))
    finally:
        release_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(wait_budgets) == 1 and 0 < wait_budgets[0] <= 1
    assert response.success is False
    assert "wait_timeout" in response.error
    assert job_transport.await_count == 1
    assert cleaned.is_set() and fetch_tasks[0].done() and fetch_tasks[0].cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_stage", ["project", "nudge"])
async def test_focus_nudge_shares_the_wait_budget(
    job_transport, wait_budgets, monkeypatch, slow_stage
):
    # Given: a running status whose focus recovery would take longer than the wait.
    monkeypatch.setattr(jobs, "should_nudge", lambda **kwargs: True)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    stage_tasks = []
    now = [100.0]
    monkeypatch.setattr(
        jobs.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: now[0])
    )

    async def fetch(*args, **kwargs):
        # Spend a known part of the deadline without relying on runner scheduling.
        now[0] += 0.75
        return deepcopy(RUNNING)

    job_transport.side_effect = fetch

    async def slow_nudge(**kwargs):
        stage_tasks.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    if slow_stage == "project":

        async def slow_project(*args):
            await slow_nudge()

        monkeypatch.setattr(jobs, "_get_unity_project_path", slow_project)
        monkeypatch.setattr(jobs, "nudge_unity_focus", AsyncMock(return_value=True))
    else:
        monkeypatch.setattr(jobs, "nudge_unity_focus", slow_nudge)
    # When: the tool polls with a one-second budget.
    task = asyncio.create_task(jobs.get_test_job(AsyncMock(), "job", wait_timeout=1))
    try:
        response = await await_completion(task, "nudge budget", timeout=watchdog_timeout(10))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # Then: it returns its known running status even though recovery timed out.
    assert wait_budgets == [1.0, 0.25]
    assert response.success and response.data.status == "running"
    assert entered.is_set() and cancelled.is_set()
    assert stage_tasks[0].done()
    assert not jobs._background_tasks
    assert jobs._active_nudge_task is None


@pytest.mark.asyncio
async def test_expired_budget_before_first_fetch_returns_explicit_timeout(
    job_transport, monkeypatch
):
    # Given: the process was paused until the deadline before its first fetch.
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(
        jobs.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: next(clock))
    )
    # When: polling resumes.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: unknown status is a timeout, not a None-to-response conversion failure.
    assert response.success is False
    assert "wait_timeout" in response.error
    job_transport.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["fetch", "project", "nudge"])
async def test_wait_preserves_external_cancellation(job_transport, monkeypatch, stage):
    # Given: an outstanding status fetch or focus recovery stage.
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    stage_tasks = []

    async def block(*args, **kwargs):
        stage_tasks.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    if stage == "fetch":
        job_transport.side_effect = block
    else:
        monkeypatch.setattr(jobs, "should_nudge", lambda **kwargs: True)
        if stage == "project":
            monkeypatch.setattr(jobs, "_get_unity_project_path", block)
        else:
            monkeypatch.setattr(jobs, "nudge_unity_focus", block)
    task = asyncio.create_task(jobs.get_test_job(AsyncMock(), "job", wait_timeout=10))
    try:
        await await_signal(entered, f"blocked {stage}", timeout=watchdog_timeout(10))
        # When: the caller cancels the tool.
        task.cancel()
        # Then: cancellation propagates and its owned child is drained.
        with pytest.raises(asyncio.CancelledError):
            await await_completion(task, "cancelled status wait", timeout=watchdog_timeout(10))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cancelled.is_set()
    assert stage_tasks[0].done() and stage_tasks[0].cancelled()
    assert not jobs._background_tasks
    assert jobs._active_nudge_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 0, -1])
async def test_nonpositive_wait_preserves_single_status_fetch(job_transport, wait_timeout):
    # Given: the original immediate polling modes.
    # When: the tool is invoked.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=wait_timeout)
    # Then: it fetches once and preserves the running result shape.
    assert response.success and response.data.status == "running"
    assert job_transport.await_count == 1


@pytest.mark.asyncio
async def test_failed_terminal_job_preserves_test_results(job_transport):
    # Given: the job failed because a test failed, while the status request succeeded.
    job_transport.return_value = {
        "success": True,
        "data": {
            "job_id": "job",
            "status": "failed",
            "result": {
                "mode": "EditMode",
                "summary": {
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "durationSeconds": 0.1,
                    "resultState": "Failed",
                },
            },
        },
    }
    # When: long polling sees the terminal status.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: it returns the full failed-test data immediately.
    assert response.success
    assert response.data.result.summary.failed == 1
    assert job_transport.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("paged", [False, True])
async def test_terminal_wait_includes_native_cleanup(job_transport, monkeypatch, status, paged):
    from services.tools.shared_tool_reads import SharedToolReads

    # The virtual wait clock must not reuse a snapshot against the real cache clock.
    monkeypatch.setattr(jobs, "_job_status_reads", SharedToolReads(freshness_s=0))
    now = [0.0]
    monkeypatch.setattr(
        jobs.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: now[0])
    )

    async def advance(seconds):
        now[0] += seconds

    monkeypatch.setattr(jobs.asyncio, "sleep", advance)
    pending = {
        "success": True,
        "data": {
            "job_id": "job",
            "status": status,
            "native_control": {"released": False, "final_witness": False},
            "result_page": {"items": [{"state": "Failed"}], "generation": "saved-page"},
        },
    }
    released = deepcopy(pending)
    released["data"]["native_control"].update(released=True, final_witness=True)
    job_transport.side_effect = [pending, released]

    response = await jobs.get_test_job(
        AsyncMock(),
        "job",
        wait_timeout=30,
        **({"include_failed_tests": True, "page_size": 5} if paged else {}),
    )

    assert response.success and response.data.status == status
    assert response.data.native_control["released"] is True
    assert response.data.native_cleanup_pending is False
    assert response.data.result_page["generation"] == "saved-page"
    assert job_transport.await_count == 2
    assert now[0] == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 1])
async def test_pending_cleanup_returns_results_and_guidance_at_budget(job_transport, wait_timeout):
    job_transport.return_value = {
        "success": True,
        "data": {
            "job_id": "job",
            "status": "failed",
            "native_control": {"released": False, "final_witness": False},
            "result_page": {"items": [{"message": "original failure"}], "generation": "saved"},
        },
    }
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=wait_timeout)
    assert response.success and response.data.status == "failed"
    assert response.data.native_cleanup_pending is True
    assert response.hint == "wait_for_native_cleanup"
    assert "get_test_job" in response.message
    assert response.data.result_page["items"][0]["message"] == "original failure"
    assert job_transport.await_count == 1
