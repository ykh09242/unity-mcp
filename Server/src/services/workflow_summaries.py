"""Bounded projections of already observed scenario results and recording drafts."""

import math


def _fields(data, names):
    result = {}
    truncated = []
    if not isinstance(data, dict):
        return result
    for name in names:
        value = data.get(name)
        if isinstance(value, str):
            result[name] = value[:1024]
            if len(value) > 1024:
                truncated.append(name)
        elif type(value) is bool or type(value) is int and value.bit_length() <= 64:
            result[name] = value
        elif type(value) is float and math.isfinite(value):
            result[name] = value
    if truncated:
        result["truncated_fields"] = truncated
    return result


_FAILURE_FIELDS = (
    "code",
    "stage",
    "iteration",
    "step_index",
    "target",
    "component",
    "property_path",
    "expected",
    "actual",
    "message",
)


def scenario_summary(data):
    """Summarize a single observed run without evaluating or rerunning its steps."""
    summary = _fields(
        data,
        (
            "job_id",
            "status",
            "phase",
            "error",
            "pending_status",
            "cleanup_error",
            "report_path",
            "report_error",
            "unexpected_log_count",
            "dropped_log_count",
            "metrics_summary",
            "runner_resources_released",
        ),
    )
    summary["scope"] = "observed_scenario_run"
    summary["settled"] = (
        data.get("status") in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
        and data.get("phase") != "finalizing"
        and data.get("runner_resources_released") is True
    )
    summary["failure"] = _fields(data.get("failure"), _FAILURE_FIELDS)
    cleanup = data.get("cleanup_failures")
    if isinstance(cleanup, list):
        summary["cleanup_failures"] = [_fields(row, _FAILURE_FIELDS) for row in cleanup[:10]]
        summary["cleanup_failures_truncated"] = len(cleanup) > 10
    steps = data.get("steps")
    if isinstance(steps, list):
        counts = {
            state: 0
            for state in (
                "succeeded",
                "failed",
                "cancelled",
                "running",
                "pending",
                "skipped",
                "unknown",
            )
        }
        failures = []
        valid = True
        for step in steps[:256]:
            state = step.get("status") if isinstance(step, dict) else None
            if not isinstance(state, str) or state not in counts:
                state = "unknown"
                valid = False
            counts[state] += 1
            if state == "failed" and len(failures) < 10:
                failures.append(
                    _fields(
                        step,
                        ("stage", "iteration", "step_index", "name", "action", "status", "detail"),
                    )
                )
        summary.update(
            {
                "step_count": len(steps),
                "inspected_steps": min(256, len(steps)),
                "step_counts": counts,
                "step_counts_exact": len(steps) <= 256 and valid,
                "failed_steps": failures,
                "failed_steps_truncated": len(steps) > 256 or counts["failed"] > 10 or not valid,
            }
        )
    return summary


def recording_summary(data):
    """Keep draft identity and existing warnings without generating assertions."""
    summary = _fields(
        data,
        (
            "recording_id",
            "status",
            "reason",
            "saved",
            "requires_review",
            "event_count",
            "max_events",
        ),
    )
    summary["scope"] = "observed_recording_draft"
    scenario = data.get("scenario")
    if isinstance(scenario, dict):
        identity = _fields(scenario, ("name", "draft"))
        if "name" in identity:
            summary["scenario_name"] = identity["name"]
        if "draft" in identity:
            summary["draft"] = identity["draft"]
        if isinstance(scenario.get("steps"), list):
            summary["step_count"] = len(scenario["steps"])
    for name in ("diagnostics", "limitations"):
        values = data.get(name)
        if isinstance(values, list):
            summary[name] = [value[:1024] for value in values[:10] if isinstance(value, str)]
            summary[name + "_truncated"] = len(values) > 10 or any(
                not isinstance(value, str) or len(value) > 1024 for value in values[:10]
            )
    return summary


def attach_summary(response, kind):
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return response
    data = response["data"]
    identity = "job_id" if kind == "scenario" else "recording_id"
    if not isinstance(data.get(identity), str) or not isinstance(data.get("status"), str):
        return response
    if data["status"] not in {
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
        "stopped",
        "expired",
    }:
        return response
    summary = scenario_summary(data) if kind == "scenario" else recording_summary(data)
    return {**response, "data": {**data, "summary": summary}}
