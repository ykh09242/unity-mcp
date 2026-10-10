"""Offline saved-run comparisons must never manufacture completion or zero query cost."""

import copy
import json
import os
import stat
from types import SimpleNamespace
import socket
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection
from models.play_scenarios import PlayScenario


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """Any transport, credential lookup or child process is an unexpected side effect."""

    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline comparison attempted a connection, credential read or process")

    monkeypatch.setattr(connection, "read_local_auth_token", forbidden)
    monkeypatch.setattr(connection, "run_command", forbidden)
    monkeypatch.setattr("cli.commands.play_scenario.run_command", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def native_report(*, environment="editor", repeat=2, searches=0, visits=0):
    """Match Create/AddSteps: all three stages per iteration, native null options retained."""
    definition = PlayScenario.model_validate(
        {
            "name": "menu-start",
            "setup_steps": [
                {"name": "Setup", "action": "load_scene", "scene": "Assets/Menu.unity"}
            ],
            "steps": [
                {"name": "Player", "action": "wait_object", "target": "Player", "active": False}
            ],
            "cleanup_steps": [
                {"name": "Cleanup", "action": "wait_scene", "scene": "Assets/Menu.unity"}
            ],
        }
    ).model_dump(mode="json")
    report = {
        "job_id": "a" * 32,
        "scenario": definition,
        "execution_environment": environment,
        "status": "succeeded",
        "phase": "finished",
        "repeat_count": repeat,
        "iteration_results_version": 1,
        "iteration_results": [],
        "started_unix_ms": 1000,
        "finished_unix_ms": 1000 + repeat * 100,
        "runner_resources_released": True,
        "error": None,
        "report_error": None,
        "pending_status": None,
        "pending_error": None,
        "cleanup_error": None,
        "failure": None,
        "cleanup_failures": [],
        "unexpected_log_count": 0,
        "unexpected_log_error": None,
        "last_unexpected_log_error": None,
        "resource_checks": [],
        "query_counts": {"target_searches": searches, "hierarchy_visits": visits},
        "reproduction": {
            "definition_hash": "a" * 64,
            "unity_version": "6000.0.84f1",
            "package_version": "1.0",
            "source_revision": "baseline",
        },
        "steps": [],
    }
    for iteration in range(1, repeat + 1):
        start = 1000 + (iteration - 1) * 100
        report["iteration_results"].append(
            {
                "iteration": iteration,
                "status": "passed",
                "started_unix_ms": start,
                "finished_unix_ms": start + 100,
            }
        )
        for stage_index, (stage, field) in enumerate(
            (("setup", "setup_steps"), ("main", "steps"), ("cleanup", "cleanup_steps"))
        ):
            for index, step in enumerate(definition[field]):
                report["steps"].append(
                    {
                        "stage": stage,
                        "iteration": iteration,
                        "step_index": index,
                        "name": step["name"],
                        "action": step["action"],
                        "status": "passed",
                        "started_unix_ms": start + stage_index * 20,
                        "finished_unix_ms": start + stage_index * 20 + 10,
                        "poll_count": 1,
                    }
                )
    if environment == "player":
        report.update(
            player_schema_version=2,
            finalization_state="completed",
            exit_code=0,
            timeout_seconds=300,
            progress_error=None,
        )
    return report


def compare_files(tmp_path, baseline, candidate, *, limits=(0, 0), output="json"):
    """Invoke the public command and prove that both supplied files remain unchanged."""
    paths = (tmp_path / "baseline.json", tmp_path / "candidate.json")
    for path, report in zip(paths, (baseline, candidate)):
        path.write_text(json.dumps(report), encoding="utf-8")
    before = [path.read_bytes() for path in paths]
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output,
            "play-scenario",
            "compare-queries",
            *map(str, paths),
            "--max-target-searches-increase",
            str(limits[0]),
            "--max-hierarchy-visits-increase",
            str(limits[1]),
        ],
    )
    assert [path.read_bytes() for path in paths] == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["baseline.json", "candidate.json"]
    return result


