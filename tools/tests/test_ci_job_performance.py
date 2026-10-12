"""Workload observations measure scheduling without inventing complete workflow latency."""

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math

import pytest

from tools import ci_job_performance as jobs


def iso(seconds):
    return (datetime(2026, 10, 12, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def snapshots():
    return [
        {
            "id": f"3.{minor}-tools",
            "context": {
                "python": f"3.{minor}",
                "suite": "tools",
                "runner": "Linux-X64",
                "workers": 2,
                "coverage": False,
                "os": "ubuntu-26.04",
            },
            "successful": True,
            "inventory": "a" * 64,
        }
        for minor in range(11, 16)
    ]


@pytest.fixture
def run():
    return {"id": 20, "workflow_id": 2, "event": "push", "head_branch": "beta", "run_attempt": 1}


@pytest.fixture
def matrix(snapshots):
    intervals = [(0, 60), (30, 90), (60, 120), (120, 150), (0, 0)]
    return [
        {
            "name": f"Python tests gate / Run Python tools tests ({entry['context']['python']})",
            "id": index + 1,
            "run_attempt": 1,
            "labels": ["ubuntu-26.04"],
            "status": "completed",
            "conclusion": "success",
            "started_at": iso(start),
            "completed_at": iso(end),
            "steps": [],
        }
        for index, (entry, (start, end)) in enumerate(zip(snapshots, intervals))
    ]


def test_workload_measures_overlap_and_excludes_active_reporting_jobs(matrix, snapshots, run):
    # Given a complete matrix and still-running report/gate/other jobs.
    inventory = [*matrix, {"name": "Python timing report"}, {"name": "Unity tests gate"}]
    # When observing completed workload intervals.
    result = jobs.observe(inventory, run, ("python", snapshots)).record()
    # Then span, summed job-seconds and actual concurrency describe only the matrix.
    assert result["elapsedSpanSeconds"] == 150
    assert result["summedJobSeconds"] == 210
    assert result["averageConcurrency"] == 1.4
    assert result["peakConcurrency"] == 2
    assert len(result["jobs"]) == 5
    assert result["excludedJobs"] == ["Python timing report", "Unity tests gate"]


def test_latest_retry_replaces_prior_interval_without_doublecount(matrix, snapshots, run):
    # Given an earlier failed attempt and a later successful retry with the same name.
    earlier = {**matrix[0], "id": 100, "conclusion": "failure", "completed_at": iso(500)}
    retry = {**matrix[0], "id": 101, "run_attempt": 2}
    for job in matrix[1:]:
        job["run_attempt"] = 2
    run["run_attempt"] = 2
    # When selecting the latest attempt regardless of response order.
    result = jobs.observe([retry, *matrix[1:], earlier], run, ("python", snapshots)).record()
    # Then only the latest interval contributes.
    assert result["summedJobSeconds"] == 210
    assert result["jobs"][0]["job_id"] == 101
    assert result["successful"] is True


@pytest.mark.parametrize("change", ["missing", "active", "skipped", "prefix", "unexpected"])
def test_incomplete_or_ambiguous_cohort_is_rejected(matrix, snapshots, run, change):
    # Given a matrix whose completeness or identity cannot be established.
    if change == "missing":
        matrix.pop()
    elif change == "active":
        matrix[0]["status"] = "in_progress"
    elif change == "skipped":
        matrix[0]["conclusion"] = "skipped"
    elif change == "prefix":
        matrix[0]["name"] = matrix[0]["name"].replace("Python tests gate", "Second invocation")
    else:
        matrix.append({"name": "Run Python server tests (3.11)"})
    # When selecting workload intervals, then no partial cohort is emitted.
    with pytest.raises(ValueError):
        jobs.observe(matrix, run, ("python", snapshots))


@pytest.mark.parametrize(
    "value", [None, True, 123, math.inf, "bad", "2026-10-12T00:00:00", iso(-1)]
)
def test_invalid_interval_timestamps_are_rejected(matrix, snapshots, run, value):
    # Given an invalid or reversed completed timestamp.
    matrix[0]["completed_at"] = value
    # When selecting intervals, then timestamp validation rejects it.
    with pytest.raises(ValueError):
        jobs.observe(matrix, run, ("python", snapshots))


def test_python_requires_all_supported_snapshot_rows(matrix, snapshots, run):
    # Given complete API jobs but a missing JUnit matrix snapshot.
    snapshots.pop()
    # When selecting the cohort, then no guessed test inventory is compared.
    with pytest.raises(ValueError, match="every supported"):
        jobs.observe(matrix, run, ("python", snapshots))


def test_full_python_cohort_preserves_coverage_row(matrix, snapshots, run):
    # Given both suites on every supported minor, including coverage on 3.14.
    server = copy.deepcopy(snapshots)
    for index, entry in enumerate(server):
        entry["id"] = entry["id"].replace("tools", "server")
        entry["context"]["suite"] = "server"
        entry["context"]["coverage"] = entry["context"]["python"] == "3.14"
        matrix.append(
            {
                **matrix[index],
                "id": index + 10,
                "name": matrix[index]["name"].replace("tools", "server"),
            }
        )
    # When observing the full workload.
    result = jobs.observe(matrix, run, ("python", [*snapshots, *server])).record()
    # Then all ten rows remain present.
    assert len(result["jobs"]) == 10


def test_compile_inventory_uses_every_manifest_channel(run, monkeypatch):
    # Given all manifest-selected compile jobs.
    names, _ = jobs.selection("compile", [])
    monkeypatch.setenv("CI_WORKLOAD_MAX_PARALLEL", "3")
    matrix = [
        {
            "name": name,
            "id": index + 1,
            "run_attempt": 1,
            "labels": ["ubuntu-26.04"],
            "status": "completed",
            "conclusion": "success",
            "started_at": iso(0),
            "completed_at": iso(100),
        }
        for index, name in enumerate(sorted(names))
    ]
    # When observing the compile workload.
    result = jobs.observe(matrix, run, ("compile", [])).record()
    # Then every manifest channel participates in both time metrics.
    assert len(result["jobs"]) == len(names)
    assert result["elapsedSpanSeconds"] == 100
    assert result["summedJobSeconds"] == 100 * len(names)
    assert result["peakConcurrency"] == len(names)
    assert result["context"]["configured_concurrency"] == 3


def test_workload_history_roundtrip_recomputes_measured_metrics(matrix, snapshots, run):
    # Given a persisted observation.
    observed = jobs.observe(matrix, run, ("python", snapshots))
    # When reading its JSON representation.
    restored = jobs.read_workload(json.loads(json.dumps(observed.record())))
    # Then context, intervals and measured values are preserved.
    assert restored == observed


@pytest.mark.parametrize("value", [True, None, "210", math.nan, math.inf, -1, 10**400, 999])
def test_malformed_history_metrics_are_rejected(matrix, snapshots, run, value):
    # Given malformed or inconsistent stored job-seconds.
    record = json.loads(json.dumps(jobs.observe(matrix, run, ("python", snapshots)).record()))
    record["summedJobSeconds"] = value
    # When reading history, then malformed metrics never become a baseline.
    with pytest.raises(ValueError):
        jobs.read_workload(record)


@pytest.mark.parametrize(
    "change", ["platform", "event", "branch", "workflow", "selection", "inventory", "failed"]
)
def test_history_requires_matching_complete_successful_context(matrix, snapshots, run, change):
    # Given earlier observations that differ in a consequential workload property.
    observed = jobs.observe(matrix, run, ("python", snapshots))
    context = observed.context
    if change == "platform":
        context = replace(
            context, platforms=((context.jobs[0], ("windows-latest",)), *context.platforms[1:])
        )
    elif change == "event":
        context = replace(context, event="pull_request")
    elif change == "branch":
        context = replace(context, branch="main")
    elif change == "workflow":
        context = replace(context, workflow_id=9)
    elif change == "selection":
        context = replace(context, selection="b" * 64)
    elif change == "inventory":
        context = replace(context, jobs=context.jobs[:-1])
    previous = replace(
        observed,
        context=context,
        jobs=tuple(replace(job, conclusion="failure") for job in observed.jobs)
        if change == "failed"
        else observed.jobs,
    )
    history = [replace(previous, run_id=identifier) for identifier in (19, 18, 17)]
    # When rendering the comparison.
    report, warnings = jobs.render_workload(observed, history)
    # Then mismatched runs do not count as baseline samples.
    assert "Collecting baseline" in report and "| 0 |" in report
    assert not warnings


@pytest.mark.parametrize("repeated", [False, True])
def test_warning_requires_repeated_worsening_after_three_baseline_samples(
    matrix, snapshots, run, repeated
):
    # Given three earlier 100-second baselines and optional preceding 140-second run.
    baseline_matrix = [{**job, "started_at": iso(0), "completed_at": iso(100)} for job in matrix]
    baseline = jobs.observe(baseline_matrix, run, ("python", snapshots))
    elevated = replace(
        baseline, jobs=tuple(replace(job, completed_at=iso(140)) for job in baseline.jobs)
    )
    history = [replace(baseline, run_id=identifier) for identifier in (16, 17, 18)]
    if repeated:
        history.append(replace(elevated, run_id=19))
    # When reporting the current increase.
    report, warnings = jobs.render_workload(elevated, history)
    # Then only sustained increases warn, and both latency/resource metrics remain visible.
    assert len(warnings) == (2 if repeated else 0)
    assert ("Repeated increase" if repeated else "Elevated; awaiting repeat") in report
    assert "not billed minutes" in report and "not full workflow" in report


def test_history_is_bounded_and_duplicate_runs_do_not_supply_extra_samples(matrix, snapshots, run):
    # Given duplicate history entries from the same run.
    observed = jobs.observe(matrix, run, ("python", snapshots))
    history = [replace(observed, run_id=19)] * 10
    # When comparing histories.
    report, warnings = jobs.render_workload(observed, history)
    # Then duplicates cannot manufacture the three-sample baseline.
    assert "| 1 | Collecting baseline" in report
    assert not warnings


@pytest.mark.parametrize("previous_policy", [None, 2, 3])
def test_configured_compile_policy_changes_start_separate_baselines(
    matrix, snapshots, run, previous_policy
):
    # Given observations with explicit configured concurrency, independent of observed peak.
    observed = jobs.observe(matrix, run, ("python", snapshots))
    context = replace(observed.context, scope="compile", configured_concurrency=3)
    current = replace(observed, context=context)
    previous = replace(current, context=replace(context, configured_concurrency=previous_policy))
    history = [replace(previous, run_id=identifier) for identifier in (17, 18, 19)]
    # When comparing compile observations.
    report, warnings = jobs.render_workload(current, history)
    # Then only matching known policy supplies a baseline.
    assert ("| 3 | Observed" if previous_policy == 3 else "| 0 | Collecting baseline") in report
    assert "Configured max-parallel: 3" in report
    assert not warnings


def test_unknown_compile_policy_shows_measurements_but_disables_regression(matrix, snapshots, run):
    # Given an observation whose configured concurrency was not recorded.
    observed = jobs.observe(matrix, run, ("python", snapshots))
    current = replace(observed, context=replace(observed.context, scope="compile"))
    history = [replace(current, run_id=identifier) for identifier in (17, 18, 19)]
    # When rendering legacy/unknown policy measurements.
    report, warnings = jobs.render_workload(current, history)
    # Then measured peak is retained without guessing a configured policy baseline.
    assert "compile baseline comparison is disabled" in report
    assert "| 0 | Collecting baseline" in report
    assert "| Observed peak concurrency | 2 |" in report
    assert not warnings


def test_conflicting_duplicate_job_inventory_is_rejected(matrix, snapshots, run):
    # Given duplicate ID/attempt records with contradictory completion status.
    duplicate = {**matrix[0], "status": "in_progress"}
    # When selecting the latest jobs, then conflicting records cannot manufacture completeness.
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        jobs.observe([*matrix, duplicate], run, ("python", snapshots))


def test_mixed_partial_rerun_rejects_retry_gap_as_workload_latency(matrix, snapshots, run):
    # Given jobs from different attempts separated by an arbitrary retry gap.
    matrix[0]["run_attempt"] = 2
    run["run_attempt"] = 2
    # When observing the workload, then a mixed cohort has no comparable elapsed span.
    with pytest.raises(ValueError, match="mixes attempts"):
        jobs.observe(matrix, run, ("python", snapshots))


@pytest.mark.parametrize(
    "selection_change", ["inventory", "coverage", "workers", "distribution", "os"]
)
def test_python_selection_digest_tracks_test_and_execution_context(snapshots, selection_change):
    # Given a changed test inventory or execution setting.
    original = jobs.selection("python", snapshots)[1]
    if selection_change == "inventory":
        snapshots[0]["inventory"] = "b" * 64
    else:
        snapshots[0]["context"][selection_change] = {
            "coverage": True,
            "workers": 4,
            "distribution": "loadfile",
            "os": "ubuntu-24.04",
        }[selection_change]
    # When deriving the stable workload selection.
    changed = jobs.selection("python", snapshots)[1]
    # Then incompatible test/execution settings never share a baseline.
    assert original != changed
