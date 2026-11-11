"""Baseline admission never implies observation of an earlier, unrecorded source state."""


def baseline_eligible(result):
    return (
        result.get("complete") is True
        and result.get("stale") is False
        and bool(result.get("environment_signature"))
        and bool(result.get("selected_files"))
        and not result.get("coverage_gaps")
        and not result.get("analyzer_errors")
    )


def baseline_receipt(identity, result):
    return {
        "baseline_id": identity if baseline_eligible(result) else None,
        "baseline_timing": "captured_now_prior_edit_history_unknown",
        "reason": None if baseline_eligible(result) else "baseline_coverage_incomplete",
    }