@pytest.mark.parametrize("environment", ["editor", "player"])
def test_zero_query_counts_are_a_complete_offline_comparison(tmp_path, environment):
    baseline = native_report(environment=environment)
    candidate = copy.deepcopy(baseline)
    candidate["job_id"] = "b" * 32
    result = compare_files(tmp_path, baseline, candidate)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1
    assert data["status"] == "within_budget"
    assert data["queries"]["target_searches"] == {
        "baseline": 0,
        "candidate": 0,
        "delta": 0,
        "max_increase": 0,
        "within_budget": True,
    }
    assert data["candidate"]["job_id"] == "b" * 32
    assert result.stderr == ""


def test_query_increase_beyond_limit_returns_nonzero_and_all_values(tmp_path):
    result = compare_files(
        tmp_path,
        native_report(searches=10, visits=100),
        native_report(searches=13, visits=104),
        limits=(2, 4),
    )
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "budget_exceeded"
    assert data["queries"]["target_searches"] == {
        "baseline": 10,
        "candidate": 13,
        "delta": 3,
        "max_increase": 2,
        "within_budget": False,
    }
    assert data["queries"]["hierarchy_visits"]["within_budget"] is True


def test_unattempted_repeat_cannot_be_a_passing_candidate(tmp_path):
    candidate = native_report()
    candidate["iteration_results"][1].update(status="skipped", started_unix_ms=None)
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["status"] == "not_comparable"
    assert "candidate" in data["error"]
    assert "queries" not in data


def test_equal_recorded_hash_does_not_hide_changed_definition(tmp_path):
    candidate = native_report()
    candidate["scenario"]["steps"][0]["active"] = True
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["status"] == "not_comparable"
    assert "definition" in data["error"]


@pytest.mark.parametrize(
    "counts,limits",
    [
        ((10, 20, 8, 22), (0, 2)),
        ((0, 0, 0, 0), (0, 0)),
        ((2**63 - 1, 0, 0, 2**63 - 1), (0, 2**63 - 1)),
    ],
)
def test_inclusive_thresholds_and_signed_decreases(tmp_path, counts, limits):
    left_searches, left_visits, right_searches, right_visits = counts
    result = compare_files(
        tmp_path,
        native_report(searches=left_searches, visits=left_visits),
        native_report(searches=right_searches, visits=right_visits),
        limits=limits,
    )
    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.stdout)["queries"]["target_searches"]["delta"]
        == right_searches - left_searches
    )


def set_field(report, field, value):
    """Change one real native report field to reproduce an input boundary violation."""
    parts = field.split(".")
    node = report
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    node[parts[-1]] = value


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("status", "timed_out"),
        ("status", "cancelled"),
        ("status", "running"),
        ("phase", "cleaning"),
        ("runner_resources_released", False),
        ("runner_resources_released", 1),
        ("finished_unix_ms", None),
        ("finished_unix_ms", 999),
        ("started_unix_ms", True),
        ("repeat_count", True),
        ("job_id", "unknown"),
        ("error", "run failed"),
        ("error", False),
        ("report_error", "export failed"),
        ("pending_error", "failed"),
        ("pending_status", "failed"),
        ("cleanup_error", "cleanup failed"),
        ("cleanup_failures", [{"code": "action_exception"}]),
        ("failure", {"code": "query_budget_exceeded"}),
        ("iteration_results_version", 2),
        ("iteration_results_version", True),
        ("iteration_results.0.status", "running"),
        ("iteration_results.1.iteration", 1),
        ("iteration_results.1.started_unix_ms", 1050),
        ("iteration_results.0.finished_unix_ms", 1300),
        ("steps.0.status", "skipped"),
        ("steps.0.started_unix_ms", None),
        ("steps.0.finished_unix_ms", 999),
        ("steps.0.finished_unix_ms", 1201),
        ("steps.0.step_index", True),
        ("steps.0.poll_count", 0),
        ("steps.0.poll_count", False),
        ("steps.0.poll_count", 1.0),
        ("steps.0.poll_count", "1"),
        ("steps.0.poll_count", -1),
        ("steps.0.poll_count", 2**63),
        ("steps.0.name", "Different"),
        ("steps.0.action", "wait_object"),
        ("steps.0.stage", "unknown"),
        ("steps.0.iteration", 3),
        ("query_counts.target_searches", None),
        ("query_counts.target_searches", False),
        ("query_counts.target_searches", 0.0),
        ("query_counts.target_searches", "0"),
        ("query_counts.target_searches", -1),
        ("query_counts.target_searches", 2**63),
        ("query_counts.hierarchy_visits", None),
        ("query_counts.hierarchy_visits", True),
        ("query_counts.hierarchy_visits", -1),
        ("query_counts.hierarchy_visits", 2**63),
        ("reproduction.definition_hash", "missing"),
        ("reproduction.unity_version", ""),
        ("reproduction.package_version", ""),
    ],
)
def test_invalid_evidence_on_either_side_never_passes(tmp_path, side, field, value):
    reports = {"baseline": native_report(), "candidate": native_report()}
    set_field(reports[side], field, value)
    result = compare_files(tmp_path, reports["baseline"], reports["candidate"])
    assert result.exit_code == 2, result.output
    assert result.stdout.startswith("{"), result.output
    data = json.loads(result.stdout)
    assert data["status"] == "not_comparable"
    assert side in data["error"]
    assert "queries" not in data


