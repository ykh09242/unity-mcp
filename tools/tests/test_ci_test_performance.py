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
