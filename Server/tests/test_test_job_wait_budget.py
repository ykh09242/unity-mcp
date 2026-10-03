"""Test-job long polling must obey the caller's complete wait budget."""
import asyncio
import importlib
import time
from collections import OrderedDict
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

jobs = importlib.import_module("services.tools.run_tests")
RUNNING = {"success": True, "data": {
    "job_id": "job", "status": "running", "last_update_unix_ms": 1,
    "progress": {"editor_is_focused": False},
}}


@pytest.fixture
def job_transport(monkeypatch):
    sender = AsyncMock(return_value=deepcopy(RUNNING))
    monkeypatch.setattr(jobs, "_nudge_states", OrderedDict())
    monkeypatch.setattr(jobs, "_terminal_nudge_jobs", OrderedDict())
    monkeypatch.setattr(jobs, "_background_tasks", set())
    monkeypatch.setattr(jobs, "_active_nudge_task", None)
    monkeypatch.setattr(jobs, "get_unity_instance_from_context", AsyncMock(return_value="Project@aaaa"))
    monkeypatch.setattr(jobs, "_get_unity_project_path", AsyncMock(return_value="/project"))
    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(jobs, "should_nudge", lambda **kwargs: False)
    return sender


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
async def test_slow_first_fetch_is_bounded_by_wait_timeout(job_transport):
    # Given: a fetch whose normal transport timeout exceeds the caller's budget.
    async def fetch(*args, **kwargs):
        await asyncio.sleep(1.4)
        return deepcopy(RUNNING)
    job_transport.side_effect = fetch
    started = time.monotonic()
    # When: the tool waits for at most one second.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: unknown status is reported as a timeout rather than fabricated running data.
    assert time.monotonic() - started < 1.2
    assert response.success is False
    assert "wait_timeout" in response.error


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_stage", ["project", "nudge"])
async def test_focus_nudge_shares_the_wait_budget(job_transport, monkeypatch, slow_stage):
    # Given: a running status whose focus recovery would take longer than the wait.
    monkeypatch.setattr(jobs, "should_nudge", lambda **kwargs: True)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def slow_nudge(**kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    if slow_stage == "project":
        async def slow_project(*args):
            await slow_nudge()
        monkeypatch.setattr(jobs, "_get_unity_project_path", slow_project)
        monkeypatch.setattr(jobs, "nudge_unity_focus", AsyncMock(return_value=True))
    else:
        monkeypatch.setattr(jobs, "nudge_unity_focus", slow_nudge)
    started = time.monotonic()
    # When: the tool polls with a one-second budget.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: it returns its known running status even though recovery timed out.
    assert time.monotonic() - started < 1.2
    assert response.success and response.data.status == "running"
    assert entered.is_set() and cancelled.is_set()
    assert not jobs._background_tasks


@pytest.mark.asyncio
async def test_expired_budget_before_first_fetch_returns_explicit_timeout(job_transport, monkeypatch):
    # Given: the process was paused until the deadline before its first fetch.
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(jobs.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: next(clock)))
    # When: polling resumes.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: unknown status is a timeout, not a None-to-response conversion failure.
    assert response.success is False
    assert "wait_timeout" in response.error
    job_transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_preserves_external_cancellation(job_transport):
    # Given: an outstanding status request.
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def fetch(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    job_transport.side_effect = fetch
    task = asyncio.create_task(jobs.get_test_job(AsyncMock(), "job", wait_timeout=10))
    await entered.wait()
    # When: the caller cancels the tool.
    task.cancel()
    # Then: cancellation propagates and its child fetch is cleaned up.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


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
    job_transport.return_value = {"success": True, "data": {"job_id": "job", "status": "failed", "result": {
        "mode": "EditMode", "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "durationSeconds": 0.1, "resultState": "Failed"},
    }}}
    # When: long polling sees the terminal status.
    response = await jobs.get_test_job(AsyncMock(), "job", wait_timeout=1)
    # Then: it returns the full failed-test data immediately.
    assert response.success
    assert response.data.result.summary.failed == 1
    assert job_transport.await_count == 1