@pytest.mark.parametrize(
    "missing",
    [
        "query_counts",
        "iteration_results",
        "iteration_results_version",
        "reproduction",
        "steps",
        "runner_resources_released",
        "phase",
        "error",
        "report_error",
        "failure",
        "cleanup_failures",
    ],
)
def test_missing_evidence_is_not_an_implicit_zero_or_success(tmp_path, missing):
    candidate = native_report()
    candidate.pop(missing)
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "reordered", "extra"])
def test_step_identity_partition_is_complete_and_ordered(tmp_path, corruption):
    candidate = native_report()
    if corruption == "missing":
        candidate["steps"].pop()
    elif corruption == "duplicate":
        candidate["steps"][1] = copy.deepcopy(candidate["steps"][0])
    elif corruption == "reordered":
        candidate["steps"][0], candidate["steps"][1] = candidate["steps"][1], candidate["steps"][0]
    else:
        candidate["steps"].append(copy.deepcopy(candidate["steps"][0]))
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"


@pytest.mark.parametrize(
    "field,value",
    [
        ("player_schema_version", 3),
        ("player_schema_version", True),
        ("finalization_state", "pending"),
        ("exit_code", 1),
        ("exit_code", False),
        ("progress_error", "journal failed"),
    ],
)
def test_player_finalization_failures_never_pass(tmp_path, field, value):
    candidate = native_report(environment="player")
    candidate[field] = value
    result = compare_files(tmp_path, native_report(environment="player"), candidate)
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"


@pytest.mark.parametrize(
    "field,value",
    [("reproduction.definition_hash", "b" * 64), ("reproduction.unity_version", "6000.0.83f1")],
)
def test_incompatible_metadata_is_explained(tmp_path, field, value):
    candidate = native_report()
    set_field(candidate, field, value)
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    assert field.split(".")[-1] in json.loads(result.stdout)["error"]


@pytest.mark.parametrize(
    "candidate", [native_report(environment="player"), native_report(repeat=3)]
)
def test_different_execution_environment_or_repeat_plan_is_not_comparable(tmp_path, candidate):
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"


def test_changed_source_and_package_labels_are_visible_without_failing_comparison(tmp_path):
    candidate = native_report()
    candidate["reproduction"].update(package_version="2.0", source_revision="candidate")
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["baseline"]["source_revision"] == "baseline"
    assert data["candidate"]["source_revision"] == "candidate"
    assert data["candidate"]["package_version"] == "2.0"


