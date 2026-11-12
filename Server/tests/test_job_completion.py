from unittest.mock import AsyncMock

import pytest

import services.job_completion as module
from services.tools.run_tests import RunTestsStartResponse


@pytest.mark.asyncio
async def test_initial_wait_preserves_start_and_failed_job_outcomes(monkeypatch):
    response = RunTestsStartResponse(success=True, data={"job_id": "a" * 32, "status": "running"})
    target = object()

    async def observe(ctx, kind, job_id, wait_timeout):
        assert module.operation_wait_target.get() is target
        assert kind == "test" and job_id == "a" * 32 and wait_timeout == 5
        return {
            "success": True,
            "data": {
                "job_status": "failed",
                "terminal": True,
                "phase": "terminal",
                "wait_status": "observed",
                "summary": {"error": "assertion failed"},
            },
        }

    monkeypatch.setattr(module, "wait_for_operation", observe)
    monkeypatch.setattr(module, "authorize_remote_operation", AsyncMock())
    result = await module.complete_job(None, target, "test", response, 5)
    assert result.success is True
    assert result.data.status == "running"
    assert result.data.completion["status"] == "failed"
    assert result.data.completion["complete"] is True
    assert module.operation_wait_target.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, object()])
async def test_unavailable_observation_preserves_known_start(monkeypatch, target):
    response = {"success": True, "data": {"job_id": "a" * 32}}
    observe = AsyncMock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(module, "wait_for_operation", observe)
    monkeypatch.setattr(module, "authorize_remote_operation", AsyncMock())
    result = await module.complete_job(None, target, "build", response, 5)
    assert result["success"] is True
    assert result["data"]["job_id"] == "a" * 32
    assert result["data"]["completion"]["complete"] is False
    if target is None:
        observe.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_does_not_capture_or_poll(monkeypatch):
    capture = AsyncMock()
    observe = AsyncMock()
    monkeypatch.setattr(module, "capture_target", capture)
    monkeypatch.setattr(module, "wait_for_operation", observe)
    assert await module.prepare_wait(None, 0) is None
    response = {"success": True, "data": {"job_id": "job"}}
    assert await module.complete_job(None, None, "build", response, 0) is response
    capture.assert_not_awaited()
    observe.assert_not_awaited()


@pytest.mark.asyncio
async def test_package_completion_returns_bounded_observed_source_flags(monkeypatch):
    target = object()
    monkeypatch.setattr(module, "authorize_remote_operation", AsyncMock())
    monkeypatch.setattr(
        module,
        "wait_for_operation",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "job_status": "succeeded",
                    "terminal": True,
                    "phase": "terminal",
                    "wait_status": "observed",
                    "summary": {"result_version": "1.2.3"},
                },
            }
        ),
    )
    fetch = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "compilation": {"is_compiling": True, "unknown": "large" * 1000},
                "assets": {"external_changes_dirty": False},
                "unrelated": [1] * 1000,
            },
        }
    )
    monkeypatch.setattr(module, "fetch_observation", fetch)
    result = await module.complete_job(
        None, target, "package", {"success": True, "data": {"job_id": "job"}}, 5
    )
    assert result["data"]["completion"]["complete"] is True
    assert result["data"]["completion"]["source_state"] == {
        "status": "observed",
        "scope": "editor_flags",
        "compilation": {"is_compiling": True},
        "assets": {"external_changes_dirty": False},
    }
    fetch.assert_awaited_once_with(target, "get_editor_state", {})
