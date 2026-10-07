"""Environment diagnostics survive the public typed test-job response."""

import importlib
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest


jobs = importlib.import_module("services.tools.run_tests")


@pytest.mark.parametrize(
    "status,error",
    [
        ("running", None),
        ("failed", "Test job failed to initialize (tests did not start within timeout)"),
        ("succeeded", None),
    ],
)
def test_typed_test_job_response_preserves_diagnostics(status: str, error: str | None) -> None:
    # Given: an editor payload with additive diagnostics and existing job fields.
    diagnostics = {
        "unity_version": "6000.0.69f1",
        "platform": "LinuxEditor",
        "graphics_api": "Vulkan",
        "last_progress_age_ms": 65000,
        "initialization_timeout_ms": 120000,
        "initialization_failed": status == "failed",
        "stall_suspected": status == "running",
        "possible_causes": ["long_running_test_or_editor_stall", "linux_vulkan_backend_candidate"],
        "recommended_actions": [
            "Compare another graphics backend in a separate reproduction session."
        ],
    }
    payload = {
        "success": True,
        "data": {
            "job_id": "diagnostic-job",
            "status": status,
            "error": error,
            "result": None,
            "diagnostics": diagnostics,
        },
    }
    # When: the public Pydantic contract parses and serializes the response.
    response = jobs.GetTestJobResponse.model_validate(payload).model_dump()
    # Then: diagnostics and existing terminal/error/result boundaries survive.
    assert response["data"].get("diagnostics") == diagnostics
    assert response["data"]["status"] == status
    assert response["data"]["error"] == error
    assert response["data"]["result"] is None


@pytest.mark.asyncio
async def test_get_test_job_returns_diagnostics_without_changing_running_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a running long test reported by the transport; no live editor is contacted.
    payload = {
        "success": True,
        "data": {
            "job_id": "diagnostic-job",
            "status": "running",
            "result": None,
            "progress": {"completed": 0, "current_test_full_name": "Suite.LongTest"},
            "diagnostics": {
                "unity_version": "6000.0.69f1",
                "platform": "LinuxEditor",
                "graphics_api": "Vulkan",
                "last_progress_age_ms": 65000,
                "initialization_timeout_ms": 15000,
                "initialization_failed": False,
                "stall_suspected": True,
                "possible_causes": ["long_running_test_or_editor_stall"],
                "recommended_actions": [
                    "Inspect Editor.log; the long test may still be running normally."
                ],
            },
        },
    }
    sender = AsyncMock(return_value=deepcopy(payload))
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Project@diagnostics")
    )
    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    # When: the real public tool polls the transport once.
    response = await jobs.get_test_job(AsyncMock(), "diagnostic-job")
    serialized = response.model_dump()
    # Then: suspected stalling remains running, and the new diagnostics reach callers.
    assert serialized["data"]["status"] == "running"
    assert serialized["data"].get("diagnostics") == payload["data"]["diagnostics"]
    assert serialized["data"]["progress"]["current_test_full_name"] == "Suite.LongTest"
    sender.assert_awaited_once()