def test_false_and_zero_definitions_are_not_equal(tmp_path):
    baseline, candidate = native_report(), native_report()
    for report, expected in ((baseline, False), (candidate, 0)):
        report["scenario"]["steps"][0] = {
            "name": "Player",
            "action": "wait_state",
            "state_id": "state",
            "state_equals": expected,
        }
        for step in report["steps"]:
            if step["stage"] == "main":
                step["action"] = "wait_state"
    result = compare_files(tmp_path, baseline, candidate)
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    assert "definition" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("output", ["text", "table"])
def test_human_output_keeps_both_counters_and_exceeded_budget(tmp_path, output):
    result = compare_files(
        tmp_path,
        native_report(searches=123, visits=456),
        native_report(searches=789, visits=555),
        output=output,
    )
    assert result.exit_code == 1, result.output
    for expected in (
        "budget_exceeded",
        "target_searches",
        "hierarchy_visits",
        "123",
        "789",
        "666",
        "456",
        "555",
        "99",
    ):
        assert expected in result.stdout


@pytest.mark.parametrize("limits", [(-1, 0), (0, -1), (2**63, 0), ("false", 0), ("1.5", 0)])
def test_invalid_limits_keep_click_argument_failure_convention(tmp_path, limits):
    result = compare_files(tmp_path, native_report(), native_report(), limits=limits)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--max-" in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "{}",
        '{"success":true,"data":{}}',
        '{"schema_version":2,"outcomes":[]}',
        '{"status":"succeeded","status":"succeeded"}',
        '{"unused":NaN}',
        '{"unused":Infinity}',
        '{"unused":1e400}',
        '{"unused":' + "[" * 33 + "0" + "]" * 33 + "}",
        '{"truncated":',
        "x" * (2097152 + 1),
    ],
    ids=[
        "array",
        "object",
        "envelope",
        "summary",
        "duplicate",
        "nan",
        "infinity",
        "overflow",
        "depth",
        "syntax",
        "size",
    ],
)
def test_bounded_parser_rejects_unsupported_or_ambiguous_input(tmp_path, payload):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(json.dumps(native_report()), encoding="utf-8")
    candidate.write_text(payload, encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-queries",
            str(baseline),
            str(candidate),
            "--max-target-searches-increase",
            "0",
            "--max-hierarchy-visits-increase",
            "0",
        ],
    )
    assert result.exit_code == 2
    assert result.stdout.startswith("{"), result.output
    data = json.loads(result.stdout)
    assert data["status"] == "not_comparable"
    assert "candidate" in data["error"]
    assert len(result.stdout) < 1024


def test_embedded_paths_and_brackets_inside_strings_are_not_followed(tmp_path):
    candidate = native_report()
    candidate["report_path"] = "missing-do-not-read/run.json"
    candidate["unused"] = "[" * 100 + '{"nested":true}'
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 99),
        ("unexpected_log_count", 1),
        ("unexpected_log_count", False),
        ("unexpected_log_error", "late failure"),
        ("last_unexpected_log_error", "late failure"),
    ],
)
def test_contradictory_success_or_unsupported_root_version_is_rejected(tmp_path, field, value):
    candidate = native_report()
    candidate[field] = value
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"


def resource_report():
    report = native_report()
    report["scenario"]["resources"]["enabled"] = True
    report["resource_checks"] = [
        {
            "iteration": row["iteration"],
            "baseline_unix_ms": row["started_unix_ms"],
            "checked_unix_ms": row["finished_unix_ms"] - 10,
            "passed": True,
            "error": None,
            "new_scriptable_objects": 0,
            "new_subscriptions": 0,
            "new_handles": 0,
        }
        for row in report["iteration_results"]
    ]
    return report


@pytest.mark.parametrize(
    "corruption",
    ["none", "missing", "duplicate", "failure", "numeric_bool", "error", "time", "over_budget"],
)
def test_resource_assertions_must_be_complete_and_successful(tmp_path, corruption):
    candidate = resource_report()
    if corruption == "missing":
        candidate["resource_checks"].pop()
    elif corruption == "duplicate":
        candidate["resource_checks"][1]["iteration"] = 1
    elif corruption == "failure":
        candidate["resource_checks"][0]["passed"] = False
    elif corruption == "numeric_bool":
        candidate["resource_checks"][0]["passed"] = 1
    elif corruption == "error":
        candidate["resource_checks"][0]["error"] = "capture failed"
    elif corruption == "time":
        candidate["resource_checks"][0]["checked_unix_ms"] = 999
    elif corruption == "over_budget":
        candidate["resource_checks"][0]["new_handles"] = 1
    result = compare_files(tmp_path, resource_report(), candidate)
    assert result.exit_code == (0 if corruption == "none" else 2), result.output


