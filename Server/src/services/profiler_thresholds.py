"""Evaluate caller-supplied limits against one retained native sampling window."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProfilerThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    marker: str = Field(min_length=1, max_length=256)
    statistic: Literal["mean", "median", "p95", "max"]
    unit: str = Field(min_length=1, max_length=64)
    maximum: float = Field(ge=0, allow_inf_nan=False)


def evaluate_thresholds(response, window_id, thresholds):
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return response
    data = response["data"]
    observed, findings, unavailable = [], [], []
    complete = (
        response.get("success") is True
        and data.get("window_id") == window_id
        and data.get("state") == "completed"
        and data.get("complete") is True
        and type(data.get("active_recorders")) is int
        and data.get("active_recorders") == 0
        and data.get("recorder_cleanup") == "disposed"
    )
    markers = data.get("markers")
    if not isinstance(markers, list) or len(markers) > 8 or data.get("window_id") != window_id:
        markers = []
        complete = False
    for threshold in thresholds:
        matches = [
            row for row in markers if isinstance(row, dict) and row.get("name") == threshold.marker
        ]
        row = matches[0] if len(matches) == 1 else {}
        value = row.get(threshold.statistic)
        if (
            row.get("status") != "available"
            or row.get("unit") != threshold.unit
            or type(value) not in (int, float)
            or type(value) is int
            and value.bit_length() > 64
            or type(value) is float
            and not math.isfinite(value)
        ):
            unavailable.append(
                {
                    "marker": threshold.marker,
                    "statistic": threshold.statistic,
                    "reason": "metric_or_unit_unavailable",
                }
            )
            complete = False
            continue
        observed.append({**threshold.model_dump(), "value": value})
        if value > threshold.maximum:
            findings.append(
                {
                    "code": "profiler_threshold_exceeded",
                    "marker": threshold.marker,
                    "statistic": threshold.statistic,
                    "value": value,
                    "maximum": threshold.maximum,
                    "unit": threshold.unit,
                }
            )
    verification = {
        "status": "issues_found" if findings else "passed" if complete else "incomplete",
        "scope": "caller_supplied_profiler_window_thresholds",
        "window_id": window_id,
        "complete": complete,
        "observed_values": observed,
        "findings": findings,
        "unavailable_metrics": unavailable,
        "further_fixes_required": True if findings else False if complete else None,
    }
    return {**response, "data": {**data, "verification": verification}}
