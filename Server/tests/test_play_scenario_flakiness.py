"""Offline analysis preserves failures and rejects ambiguous or unsafe visual evidence."""

import json
from pathlib import Path
import socket
import subprocess

import click
from click.testing import CliRunner
import pytest

from cli.commands.play_scenario_analysis import register_analysis_commands
from cli.utils.play_scenario_flakiness import analyze_flakiness


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline analysis attempted network or subprocess access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(
        "cli.commands.play_scenario_analysis.get_config",
        lambda: type("Config", (), {"format": "json"})(),
    )


@pytest.fixture
def command_group():
    group = click.Group()
    register_analysis_commands(group)
    return group


def write_json(path, content):
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def report(job_id="run-a", states=("passed",), **changes):
    value = {
        "job_id": job_id,
        "status": "succeeded" if all(state == "passed" for state in states) else "failed",
        "repeat_count": len(states),
        "execution_environment": "editor",
        "reproduction": {
            "definition_hash": "a" * 64,
            "unity_version": "6000.0",
            "package_version": "1.0",
            "environment": {
                "schema_version": 1,
                "operating_system": "Windows 11",
                "platform": "WindowsEditor",
                "architecture": "x64",
                "scripting_backend": "mono",
                "build_mode": "editor",
                "runtime_version": "6.0",
            },
        },
        "iteration_results_version": 1,
        "iteration_results": [
            {
                "iteration": index,
                "status": state,
                "started_unix_ms": None if state == "skipped" else index * 100,
                "finished_unix_ms": index * 100 + 20,
            }
            for index, state in enumerate(states, 1)
        ],
        "steps": [],
    }
    value.update(changes)
    return value


def test_intermitten_failure_preserved_with_attempt_denominator(tmp_path):
    failure = {
        "stage": "main",
        "iteration": 1,
        "step_index": 0,
        "code": "TARGET_TIMEOUT",
        "message": "<script>unsafe</script>",
    }
    first = write_json(
        tmp_path / "first.json",
        report(states=("failed", "timed_out", "cancelled", "skipped"), failure=failure),
    )
    later = write_json(tmp_path / "later.json", report(job_id="run-b"))
    result = analyze_flakiness((first, later))
    group = result.groups[0]
    assert result.status == "analyzed"
    assert group.intermittent
    assert group.counts["passed"] == 1
    assert group.counts["skipped"] == 1
    assert group.failure_rate_denominator == 4
    assert group.failure_rate == 0.5
    assert group.original_outcomes[0].failure.message == failure["message"]
    assert group.timeout_durations_ms == [20]
    assert group.failed_steps[0].code == "TARGET_TIMEOUT"


def test_incompatible_environments_do_not_establish_intermittency(tmp_path):
    first = write_json(tmp_path / "first.json", report(states=("failed",)))
    second = write_json(
        tmp_path / "second.json", report(job_id="run-b", execution_environment="player")
    )
    result = analyze_flakiness((first, second))
    assert len(result.groups) == 2
    assert all(not group.intermittent for group in result.groups)


def test_duplicates_and_unsupported_evidence_are_explicit(tmp_path):
    source = write_json(tmp_path / "run.json", report())
    malformed = tmp_path / "outcomes.jsonl"
    malformed.write_text("{}\n{}\n", encoding="utf-8")
    result = analyze_flakiness((source, source, malformed))
    assert result.status == "incomplete"
    assert result.groups[0].counts["passed"] == 1
    assert len(result.unsupported_inputs) == 2


def test_missing_compatibility_is_separate_and_incomplete(tmp_path):
    sources = tuple(
        write_json(tmp_path / f"{index}.json", report(job_id=f"run-{index}", reproduction={}))
        for index in range(2)
    )
    result = analyze_flakiness(sources)
    assert result.status == "incomplete"
    assert len(result.groups) == 2


def test_flakiness_cli_retains_partial_json_and_exit_two(command_group, tmp_path):
    source = write_json(tmp_path / "run.json", report())
    result = CliRunner().invoke(
        command_group, ["analyze-flakiness", str(source), str(tmp_path / "missing.json")]
    )
    assert result.exit_code == 2
    assert json.loads(result.output)["groups"][0]["counts"]["passed"] == 1


def test_native_nested_environment_changes_partition_failure_history(tmp_path):
    failed = report(states=("failed",))
    passed = report(job_id="run-linux")
    passed["reproduction"]["environment"].update(operating_system="Linux", platform="LinuxEditor")
    sources = (
        write_json(tmp_path / "failed.json", failed),
        write_json(tmp_path / "passed.json", passed),
    )
    result = analyze_flakiness(sources)
    assert result.status == "analyzed"
    assert len(result.groups) == 2
    assert all(not group.intermittent for group in result.groups)


@pytest.mark.parametrize("missing", ["absent", "null", "unknown"])
def test_native_unknown_environment_retains_counts_but_cannot_merge(tmp_path, missing):
    first, later = report(states=("failed",)), report(job_id="later")
    for raw in (first, later):
        environment = raw["reproduction"].pop("environment")
        if missing == "null":
            raw["reproduction"]["environment"] = None
        elif missing == "unknown":
            environment["operating_system"] = None
            raw["reproduction"]["environment"] = environment
    sources = (
        write_json(tmp_path / "first.json", first),
        write_json(tmp_path / "later.json", later),
    )
    result = analyze_flakiness(sources)
    assert result.status == "incomplete"
    assert len(result.groups) == 2
    assert sum(group.counts["passed"] for group in result.groups) == 1
    assert sum(group.counts["failed"] for group in result.groups) == 1


def test_native_soak_parent_failure_cannot_be_hidden_by_selected_success(tmp_path):
    source = write_json(tmp_path / "run.json", report(soak={"status": "timed_out", "exit_code": 2}))
    result = analyze_flakiness((source,))
    assert result.status == "incomplete"
    assert result.groups == []
    assert "Soak parent" in result.unsupported_inputs[0]["error"]


def test_failure_codes_are_matched_to_native_step_identity(tmp_path):
    value = report(
        states=("timed_out",),
        failure={"stage": "main", "iteration": 1, "step_index": 0, "code": "TARGET_TIMEOUT"},
        cleanup_failures=[
            {"stage": "cleanup", "iteration": 1, "step_index": 0, "code": "CLEANUP_QUERY_FAILED"}
        ],
        steps=[
            {
                "stage": "main",
                "iteration": 1,
                "step_index": 0,
                "name": "Start",
                "status": "timed_out",
            },
            {
                "stage": "cleanup",
                "iteration": 1,
                "step_index": 0,
                "name": "Cleanup",
                "status": "failed",
            },
            {
                "stage": "cleanup",
                "iteration": 1,
                "step_index": 1,
                "name": "Other cleanup",
                "status": "failed",
            },
        ],
    )
    source = write_json(tmp_path / "run.json", value)
    result = analyze_flakiness((source,))
    identities = {
        (failure.stage, failure.step_index): failure.code
        for failure in result.groups[0].failed_steps
    }
    assert identities == {
        ("main", 0): "TARGET_TIMEOUT",
        ("cleanup", 0): "CLEANUP_QUERY_FAILED",
        ("cleanup", 1): None,
    }
    assert result.groups[0].original_outcomes[0].cleanup_failures[0].code == "CLEANUP_QUERY_FAILED"