def test_a_claimed_success_cannot_exceed_its_own_native_query_budget(tmp_path):
    baseline = native_report(searches=3)
    baseline["scenario"]["query_budget"].update(enabled=True, max_target_searches=2)
    result = compare_files(tmp_path, baseline, copy.deepcopy(baseline))
    assert result.exit_code == 2, result.output
    assert "baseline" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("missing", ["target_searches", "hierarchy_visits"])
def test_missing_individual_counter_never_defaults_to_zero(tmp_path, missing):
    candidate = native_report()
    candidate["query_counts"].pop(missing)
    result = compare_files(tmp_path, native_report(), candidate)
    assert result.exit_code == 2
    assert missing in json.loads(result.stdout)["error"]


def test_two_input_files_are_opened_exactly_once(tmp_path, monkeypatch):
    actual_open, opened = os.open, []

    def observed_open(path, *args, **kwargs):
        opened.append(Path(path))
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", observed_open)
    result = compare_files(tmp_path, native_report(), native_report())
    assert result.exit_code == 0, result.output
    assert opened == [tmp_path / "baseline.json", tmp_path / "candidate.json"]


@pytest.mark.parametrize("kind", ["missing", "directory", "denied", "bad_utf8"])
def test_unreadable_inputs_return_bounded_structured_errors(tmp_path, monkeypatch, kind):
    path = tmp_path / "report.json"
    if kind == "directory":
        path.mkdir()
    elif kind in {"denied", "bad_utf8"}:
        path.write_bytes(b"\xff" if kind == "bad_utf8" else b"{}")
    if kind == "denied":

        def denied(*_args, **_kwargs):
            raise PermissionError("fixture")

        monkeypatch.setattr(os, "open", denied)
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-queries",
            str(path),
            str(path),
            "--max-target-searches-increase",
            "0",
            "--max-hierarchy-visits-increase",
            "0",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["status"] == "not_comparable"
    assert len(result.stdout) < 1024


