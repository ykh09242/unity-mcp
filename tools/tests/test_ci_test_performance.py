"""Timing observations must stay comparable, bounded, and independent of test gates."""

import copy
import io
import json
import math
import subprocess
import sys
import zipfile

import pytest

from tools import ci_test_performance as performance


@pytest.fixture
def timing(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite time="20" failures="0" errors="0">'
        '<testcase classname="tests.slow" name="one" time="8"/>'
        '<testcase classname="tests.slow" name="two" time="7"/>'
        '<testcase classname="tests.other" name="skip" time="0"><skipped/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return performance.snapshot(
        report, python="3.15", suite="tools", runner="Linux-X64", workers=0, coverage=False
    )


def bundle(entries):
    return json.dumps({"schema": performance.SCHEMA, "snapshots": entries}).encode()


@pytest.fixture
def environment():
    return {
        "os": "ubuntu-24.04",
        "imageOS": "ubuntu24",
        "imageVersion": "20261005.1.0",
        "python": "3.15.0",
        "dotnetSdk": "10.0.100",
    }


def test_environment_observes_os_release_python_patch_and_bounded_sdk(monkeypatch):
    monkeypatch.setattr(performance.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        performance.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "26.04"},
    )
    monkeypatch.setattr(performance.platform, "python_version", lambda: "3.15.1")
    monkeypatch.setenv("ImageOS", "ubuntu26")
    monkeypatch.setenv("ImageVersion", "20261012.2.0")

    def command(args, **options):
        assert args == ["dotnet", "--version"]
        assert options == {"capture_output": True, "text": True, "timeout": 5, "check": False}
        return subprocess.CompletedProcess(args, 0, "10.0.101\n", "")

    monkeypatch.setattr(performance.subprocess, "run", command)
    assert performance.execution_environment() == {
        "os": "ubuntu-26.04",
        "imageOS": "ubuntu26",
        "imageVersion": "20261012.2.0",
        "python": "3.15.1",
        "dotnetSdk": "10.0.101",
    }


