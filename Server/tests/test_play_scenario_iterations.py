"""Regression coverage for explicit iteration admission and historical accounting."""

from copy import deepcopy

from pydantic import JsonValue, ValidationError
import pytest

from cli.utils.play_scenario_iterations import (
    ATTEMPTED_KEYS,
    STATE_KEYS,
    IterationResultsError,
    add_iteration_counts,
    iteration_counts,
    validate_iteration_results,
    zero_iteration_counts,
)
from cli.utils.play_scenario_player import PlayerFinalReport, PlayerRunOptions, run_player

from .test_play_scenario_player import bundle, child


def _row(iteration: int, status: str = "passed") -> dict[str, JsonValue]:
    start = iteration * 10
    row: dict[str, JsonValue] = {
        "iteration": iteration,
        "status": status,
        "started_unix_ms": start,
        "finished_unix_ms": start + 1,
    }
    if status in {"pending", "skipped"}:
        row["started_unix_ms"] = None
    if status in {"pending", "running"}:
        row["finished_unix_ms"] = None
    return row


def _report(rows: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    return {
        "status": "failed",
        "started_unix_ms": 0,
        "finished_unix_ms": 1000,
        "iteration_results_version": 1,
        "iteration_results": rows,
    }


def test_native_failure_preserves_passed_and_skipped_slots():
    report = _report([_row(1), _row(2, "failed"), _row(3, "skipped")])
    counts = iteration_counts(report, 3)
    assert counts == {
        **zero_iteration_counts(),
        "planned": 3,
        "executed": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 1,
    }
    assert report["iteration_results"][2]["started_unix_ms"] is None


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_native_baseline_failure_is_attempted_without_any_step_start(status):
    report = _report([_row(1, status), _row(2, "skipped")])
    report["status"] = status
    counts = iteration_counts(report, 2)
    assert counts["executed"] == counts[status] == 1
    assert counts["skipped"] == 1


def test_live_checkpoint_accounting_partitions_all_states():
    rows = [_row(index, state) for index, state in enumerate(STATE_KEYS[:-1], 1)]
    report = _report(rows)
    report["status"] = "running"
    counts = iteration_counts(report, 7)
    assert counts["planned"] == sum(counts[state] for state in STATE_KEYS) == 7
    assert counts["executed"] == sum(counts[state] for state in ATTEMPTED_KEYS) == 5
    assert all(counts[state] == 1 for state in STATE_KEYS[:-1])
    with pytest.raises(IterationResultsError, match="unfinished"):
        validate_iteration_results(report, 7, terminal=True)


@pytest.mark.parametrize("version", [None, True, False, 0, 2, "1", 1.0])
def test_native_version_rejects_coercion_and_unknown_versions(version):
    report = _report([_row(1)])
    report["iteration_results_version"] = version
    with pytest.raises(IterationResultsError):
        iteration_counts(report, 1)


@pytest.mark.parametrize("field", ["iteration_results", "iteration_results_version"])
def test_partial_native_contract_cannot_fall_back_to_legacy_success(field):
    report = _report([_row(1)])
    report["status"] = "succeeded"
    del report[field]
    with pytest.raises(IterationResultsError, match="both"):
        iteration_counts(report, 1)


@pytest.mark.parametrize("ids", [[], [1], [1, 1], [2, 1], [1, 3], [0, 2], [1, 2, 3]])
def test_native_ledger_requires_exact_sequential_planned_ids(ids):
    with pytest.raises(IterationResultsError):
        iteration_counts(_report([_row(iteration) for iteration in ids]), 2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iteration", True),
        ("iteration", "1"),
        ("iteration", 1.0),
        ("status", "succeeded"),
        ("status", 1),
        ("started_unix_ms", True),
        ("started_unix_ms", "10"),
        ("started_unix_ms", 10.0),
        ("started_unix_ms", -1),
        ("finished_unix_ms", 2**63),
        ("finished_unix_ms", False),
    ],
)
def test_native_row_fields_are_strict(field, value):
    row = _row(1)
    row[field] = value
    with pytest.raises(IterationResultsError):
        iteration_counts(_report([row]), 1)


@pytest.mark.parametrize(
    ("status", "start", "finish"),
    [
        ("pending", 1, None),
        ("pending", None, 1),
        ("running", None, None),
        ("running", 1, 2),
        ("passed", None, 2),
        ("failed", 1, None),
        ("timed_out", 3, 2),
        ("cancelled", None, None),
        ("skipped", 1, 2),
    ],
)
def test_native_state_requires_consistent_timestamps(status, start, finish):
    row = {
        "iteration": 1,
        "status": status,
        "started_unix_ms": start,
        "finished_unix_ms": finish,
    }
    report = _report([row])
    report["status"] = "running"
    with pytest.raises(IterationResultsError):
        iteration_counts(report, 1)


def test_native_equal_timestamp_and_nullable_skipped_finish_are_valid():
    attempted = _row(1, "cancelled")
    attempted["started_unix_ms"] = attempted["finished_unix_ms"] = 0
    skipped = _row(2, "skipped")
    skipped["finished_unix_ms"] = None
    counts = iteration_counts(_report([attempted, skipped]), 2)
    assert counts["cancelled"] == counts["executed"] == counts["skipped"] == 1


@pytest.mark.parametrize("status", ["pending", "running", "skipped", "failed", "cancelled"])
def test_native_success_cannot_claim_non_passed_repeat(status):
    report = _report([_row(1, status)])
    report["status"] = "succeeded"
    with pytest.raises(IterationResultsError):
        iteration_counts(report, 1)