@pytest.mark.parametrize("link_kind", ["file", "ancestor"])
def test_links_are_rejected_without_opening_a_target(tmp_path, monkeypatch, link_kind):
    from cli.utils.play_scenario_query_compare import QueryComparisonError, read_report

    directory = tmp_path / "source"
    directory.mkdir()
    source = directory / "report.json"
    source.write_text(json.dumps(native_report()), encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(
            source if link_kind == "file" else directory,
            target_is_directory=link_kind == "ancestor",
        )
    except OSError as exc:
        pytest.skip(
            f"Host cannot create a symbolic link: {exc.winerror if hasattr(exc, 'winerror') else exc.errno}"
        )

    def forbidden(*_args, **_kwargs):
        pytest.fail("A linked target was opened")

    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(QueryComparisonError):
        read_report(link if link_kind == "file" else link / source.name, "candidate")


@pytest.mark.parametrize("kind", ["file_reparse", "parent_reparse", "special_file"])
def test_reparse_and_special_files_are_rejected_before_open(tmp_path, monkeypatch, kind):
    from cli.utils.play_scenario_query_compare import QueryComparisonError, read_report

    source = tmp_path / "report.json"
    source.write_text("{}", encoding="utf-8")
    original = Path.lstat
    marker = tmp_path if kind == "parent_reparse" else source

    def marked_lstat(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path != marker:
            return actual
        return SimpleNamespace(
            st_mode=stat.S_IFIFO if kind == "special_file" else actual.st_mode,
            st_file_attributes=0 if kind == "special_file" else stat.FILE_ATTRIBUTE_REPARSE_POINT,
            st_size=actual.st_size,
        )

    def forbidden(*_args, **_kwargs):
        pytest.fail("A reparse point or special file was opened")

    monkeypatch.setattr(Path, "lstat", marked_lstat)
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(QueryComparisonError):
        read_report(source, "candidate")


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("iteration", [1, 2])
@pytest.mark.parametrize("timing", ["baseline_after_start", "check_before_finish", "equal"])
def test_resource_measurements_enclose_every_executed_step(tmp_path, side, iteration, timing):
    reports = {"baseline": resource_report(), "candidate": resource_report()}
    report = reports[side]
    steps = [step for step in report["steps"] if step["iteration"] == iteration]
    check = report["resource_checks"][iteration - 1]
    check["baseline_unix_ms"] = steps[0]["started_unix_ms"]
    check["checked_unix_ms"] = steps[-1]["finished_unix_ms"]
    if timing == "baseline_after_start":
        check["baseline_unix_ms"] += 1
    elif timing == "check_before_finish":
        check["checked_unix_ms"] -= 1
    result = compare_files(tmp_path, reports["baseline"], reports["candidate"])
    assert result.exit_code == (0 if timing == "equal" else 2), result.output


@pytest.mark.parametrize("absent_stage", ["setup", "cleanup", "both"])
def test_resource_measurements_follow_actual_steps_without_optional_stages(tmp_path, absent_stage):
    report = resource_report()
    stages = {"setup", "cleanup"} if absent_stage == "both" else {absent_stage}
    if "setup" in stages:
        first = report["scenario"]["setup_steps"][0]
        report["scenario"]["steps"] = [first]
        for step in report["steps"]:
            if step["stage"] == "main":
                step.update(name=first["name"], action=first["action"])
    for stage in stages:
        report["scenario"][f"{stage}_steps"] = []
    report["steps"] = [step for step in report["steps"] if step["stage"] not in stages]
    for check in report["resource_checks"]:
        steps = [step for step in report["steps"] if step["iteration"] == check["iteration"]]
        check["baseline_unix_ms"] = steps[0]["started_unix_ms"]
        check["checked_unix_ms"] = steps[-1]["finished_unix_ms"]
    result = compare_files(tmp_path, report, copy.deepcopy(report))
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "source",
    [
        r"\\untrusted-host\share\run.json",
        "//untrusted-host/share/run.json",
        r"\\?\UNC\untrusted-host\share\run.json",
        r"\\?\C:\reports\run.json",
        r"\\.\C:\reports\run.json",
        r"\??\UNC\untrusted-host\share\run.json",
    ],
    ids=["unc", "slash_unc", "extended_unc", "extended_drive", "device", "nt_namespace"],
)
def test_remote_and_device_namespaces_are_rejected_before_filesystem_access(monkeypatch, source):
    from cli.utils.play_scenario_query_compare import QueryComparisonError, read_report

    def forbidden(*_args, **_kwargs):
        pytest.fail("An unsupported namespace reached a filesystem operation")

    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(QueryComparisonError):
        read_report(Path(source), "candidate")


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_linked_ancestors_are_rejected_before_descendant_stat(tmp_path, monkeypatch, kind):
    from cli.utils.play_scenario_query_compare import QueryComparisonError, read_report

    marker = tmp_path / "linked"
    source = marker / "nested" / "run.json"
    original = Path.lstat

    def guarded_lstat(path, *args, **kwargs):
        if path == marker:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFDIR,
                st_file_attributes=0 if kind == "symlink" else stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        if marker in path.parents:
            pytest.fail("A filesystem operation traversed an unchecked linked ancestor")
        return original(path, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A linked ancestor reached a file open")

    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(QueryComparisonError):
        read_report(source, "candidate")


def test_cli_defers_all_input_io_to_the_report_loader(tmp_path, monkeypatch):
    paths = [tmp_path / "baseline.json", tmp_path / "candidate.json"]
    loaded = []
    original = os.stat

    def guarded_stat(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)) and Path(path) in paths:
            pytest.fail("CLI argument parsing probed an input before report admission")
        return original(path, *args, **kwargs)

    def loader(path, label):
        loaded.append((path, label))
        return native_report()

    monkeypatch.setattr(os, "stat", guarded_stat)
    monkeypatch.setattr("cli.commands.play_scenario.read_report", loader)
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-queries",
            *map(str, paths),
            "--max-target-searches-increase",
            "0",
            "--max-hierarchy-visits-increase",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert loaded == list(zip(paths, ["baseline", "candidate"]))
