from unittest.mock import AsyncMock

import pytest

import services.tools.wait_for_operation as module


@pytest.mark.asyncio
async def test_auditor_wait_uses_existing_audit_id(monkeypatch):
    target = module.ObservationTarget("Project@hash", None, "generation", None)
    fetch = AsyncMock()
    monkeypatch.setattr(module, "fetch_observation", fetch)
    await module.fetch_status(target, "auditor", "a" * 32)
    fetch.assert_awaited_once_with(
        target, "manage_project_auditor", {"action": "status", "audit_id": "a" * 32}
    )


@pytest.mark.parametrize("retained", [False, True])
def test_auditor_terminal_requires_released_owner(retained):
    result = module.project_observation(
        "auditor",
        "a" * 32,
        {
            "success": True,
            "data": {
                "audit_id": "a" * 32,
                "state": "failed",
                "native_owner_retained": retained,
                "diagnostic_count": 12,
                "diagnostic_count_exact": False,
                "modules_failed": 1,
            },
        },
    )
    assert result["success"] is True
    assert result["data"]["terminal"] is (not retained)
    if not retained:
        assert result["data"]["summary"]["diagnostic_count_exact"] is False


def test_recording_terminal_summary_preserves_output_and_frame_evidence():
    result = module.project_observation(
        "recording",
        "b" * 32,
        {
            "success": True,
            "data": {
                "job_id": "b" * 32,
                "status": "completed",
                "frames_recorded": 18,
                "skipped_sample_slots": 3,
                "output_path": "Captures/Recordings/sample.mp4",
            },
        },
    )
    assert result["data"]["terminal"] is True
    assert result["data"]["summary"]["frames_recorded"] == 18
    assert result["data"]["summary"]["skipped_sample_slots"] == 3
