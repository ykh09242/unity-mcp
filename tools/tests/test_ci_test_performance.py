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
