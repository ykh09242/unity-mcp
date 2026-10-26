"""Public test-job paths retain bounded cached native cleanup observations."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

jobs = importlib.import_module("services.tools.run_tests")
view = importlib.import_module("services.tools.view_test_results")
wait = importlib.import_module("services.tools.test_job_wait")


def completed_native_reply():
    return {
        "success": True,
        "message": "Test job status retrieved.",
        "data": {
            "job_id": "owned-job",
            "status": "succeeded",
            "completion_kind": "completed",
            "result_availability": "available",
            "native_control": {
                "final_witness": True,
                "termination": "cleanup_pending",
                "released": False,
            },
            "native_cleanup_diagnostics": {
                "editor_update_count": 342,
                "editor_updates_since_final": 267,
                "last_editor_update_uptime": 55.6,
                "tick_count": 344,
                "ticks_since_final": 267,
                "final_witness_uptime": 30.1,
                "deferred_turn_seen": False,
                "last_snapshot_uptime": 55.6,
                "registered_count": 0,
                "active_count": 0,
                "serialized_active_count": 0,
                "global_active": False,
                "last_snapshot_error": None,
                "restored_after_reload": False,
            },
            "no_throttle_diagnostics": {
                "test_run_active": True,
                "settings_captured": True,
                "maintenance_subscribed": True,
                "last_acquire_uptime": 51.6,
                "last_acquire_succeeded": True,
                "last_acquire_suppressed": False,
            },
            "result": {
                "mode": "EditMode",
                "summary": {
                    "total": 1,
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "durationSeconds": 0.1,
                    "resultState": "Passed",
                },
            },
            "result_page": {
                "items": [],
                "total_count": 0,
                "has_more": False,
                "next_cursor": None,
                "generation": "a" * 32,
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 1])
async def test_immediate_and_waited_public_status_keep_native_cleanup_telemetry(
    monkeypatch, wait_timeout
):
    monkeypatch.setattr(jobs.config, "http_remote_hosted", False)
    monkeypatch.setattr(jobs.config, "transport_mode", "stdio")
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(
        jobs.unity_transport,
        "send_with_unity_instance",
        AsyncMock(return_value=completed_native_reply()),
    )
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    monkeypatch.setattr(wait, "get_authenticated_stdio_generation", AsyncMock(return_value="g1"))
    ctx = SimpleNamespace(get_state=AsyncMock(return_value=None))

    response = await jobs.get_test_job(ctx, "owned-job", wait_timeout=wait_timeout)

    data = response.model_dump(mode="json", exclude_none=True)["data"]
    assert data["native_control"]["released"] is False
    assert data["native_cleanup_diagnostics"]["editor_updates_since_final"] == 267
    assert data["native_cleanup_diagnostics"]["deferred_turn_seen"] is False
    assert data["no_throttle_diagnostics"]["settings_captured"] is True


@pytest.mark.asyncio
async def test_public_result_view_keeps_cached_native_cleanup_telemetry(monkeypatch):
    monkeypatch.setattr(view.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        view, "get_unity_instance_from_context", AsyncMock(return_value="Game@owned")
    )
    monkeypatch.setattr(
        view.unity_transport,
        "send_with_unity_instance",
        AsyncMock(return_value=completed_native_reply()),
    )

    result = await view.view_test_results(SimpleNamespace(), "owned-job")

    assert result.is_error is False
    data = result.structured_content["data"]
    assert data["native_cleanup_diagnostics"]["registered_count"] == 0
    assert data["native_cleanup_diagnostics"]["global_active"] is False
    assert data["no_throttle_diagnostics"]["last_acquire_suppressed"] is False


def test_older_package_status_omits_missing_telemetry_without_changing_status():
    response = jobs.GetTestJobResponse.model_validate(
        {"success": True, "data": {"job_id": "older-job", "status": "succeeded"}}
    )
    data = response.model_dump(mode="json", exclude_none=True)["data"]
    assert data["status"] == "succeeded"
    assert "native_cleanup_diagnostics" not in data
    assert "no_throttle_diagnostics" not in data


@pytest.mark.parametrize(
    "invalid",
    [
        {"registered_count": 33},
        {"editor_update_count": -1},
        {"last_snapshot_error": "x" * 129},
    ],
)
def test_public_cleanup_telemetry_rejects_out_of_bounds_scalars(invalid):
    with pytest.raises(ValidationError):
        jobs.NativeCleanupDiagnostics.model_validate(invalid)