def test_native_intervals_must_fit_report_and_repeat_order():
    rows = [_row(1), _row(2)]
    for field, value in [("started_unix_ms", 12), ("finished_unix_ms", 10)]:
        report = _report(deepcopy(rows))
        report[field] = value
        with pytest.raises(IterationResultsError):
            iteration_counts(report, 2)
    rows[1]["started_unix_ms"] = 9
    with pytest.raises(IterationResultsError, match="overlap"):
        iteration_counts(_report(rows), 2)


@pytest.mark.parametrize("repeat_count", [True, 0, 11, 1.0, "1"])
def test_repeat_count_is_strict_even_for_legacy(repeat_count):
    with pytest.raises(IterationResultsError):
        iteration_counts({"status": "succeeded"}, repeat_count)


def test_legacy_admitted_success_preserves_previous_complete_accounting():
    report = {"status": "succeeded"}
    assert validate_iteration_results(report, 10) is None
    counts = iteration_counts(report, 10)
    assert counts["planned"] == counts["executed"] == counts["passed"] == 10


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_legacy_steps_distinguish_attempts_skipped_and_missing_evidence(status):
    report = {
        "status": status,
        "steps": [
            {"iteration": 1, "status": "passed", "started_unix_ms": 0},
            {"iteration": 2, "status": "passed", "started_unix_ms": 1},
            {"iteration": 3, "status": "skipped", "started_unix_ms": None},
        ],
    }
    counts = iteration_counts(report, 4)
    assert counts["executed"] == 2
    assert counts["passed"] == counts[status] == counts["skipped"] == counts["unknown"] == 1


def test_legacy_unfinished_earlier_attempt_remains_executed_without_inventing_a_pass():
    report = {
        "status": "failed",
        "steps": [
            {"iteration": 1, "status": "running", "started_unix_ms": 0},
            {"iteration": 2, "status": "running", "started_unix_ms": 1},
        ],
    }
    counts = iteration_counts(report, 2)
    assert counts["executed"] == 2
    assert counts["running"] == counts["failed"] == 1
    assert counts["passed"] == 0


@pytest.mark.parametrize("start", [None, True, False, -1, "1", 1.0])
def test_legacy_invalid_or_absent_start_does_not_establish_execution(start):
    report = {
        "status": "failed",
        "steps": [{"iteration": 1, "status": "failed", "started_unix_ms": start}],
    }
    counts = iteration_counts(report, 1)
    assert counts["executed"] == counts["failed"] == counts["passed"] == 0
    assert counts["unknown"] == 1


def test_legacy_resource_failure_can_reclassify_attributed_previous_pass():
    report = {
        "status": "failed",
        "failure": {"iteration": 1, "code": "resource_assertion_failed"},
        "steps": [_row(1), _row(2, "failed")],
    }
    counts = iteration_counts(report, 2)
    assert counts["executed"] == counts["failed"] == 2
    assert counts["passed"] == 0


def test_aggregation_is_additive_and_does_not_alias_or_mutate_inputs():
    first = iteration_counts(_report([_row(1, "failed"), _row(2, "skipped")]), 2)
    original = first.copy()
    second = iteration_counts({"status": "succeeded"}, 3)
    combined = add_iteration_counts(first, second)
    assert first == original
    assert combined["planned"] == 5 and combined["executed"] == 4
    assert combined["passed"] == 3 and combined["failed"] == combined["skipped"] == 1
    empty = zero_iteration_counts()
    empty["planned"] = 1
    assert zero_iteration_counts()["planned"] == 0


def test_player_admission_preserves_legacy_and_accepts_native_ledger(bundle, child):
    build, output, _manifest = bundle
    report = run_player(PlayerRunOptions(build, output)).report
    PlayerFinalReport.model_validate(report)
    report["iteration_results_version"] = 1
    report["iteration_results"] = [
        {"iteration": 1, "status": "passed", "started_unix_ms": 1, "finished_unix_ms": 2}
    ]
    PlayerFinalReport.model_validate(report)
    report["iteration_results"][0]["status"] = "failed"
    with pytest.raises(ValidationError):
        PlayerFinalReport.model_validate(report)


def test_player_admission_checks_terminal_ledger_before_discarding_extra_fields(bundle, child):
    build, output, _manifest = bundle
    report = run_player(PlayerRunOptions(build, output)).report
    report["iteration_results_version"] = 1
    with pytest.raises(ValidationError):
        PlayerFinalReport.model_validate(report)


@pytest.mark.parametrize("rows", [None, {}, "rows", [_row(1), None], [_row(1), []]])
def test_native_container_and_each_row_must_have_the_declared_shape(rows):
    report = _report([])
    report["iteration_results"] = rows
    with pytest.raises(IterationResultsError):
        iteration_counts(report, 1)


@pytest.mark.parametrize("field", ["status", "started_unix_ms", "finished_unix_ms"])
def test_native_rows_require_all_contract_fields(field):
    row = _row(1)
    del row[field]
    with pytest.raises(IterationResultsError):
        iteration_counts(_report([row]), 1)


def test_native_malformed_run_status_is_rejected_as_contract_error():
    report = _report([_row(1)])
    report["status"] = ["failed"]
    with pytest.raises(IterationResultsError):
        iteration_counts(report, 1)