@pytest.mark.parametrize("failure", [OSError("missing"), subprocess.TimeoutExpired("dotnet", 5)])
def test_missing_environment_sources_do_not_hide_test_timings(monkeypatch, failure):
    monkeypatch.setattr(performance.platform, "system", lambda: "Linux")
    monkeypatch.setattr(performance.platform, "freedesktop_os_release", lambda: {})

    def command(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(performance.subprocess, "run", command)
    result = performance.execution_environment()
    assert result["os"] == "Linux-unknown"
    assert result["dotnetSdk"] == "unavailable"


def test_snapshot_records_environment_and_separates_os_baselines(tmp_path, environment):
    junit = tmp_path / "report.xml"
    junit.write_text(
        '<testsuite time="10"><testcase name="case" time="9"/></testsuite>', encoding="utf-8"
    )
    options = {
        "python": "3.15",
        "suite": "server",
        "runner": "Linux-X64",
        "workers": 2,
        "coverage": False,
    }
    legacy = performance.snapshot(junit, **options)
    current = performance.snapshot(junit, **options, environment=environment)
    candidate = performance.snapshot(
        junit, **options, environment={**environment, "os": "ubuntu-26.04"}
    )
    assert performance.read_bundle(bundle([current])) == [current]
    assert not performance.comparable(current, legacy)
    assert not performance.comparable(current, candidate)
    assert not performance.module_comparable(current, candidate, "unknown")
    updated = performance.snapshot(
        junit,
        **options,
        environment={
            **environment,
            "imageVersion": "new",
            "python": "3.15.1",
            "dotnetSdk": "10.0.101",
        },
    )
    assert performance.comparable(current, updated)
    assert performance.module_comparable(current, updated, "unknown")
    report, _ = performance.render_report([current], [], [])
    assert "ubuntu-24.04 | ubuntu24 | 20261005.1.0 | 3.15.0 | 10.0.100" in report


@pytest.mark.parametrize("value", [None, [], {}, {"os": "ubuntu-26.04"}])
def test_malformed_environment_is_rejected(timing, value):
    timing["environment"] = value
    with pytest.raises(ValueError, match="execution environment"):
        performance.read_bundle(bundle([timing]))


def test_csharp_junit_phases_are_observations_not_extra_elapsed(tmp_path):
    junit = tmp_path / "report.xml"
    junit.write_text(
        '<testsuite time="20"><testcase classname="tests.csharp" name="roundtrip" time="19">'
        '<properties><property name="csharp_sdk_version" value="10.0.100"/>'
        '<property name="csharp_target_framework" value="net10.0"/>'
        '<property name="csharp_sdk_discovery_seconds" value="1"/>'
        '<property name="csharp_compile_seconds" value="15"/>'
        '<property name="csharp_execution_seconds" value="0.5"/>'
        '<property name="csharp_execution_seconds" value="1.5"/>'
        '<property name="unrelated_property" value="ignored"/>'
        "</properties></testcase></testsuite>",
        encoding="utf-8",
    )
    result = performance.snapshot(
        junit, python="3.15", suite="server", runner="Linux-X64", workers=2, coverage=False
    )
    assert result["elapsed"] == 20
    assert result["modules"] == {"tests.csharp": 19}
    assert len(result["csharpPhases"]) == 4
    assert performance.read_bundle(bundle([result])) == [result]
    report, warnings = performance.render_report([result], [], [])
    assert not warnings
    assert "| compile | 1 | 15.000s | 15.000s |" in report
    assert "| execution | 2 | 2.000s | 1.500s |" in report
    assert "Selected SDK: 10.0.100; target: net10.0." in report


@pytest.mark.parametrize("duration", ["NaN", "inf", "-1"])
def test_invalid_csharp_phase_duration_is_rejected(tmp_path, duration):
    junit = tmp_path / "report.xml"
    junit.write_text(
        '<testsuite time="20"><testcase name="case" time="19"><properties>'
        f'<property name="csharp_compile_seconds" value="{duration}"/>'
        "</properties></testcase></testsuite>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite nonnegative"):
        performance.junit_summary(junit)


@pytest.mark.parametrize(
    "field,value",
    [
        ("csharpPhases", {}),
        ("csharpPhases", [{"case": "test", "phase": "unknown", "seconds": 1}]),
        ("csharpPhases", [{"case": "test", "phase": "compile", "seconds": True}]),
        ("csharpRuntime", []),
        ("csharpRuntime", {"csharp_sdk_version": 10}),
    ],
)
def test_invalid_csharp_history_metadata_is_rejected(timing, field, value):
    timing[field] = value
    with pytest.raises(ValueError):
        performance.read_bundle(bundle([timing]))


def test_snapshot_keeps_wall_time_distinct_from_summed_test_time(timing):
    assert timing["elapsed"] == 20
    assert timing["tests"] == 3
    assert timing["skipped"] == 1
    assert sum(timing["modules"].values()) == 15
    assert timing["successful"] is True


def test_nested_http_is_detail_not_added_elapsed(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuite time="10"><testcase name="case" time="9"/></testsuite>', encoding="utf-8"
    )
    result = performance.snapshot(
        report,
        python="3.14",
        suite="server",
        runner="Linux-X64",
        workers=2,
        coverage=True,
        nested=report,
    )
    assert result["elapsed"] == 10
    assert result["nestedHttp"]["elapsed"] == 10


@pytest.mark.parametrize("duration", ["NaN", "inf", "-1"])
def test_nonfinite_or_negative_junit_is_rejected(tmp_path, duration):
    report = tmp_path / "report.xml"
    report.write_text(
        f'<testsuite time="1"><testcase name="case" time="{duration}"/></testsuite>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite nonnegative"):
        performance.junit_summary(report)


@pytest.mark.parametrize(
    "field,value",
    [("python", "3.14"), ("runner", "Windows-X64"), ("workers", 2), ("coverage", True)],
)
def test_different_execution_context_is_not_compared(timing, field, value):
    changed = copy.deepcopy(timing)
    changed["context"][field] = value
    assert not performance.comparable(timing, changed)


def test_changed_test_or_skip_inventory_and_failed_runs_are_excluded(timing):
    changed = copy.deepcopy(timing)
    changed["inventory"] = "a" * 64
    assert not performance.comparable(timing, changed)
    changed = copy.deepcopy(timing)
    changed["successful"] = False
    assert not performance.comparable(timing, changed)
    assert not performance.comparable(changed, timing)


def test_median_requires_three_samples_and_both_thresholds():
    assert performance.regression(200, [100, 100], 10) == (None, False)
    assert performance.regression(130, [100, 100, 900], 10) == (100, True)
    assert performance.regression(120, [100, 100, 100], 10) == (100, False)
    assert performance.regression(8, [1, 1, 1], 10) == (1, False)


def test_report_warns_without_counting_nested_time_or_interpreting_markup(timing):
    history = [[copy.deepcopy(timing)] for _ in range(3)]
    timing["elapsed"] = 40
    timing["modules"]["tests.slow"] = 25
    timing["slowest"][0]["name"] = "test|<script>\n`payload`"
    report, warnings = performance.render_report([timing], history, [])
    assert "20.00s | 3 | Slower" in report
    assert len(warnings) == 2
    assert "<script>" not in report
    assert "test&#124;&lt;script&gt; &#96;payload&#96;" in report


def test_invalid_or_duplicate_artifact_data_is_rejected(timing):
    with pytest.raises(ValueError, match="Duplicate"):
        performance.read_bundle(bundle([timing, timing]))
    timing["elapsed"] = math.inf
    with pytest.raises(ValueError, match="finite"):
        performance.read_bundle(bundle([timing]))


@pytest.mark.parametrize("value", ["20", True, None, [], {}])
@pytest.mark.parametrize("field", ["elapsed", "module", "testcase"])
def test_malformed_history_durations_are_rejected_before_comparison(timing, value, field):
    if field == "elapsed":
        timing["elapsed"] = value
    elif field == "module":
        timing["modules"]["tests.slow"] = value
    else:
        timing["slowest"][0]["seconds"] = value
    with pytest.raises(ValueError, match="JSON number"):
        performance.read_bundle(bundle([timing]))


def test_overflowing_history_duration_is_rejected(timing):
    timing["elapsed"] = 10**400
    with pytest.raises(ValueError, match="finite"):
        performance.read_bundle(bundle([timing]))


def test_github_history_filters_runs_and_reads_only_small_named_member(timing, monkeypatch):
    current = {
        "id": 20,
        "workflow_id": 2,
        "run_attempt": 1,
        "head_branch": "beta",
        "event": "push",
        "conclusion": "success",
    }
    eligible = {**current, "id": 19}
    runs = [
        current,
        {**eligible, "id": 18, "event": "pull_request"},
        {**eligible, "id": 17, "conclusion": "failure"},
        {**eligible, "id": 16, "head_branch": "other"},
        {**eligible, "id": 15, "workflow_id": 9},
        eligible,
    ]
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("timings.json", bundle([timing]))
        archive.writestr("../ignored", b"never extracted")
    endpoints = []

    def api(endpoint):
        endpoints.append(endpoint)
        if endpoint.endswith("/runs/20"):
            return json.dumps(current).encode()
        if "/workflows/2/runs?" in endpoint:
            return json.dumps({"workflow_runs": runs}).encode()
        if "/runs/19/artifacts?" in endpoint:
            return json.dumps(
                {
                    "artifacts": [
                        {
                            "id": 31,
                            "name": performance.ARTIFACT,
                            "expired": False,
                            "size_in_bytes": len(content.getvalue()),
                        }
                    ]
                }
            ).encode()
        if endpoint.endswith("/artifacts/31/zip"):
            return content.getvalue()
        pytest.fail(f"Unexpected history request: {endpoint}")

    monkeypatch.setattr(performance, "github_api", api)
    history, notes = performance.github_history("owner/repo", 20)
    assert history == [[timing]] and "run 19" in notes[0]
    assert len(endpoints) == 4


def test_history_outage_still_writes_current_report_and_exits_success(
    timing, tmp_path, monkeypatch
):
    current, output = tmp_path / "current", tmp_path / "output"
    current.mkdir()
    (current / "tools.json").write_text(json.dumps(timing), encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        performance,
        "github_api",
        lambda endpoint: (_ for _ in ()).throw(ValueError("API unavailable")),
    )
    assert (
        performance.main(
            [
                "report",
                "--current",
                str(current),
                "--output",
                str(output),
                "--repository",
                "owner/repo",
                "--run-id",
                "20",
            ]
        )
        == 0
    )
    assert "history is unavailable" in (output / "report.md").read_text(encoding="utf-8")
    assert performance.read_bundle((output / "timings.json").read_bytes()) == [timing]


def test_real_report_cli_returns_success_for_detected_regression(timing, tmp_path):
    history = tmp_path / "history.json"
    history.write_bytes(bundle([timing]))
    timing["elapsed"] = 40
    current = tmp_path / "current"
    current.mkdir()
    (current / "tools.json").write_text(json.dumps(timing), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            performance.__file__,
            "report",
            "--current",
            str(current),
            "--output",
            str(tmp_path / "result"),
            "--history",
            *([str(history)] * 3),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "::warning::3.15-tools" in result.stdout


def test_unchanged_module_retains_baseline_when_another_module_changes(timing):
    history = [[copy.deepcopy(timing)] for _ in range(3)]
    timing["inventory"] = "a" * 64
    timing["moduleInventories"]["tests.other"] = "b" * 64
    timing["modules"]["tests.slow"] = 25
    report, warnings = performance.render_report([timing], history, [])
    assert "Collecting baseline" in report
    assert "| tests.slow | 25.00s | 15.00s (slower) |" in report
    assert len(warnings) == 1


@pytest.mark.parametrize("change", ["inventory", "context", "failed", "nested"])
def test_module_baseline_excludes_changed_workload_or_context(timing, change):
    previous = copy.deepcopy(timing)
    if change == "inventory":
        previous["moduleInventories"]["tests.slow"] = "a" * 64
    elif change == "context":
        previous["context"]["workers"] = 2
    elif change == "failed":
        previous["successful"] = False
    else:
        previous["nestedHttp"] = {"inventory": "a" * 64}
    assert not performance.module_comparable(timing, previous, "tests.slow")


def test_legacy_module_baseline_requires_the_entire_suite_to_match(timing):
    previous = copy.deepcopy(timing)
    del previous["moduleInventories"]
    assert performance.module_comparable(timing, previous, "tests.slow")
    previous["inventory"] = "a" * 64
    assert not performance.module_comparable(timing, previous, "tests.slow")


@pytest.mark.parametrize("value", [None, [], {}, {"tests.slow": "bad"}])
def test_malformed_module_inventory_is_rejected(timing, value):
    timing["moduleInventories"] = value
    with pytest.raises(ValueError, match="module inventory"):
        performance.read_bundle(bundle([timing]))


def test_module_inventory_tracks_case_names_and_outcomes(tmp_path):
    path = tmp_path / "report.xml"
    summaries = []
    for name, outcome in [("one", ""), ("two", ""), ("two", "<skipped/>")]:
        path.write_text(
            f'<testsuite time="1"><testcase classname="module" name="{name}" time="1">'
            f"{outcome}</testcase></testsuite>",
            encoding="utf-8",
        )
        summaries.append(performance.junit_summary(path))
    assert len({value["moduleInventories"]["module"] for value in summaries}) == 3


def test_stages_paginate_and_keep_only_completed_measurements(monkeypatch):
    step = {
        "name": "Run Pylint",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-12T01:00:00Z",
        "completed_at": "2026-10-12T01:00:42Z",
    }
    jobs = [{"name": f"job {i}", "steps": []} for i in range(100)]
    jobs[0]["steps"] = [step, {**step, "status": "in_progress"}, {**step, "conclusion": "skipped"}]
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        page = (
            jobs
            if endpoint.endswith("page=1")
            else [
                {
                    "name": "compile|<job>",
                    "steps": [{**step, "name": "Compile", "conclusion": "failure"}],
                }
            ]
        )
        return json.dumps({"total_count": 101, "jobs": page}).encode()

    monkeypatch.setattr(performance, "github_api", api)
    stages = performance.github_stages("owner/repo", 20)
    assert len(calls) == 2 and len(stages) == 2
    assert stages[0]["seconds"] == 42
    assert stages[1]["conclusion"] == "failure"
    report = performance.render_stages(stages)
    assert "compile&#124;&lt;job&gt;" in report and "42.00s" in report


@pytest.mark.parametrize("end", ["2026-10-12T00:00:00Z", "2026-10-12T01:00:42", "bad"])
def test_invalid_stage_timestamps_are_rejected(monkeypatch, end):
    payload = {
        "total_count": 1,
        "jobs": [
            {
                "name": "job",
                "steps": [
                    {
                        "name": "Compile",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-12T01:00:00Z",
                        "completed_at": end,
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(performance, "github_api", lambda endpoint: json.dumps(payload).encode())
    with pytest.raises(ValueError):
        performance.github_stages("owner/repo", 20)


def test_stage_outage_is_observational_and_still_writes_report(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        performance, "github_api", lambda endpoint: (_ for _ in ()).throw(ValueError("outage"))
    )
    assert (
        performance.main(
            ["stages", "--repository", "owner/repo", "--run-id", "20", "--output", str(tmp_path)]
        )
        == 0
    )
    assert "Stage timings are unavailable" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "timings.json").read_text(encoding="utf-8"))["stages"] == []


@pytest.mark.parametrize("wrapper", ["test_http_security", "tests.test_http_security.TestHTTP"])
def test_changed_nested_http_only_invalidates_identified_wrapper(timing, wrapper):
    timing["modules"][wrapper] = 10
    timing["moduleInventories"][wrapper] = "c" * 64
    timing["nestedHttp"] = {"inventory": "a" * 64}
    previous = copy.deepcopy(timing)
    previous["nestedHttp"]["inventory"] = "b" * 64
    assert performance.module_comparable(timing, previous, "tests.slow")
    assert not performance.module_comparable(timing, previous, wrapper)
    del previous["modules"][wrapper]
    assert not performance.module_comparable(timing, previous, "tests.slow")


@pytest.mark.parametrize(
    "name", ["Restore optional dependency inputs", "Save validated optional dependency inputs"]
)
def test_optional_input_cache_steps_are_reported(monkeypatch, name):
    response = {
        "total_count": 1,
        "jobs": [
            {
                "name": "optional",
                "steps": [
                    {
                        "name": name,
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-12T01:00:00Z",
                        "completed_at": "2026-10-12T01:00:19Z",
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(performance, "github_api", lambda endpoint: json.dumps(response).encode())
    assert performance.github_stages("owner/repo", 20)[0]["seconds"] == 19


@pytest.mark.parametrize("timestamp", [None, True, 123, [], {}])
def test_invalid_stage_data_preserves_valid_test_report(timing, tmp_path, monkeypatch, timestamp):
    current = tmp_path / "current"
    current.mkdir()
    (current / "tools.json").write_text(json.dumps(timing), encoding="utf-8")
    response = {
        "total_count": 1,
        "jobs": [
            {
                "name": "job",
                "steps": [
                    {
                        "name": "Compile",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-12T01:00:00Z",
                        "completed_at": timestamp,
                    }
                ],
            }
        ],
    }
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(performance, "github_api", lambda endpoint: json.dumps(response).encode())
    monkeypatch.setattr(performance, "github_history", lambda *args: ([], []))
    output = tmp_path / "output"
    assert (
        performance.main(
            [
                "report",
                "--current",
                str(current),
                "--repository",
                "owner/repo",
                "--run-id",
                "20",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "Stage timings are unavailable" in (output / "report.md").read_text(encoding="utf-8")
    assert performance.read_bundle((output / "timings.json").read_bytes()) == [timing]


@pytest.mark.parametrize("count", [0, 1, 2, 3, 7])
def test_module_report_shows_its_own_bounded_sample_count(timing, count):
    history = [[copy.deepcopy(timing)] for _ in range(count)]
    timing["inventory"] = "a" * 64
    report, warnings = performance.render_report([timing], history, [])
    baseline = "15.00s" if count >= performance.MIN_SAMPLES else "-"
    assert f"| tests.slow | 15.00s | {baseline} | {min(count, performance.MAX_SAMPLES)} |" in report
    assert "| 0 | Collecting baseline" in report
    assert not warnings


def workload_fixture(timing):
    snapshots, jobs = [], []
    for index, minor in enumerate(range(11, 16)):
        entry = copy.deepcopy(timing)
        entry["id"] = f"3.{minor}-tools"
        entry["context"]["python"] = f"3.{minor}"
        snapshots.append(entry)
        jobs.append(
            {
                "name": f"Run Python tools tests (3.{minor})",
                "id": index + 1,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
                "labels": ["ubuntu-26.04"],
                "steps": [],
                "started_at": "2026-10-12T00:00:00Z",
                "completed_at": "2026-10-12T00:01:40Z",
            }
        )
    return snapshots, jobs


def test_report_cli_shares_api_inventory_and_keeps_active_report_outside_workload(
    timing, tmp_path, monkeypatch
):
    # Given complete snapshots/jobs and a reporting job still running.
    snapshots, jobs = workload_fixture(timing)
    jobs.append({"name": "Python timing report", "status": "in_progress", "steps": []})
    current = tmp_path / "current"
    current.mkdir()
    for entry in snapshots:
        (current / f"{entry['id']}.json").write_text(json.dumps(entry), encoding="utf-8")
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        if endpoint.endswith("/runs/20"):
            return json.dumps(
                {
                    "id": 20,
                    "workflow_id": 2,
                    "run_attempt": 1,
                    "event": "push",
                    "head_branch": "beta",
                }
            ).encode()
        if "/workflows/2/runs?" in endpoint:
            return b'{"workflow_runs":[]}'
        if "/runs/20/jobs?" in endpoint:
            return json.dumps({"total_count": len(jobs), "jobs": jobs}).encode()
        pytest.fail(f"Unexpected request: {endpoint}")

    monkeypatch.setattr(performance, "github_api", api)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    output = tmp_path / "output"
    # When executing the real report CLI path.
    status = performance.main(
        [
            "report",
            "--current",
            str(current),
            "--repository",
            "owner/repo",
            "--run-id",
            "20",
            "--workload",
            "python",
            "--output",
            str(output),
        ]
    )
    # Then the existing bundle/report retain tests and stages plus workload metrics.
    record = json.loads((output / "timings.json").read_bytes())
    report = (output / "report.md").read_text(encoding="utf-8")
    assert status == 0 and len(calls) == 3
    assert len(record["snapshots"]) == 5 and record["stages"] == []
    assert record["workload"]["elapsedSpanSeconds"] == 100
    assert record["workload"]["summedJobSeconds"] == 500
    assert record["workload"]["peakConcurrency"] == 5
    assert "CI stage timings" in report and "Completed CI workload" in report
    assert "telemetry is unavailable" in report


@pytest.mark.parametrize("metric", [None, "valid", "malformed", "wrong-run", "stale-attempt"])
def test_workload_history_reuses_existing_archive_and_skips_legacy_or_invalid_metrics(
    timing, monkeypatch, metric
):
    # Given one previous valid test bundle with optional workload metrics.
    snapshots, jobs = workload_fixture(timing)
    current = {
        "id": 20,
        "workflow_id": 2,
        "run_attempt": 1,
        "event": "push",
        "head_branch": "beta",
        "conclusion": "success",
    }
    previous = {**current, "id": 19}
    report = {"schema": performance.SCHEMA, "snapshots": snapshots}
    if metric is not None:
        report["workload"] = performance.ci_job_performance.observe(
            jobs, previous, ("python", snapshots)
        ).record()
        if metric == "malformed":
            report["workload"]["elapsedSpanSeconds"] = math.inf
        elif metric == "wrong-run":
            report["workload"]["runId"] = 18
        elif metric == "stale-attempt":
            previous["run_attempt"] = 2
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("timings.json", json.dumps(report))
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        if "/workflows/2/runs?" in endpoint:
            return json.dumps({"workflow_runs": [previous]}).encode()
        if "/runs/19/artifacts?" in endpoint:
            return json.dumps(
                {
                    "artifacts": [
                        {
                            "id": 31,
                            "name": performance.ARTIFACT,
                            "expired": False,
                            "size_in_bytes": len(content.getvalue()),
                        }
                    ]
                }
            ).encode()
        if endpoint.endswith("/artifacts/31/zip"):
            return content.getvalue()
        pytest.fail(f"Unexpected request: {endpoint}")

    monkeypatch.setattr(performance, "github_api", api)
    observed = []
    request = performance.ci_job_performance.WorkloadHistory("python", current, observed)
    # When loading bounded history through its existing archive reader.
    history, notes = performance.github_history("owner/repo", 20, request)
    # Then malformed/legacy workload fields do not erase valid test timing history.
    assert history == [snapshots] and len(calls) == 3
    assert len(observed) == (1 if metric == "valid" else 0)
    assert any("Skipped invalid workload" in note for note in notes) == (
        metric in {"malformed", "wrong-run", "stale-attempt"}
    )


def worker_payload(complete=True):
    return {
        "schema": 1,
        "distribution": "no",
        "expectedWorkers": 1,
        "complete": complete,
        "elapsedSeconds": 20,
        "workers": [
            {
                "id": "main",
                "testCount": 3,
                "elapsedSeconds": 20,
                "collectionSeconds": 1,
                "protocolSeconds": 15,
                "setupSeconds": 2,
                "callSeconds": 12,
                "teardownSeconds": 1,
                "firstTestSeconds": 1,
                "lastTestSeconds": 16,
                "finishedSeconds": 20,
            }
        ]
        if complete
        else [],
    }


def test_optional_worker_snapshot_ingestion_retains_compact_metrics(timing, tmp_path):
    # Given a bounded optional worker report.
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(worker_payload()), encoding="utf-8")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite time="20"><testcase name="one" time="8"/><testcase name="two" time="7"/><testcase name="skip" time="0"><skipped/></testcase></testsuite>',
        encoding="utf-8",
    )
    # When capturing the standard snapshot with its optional telemetry path.
    result = performance.snapshot(
        junit,
        python="3.15",
        suite="tools",
        runner="Linux-X64",
        workers=0,
        coverage=False,
        worker_timings=path,
    )
    # Then serial worker count and distribution remain compatible with the suite.
    assert performance.validate_snapshot(result)["workerTimings"]["complete"] is True
    assert result["context"]["distribution"] == "no"
    assert result["context"]["ordering"] == "unrecorded"

    # Changing the ordering policy starts a separate baseline without losing legacy reports.
    observed = worker_payload()
    observed["ordering"] = "collection"
    path.write_text(json.dumps(observed), encoding="utf-8")
    current = performance.snapshot(
        junit,
        python="3.15",
        suite="tools",
        runner="Linux-X64",
        workers=0,
        coverage=False,
        worker_timings=path,
    )
    assert performance.validate_snapshot(current)["context"]["ordering"] == "collection"
    assert not performance.comparable(current, result)
    assert not performance.module_comparable(current, result, "unknown")
    report, _ = performance.render_report([current], [[result]], [])
    assert "ordering: `collection`" in report


def test_incomplete_worker_metadata_remains_explicit_in_report(timing):
    # Given valid JUnit timing but incomplete optional worker observations.
    timing["workerTimings"] = worker_payload(False)
    timing["context"]["distribution"] = "no"
    performance.validate_snapshot(timing)
    # When rendering the report.
    report, _ = performance.render_report([timing], [], [])
    # Then incomplete telemetry is identified without claiming worker coverage.
    assert "incomplete" in report.lower()


@pytest.mark.parametrize("change", ["count", "workers", "distribution", "ordering"])
def test_worker_metadata_must_match_snapshot_selection(timing, change):
    # Given telemetry that claims a different test count, scheduler or worker count.
    timing["workerTimings"] = worker_payload()
    timing["context"]["distribution"] = "no"
    if change == "count":
        timing["workerTimings"]["workers"][0]["testCount"] = 4
    elif change == "workers":
        timing["context"]["workers"] = 2
    elif change == "distribution":
        timing["context"]["distribution"] = "loadfile"
    else:
        timing["context"]["ordering"] = "first-file:test_http_security.py"
    # When validating the snapshot, then mismatched optional metadata is rejected.
    with pytest.raises(ValueError, match="worker timing distribution"):
        performance.validate_snapshot(timing)


def test_compile_stages_cli_loads_matching_artifact_and_records_same_workload_metrics(
    tmp_path, monkeypatch
):
    # Given all manifest rows and a preceding compile-only bundle.
    names, _ = performance.ci_job_performance.selection("compile", [])
    jobs = [
        {
            "name": name,
            "id": index + 1,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
            "labels": ["ubuntu-26.04"],
            "steps": [
                {
                    "name": "Compile",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-12T00:00:10Z",
                    "completed_at": "2026-10-12T00:01:40Z",
                }
            ],
            "started_at": "2026-10-12T00:00:00Z",
            "completed_at": "2026-10-12T00:01:40Z",
        }
        for index, name in enumerate(sorted(names))
    ]
    run = {
        "id": 20,
        "workflow_id": 2,
        "run_attempt": 1,
        "event": "push",
        "head_branch": "beta",
        "conclusion": "success",
    }
    previous = {**run, "id": 19}
    record = {
        "schema": 1,
        "snapshots": [],
        "workload": performance.ci_job_performance.observe(
            jobs, previous, ("compile", [])
        ).record(),
    }
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("timings.json", json.dumps(record))
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        if endpoint.endswith("/runs/20"):
            return json.dumps(run).encode()
        if "/workflows/2/runs?" in endpoint:
            return json.dumps({"workflow_runs": [previous]}).encode()
        if "/runs/19/artifacts?" in endpoint:
            assert "name=compile-stage-timings" in endpoint
            return json.dumps(
                {
                    "artifacts": [
                        {
                            "id": 31,
                            "name": "compile-stage-timings",
                            "expired": False,
                            "size_in_bytes": len(content.getvalue()),
                        }
                    ]
                }
            ).encode()
        if endpoint.endswith("/artifacts/31/zip"):
            return content.getvalue()
        if "/runs/20/jobs?" in endpoint:
            return json.dumps({"total_count": len(jobs), "jobs": jobs}).encode()
        pytest.fail(f"Unexpected request: {endpoint}")

    monkeypatch.setattr(performance, "github_api", api)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    # When running the real compile stage reporting command.
    status = performance.main(
        [
            "stages",
            "--repository",
            "owner/repo",
            "--run-id",
            "20",
            "--workload",
            "compile",
            "--output",
            str(tmp_path),
        ]
    )
    # Then the same workload metrics appear alongside completed compile steps.
    saved = json.loads((tmp_path / "timings.json").read_bytes())
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert status == 0 and len(calls) == 5
    assert saved["snapshots"] == [] and len(saved["stages"]) == len(names)
    assert saved["workload"]["summedJobSeconds"] == len(names) * 100
    assert saved["workload"]["elapsedSpanSeconds"] == 100
    assert "Completed CI workload" in report and "Collecting baseline" in report


@pytest.mark.parametrize("payload", [b"[]", b"{", b'{"schema":1,"snapshots":[]}'])
def test_malformed_or_legacy_local_histories_keep_report_warning_only(
    timing, tmp_path, monkeypatch, payload
):
    # Given valid current snapshots and malformed or legacy optional history.
    current = tmp_path / "current"
    current.mkdir()
    (current / "tools.json").write_text(json.dumps(timing), encoding="utf-8")
    history = tmp_path / "history.json"
    history.write_bytes(payload)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    output = tmp_path / "output"
    # When producing a local report with workload history enabled.
    status = performance.main(
        [
            "report",
            "--current",
            str(current),
            "--history",
            str(history),
            "--workload",
            "python",
            "--output",
            str(output),
        ]
    )
    # Then invalid histories cannot turn the observational report into a failed gate.
    assert status == 0
    assert performance.read_bundle((output / "timings.json").read_bytes()) == [timing]
