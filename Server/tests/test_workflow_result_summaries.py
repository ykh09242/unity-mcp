"""Explicit performance limits and bounded console evidence preserve observed outcomes."""

from unittest.mock import AsyncMock

import pytest

from services.profiler_thresholds import ProfilerThreshold, evaluate_thresholds
from services.verification.reports import console_delta


@pytest.mark.parametrize(
    "complete,number,state",
    [
        (True, 5, "passed"),
        (True, 15, "issues_found"),
        (False, 5, "incomplete"),
        (False, 15, "issues_found"),
    ],
)
def test_profiler_limits_preserve_sampling_completeness(complete, number, state):
    response = {
        "success": True,
        "data": {
            "window_id": "window",
            "state": "completed",
            "complete": complete,
            "active_recorders": 0,
            "recorder_cleanup": "disposed",
            "markers": [
                {"name": "Frame", "status": "available", "unit": "TimeNanoseconds", "p95": number}
            ],
        },
    }
    limits = [
        ProfilerThreshold(marker="Frame", statistic="p95", unit="TimeNanoseconds", maximum=10)
    ]
    result = evaluate_thresholds(response, "window", limits)
    assert result["success"] is True
    assert result["data"]["verification"]["status"] == state
    assert result["data"]["verification"]["complete"] is complete
    assert "verification" not in response["data"]


@pytest.mark.parametrize("unit", [None, "Bytes"])
def test_profiler_unknown_metric_or_units_never_pass(unit):
    response = {
        "success": True,
        "data": {
            "window_id": "window",
            "complete": True,
            "markers": [{"name": "Frame", "status": "available", "unit": unit, "max": 0}],
        },
    }
    result = evaluate_thresholds(
        response,
        "window",
        [ProfilerThreshold(marker="Frame", statistic="max", unit="TimeNanoseconds", maximum=10)],
    )
    assert result["data"]["verification"]["status"] == "incomplete"
    assert result["data"]["verification"]["further_fixes_required"] is None


@pytest.mark.asyncio
async def test_profiler_thresholds_validate_before_dispatch_and_are_server_only(monkeypatch):
    import services.tools.manage_profiler as module

    send = AsyncMock(return_value={"success": True, "data": {"window_id": "window"}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="original")
    )
    limits = [{"marker": "Frame", "statistic": "max", "unit": "TimeNanoseconds", "maximum": 10}]
    refused = await module.manage_profiler(None, "window_start", thresholds=limits)
    assert refused["success"] is False
    send.assert_not_awaited()
    result = await module.manage_profiler(
        None, "window_result", window_id="window", thresholds=limits
    )
    assert result["data"]["verification"]["status"] == "incomplete"
    assert "thresholds" not in send.call_args.args[3]


def test_console_delta_keeps_loss_unknown_and_bounds_actual_findings():
    before = {"generation": "g", "filter_identity": "f"}
    after = {
        **before,
        "loss_unknown": True,
        "has_more": False,
        "coverage": {"observed_interval_rows": 40},
        "items": [{"entry": {"type": "Error", "message": "x" * 2000, "file": "A.cs", "line": 7}}]
        * 40,
    }
    result = console_delta(before, after)
    assert result["state"] == "failed"
    assert result["coverage"] == "unknown"
    assert len(result["findings"]) == 20
    assert len(result["findings"][0]["message"]) == 1024
    assert result["findings_truncated"] is True
    after["items"] = []
    assert console_delta(before, after)["state"] == "incomplete"
    after["loss_unknown"] = False
    assert console_delta(before, after)["state"] == "passed"
    after["filter_identity"] = "changed"
    assert console_delta(before, after)["state"] == "incomplete"


def test_scenario_summary_retains_primary_failure_and_bounded_step_counts():
    from services.workflow_summaries import scenario_summary

    data = {
        "job_id": "a" * 32,
        "status": "failed",
        "phase": "done",
        "runner_resources_released": True,
        "failure": {"code": "late_failure", "message": "x" * 2000},
        "steps": [{"status": "succeeded"}] * 300,
        "cleanup_failures": [{"code": "cleanup_failure"}] * 20,
    }
    summary = scenario_summary(data)
    assert summary["status"] == "failed"
    assert summary["settled"] is True
    assert summary["step_counts_exact"] is False
    assert summary["inspected_steps"] == 256
    assert summary["failure"]["code"] == "late_failure"
    assert len(summary["failure"]["message"]) == 1024
    assert len(summary["cleanup_failures"]) == 10
    assert summary["cleanup_failures_truncated"] is True
    data["runner_resources_released"] = False
    assert scenario_summary(data)["settled"] is False


@pytest.mark.asyncio
async def test_scenario_wrappers_attach_observed_summary_without_replay(monkeypatch):
    import services.tools.manage_play_scenario as play
    import services.tools.manage_scenario_recording as recording

    run = {
        "success": True,
        "data": {
            "job_id": "a" * 32,
            "status": "cancelled",
            "phase": "done",
            "steps": [],
            "runner_resources_released": True,
        },
    }
    send = AsyncMock(return_value=run)
    monkeypatch.setattr(play, "get_unity_instance_from_context", AsyncMock(return_value="original"))
    monkeypatch.setattr(play, "send_with_unity_instance", send)
    result = await play.manage_play_scenario(None, "status", job_id="a" * 32)
    assert result["data"]["summary"]["status"] == "cancelled"
    assert result["success"] is True
    send.assert_awaited_once()
    draft = {
        "success": True,
        "data": {
            "recording_id": "b" * 32,
            "status": "stopped",
            "saved": True,
            "requires_review": True,
            "scenario": {"name": "recorded", "draft": True, "steps": [{}] * 5},
            "diagnostics": ["warning"] * 20,
            "limitations": ["observed only"],
        },
    }
    send = AsyncMock(return_value=draft)
    monkeypatch.setattr(
        recording, "get_unity_instance_from_context", AsyncMock(return_value="original")
    )
    monkeypatch.setattr(recording, "send_with_unity_instance", send)
    result = await recording.manage_scenario_recording(None, "stop", recording_id="b" * 32)
    assert result["data"]["summary"]["scenario_name"] == "recorded"
    assert result["data"]["summary"]["requires_review"] is True
    assert result["data"]["summary"]["step_count"] == 5
    assert result["data"]["summary"]["diagnostics_truncated"] is True
    assert "summary" not in draft["data"]
    send.assert_awaited_once()
