"""Actual supported flow commands on isolated programs; no provider or native claims."""

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from tools.tests.test_feature_program import HEAD, fp, make_small_program


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "git", lambda _repo, args: "" if args[0] == "status" else HEAD)
    return make_small_program(tmp_path / "repo", "flow-fixture", ("S01", "S02"))


def output(fixture, capsys, *argv):
    capsys.readouterr()
    assert fixture.run(*argv) == 0
    captured = capsys.readouterr()
    assert not captured.err
    return json.loads(captured.out)


def declared_inputs(fixture):
    return fixture.write(
        "evidence/S01/attempt-1/manifest.json",
        {
            "schema_version": 1,
            "checks": ["scoped_tests"],
            "groups": {
                "source": [{"path": "src/S01.py", "kind": "file", "required": True}],
                "test": [],
                "config": [],
            },
            "artifact_refs": [],
            "completeness": {
                "status": "complete",
                "reason": "Exact synthetic scope inspected",
                "unknowns": [],
                "evidence_refs": [],
            },
        },
    )


def test_cli_precise_inputs_ignore_unrelated_source_and_detect_actual_changed_bytes(
    fixture, capsys
):
    (fixture.program.repo / "src/S01.py").write_bytes(b"original = True\n")
    manifest = declared_inputs(fixture)
    environment = fixture.write(
        "environment.json", {"facts": {"fixture": "synthetic"}, "unknowns": []}
    )
    method = fixture.write(
        "method.json", {"procedure": "inert procedure", "execution_kind": "test"}
    )
    before = output(
        fixture,
        capsys,
        "inputs-capture",
        "--manifest",
        manifest,
        "--environment",
        environment,
        "--method",
        method,
    )
    (fixture.program.repo / "src/S02.py").write_bytes(b"unrelated = 2\r\n")
    unrelated = output(
        fixture,
        capsys,
        "inputs-capture",
        "--manifest",
        manifest,
        "--environment",
        environment,
        "--method",
        method,
    )
    assert before["input_digest"] == unrelated["input_digest"]
    (fixture.program.repo / "src/S01.py").write_bytes(b"original = True\r\n")
    after = output(
        fixture,
        capsys,
        "inputs-capture",
        "--manifest",
        manifest,
        "--environment",
        environment,
        "--method",
        method,
    )
    assert before["input_digest"] != after["input_digest"]
    left, right = fixture.write("before.json", before), fixture.write("after.json", after)
    comparison = output(fixture, capsys, "inputs-compare", "--before", left, "--after", right)
    assert comparison["status"] == "changed"
    assert comparison["affected_checks"] == ["scoped_tests"]


def stage(identifier, reason, owner="worker"):
    return {
        "id": identifier,
        "operation": "note",
        "observed_at": "2026-10-14T10:00:00+00:00",
        "payload": {
            "id": "S01",
            "owner": owner,
            "reason": reason,
            "next_action": "Inspect synthetic record",
            "evidence": [],
        },
    }


def plan_request(fixture, stages):
    return fixture.write(
        "closure-request.json", {"owner_id": "worker", "scope": ["src/S01.py"], "stages": stages}
    )


def test_full_sequence_preflight_rejects_later_invalid_stage_before_any_commit(
    fixture, capsys, monkeypatch
):
    assert fixture.claim() == 0
    original = fixture.program.path("backlog.json").read_bytes()
    request = plan_request(
        fixture, [stage("first", "Valid note"), stage("second", "Wrong actor", "other")]
    )
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("Preparation executed a process")
    )
    capsys.readouterr()
    assert fixture.run("closure-prepare", "--input", request) == 1
    assert capsys.readouterr().err
    assert fixture.program.path("backlog.json").read_bytes() == original


def test_exact_partial_resume_publishes_only_remaining_normal_records(fixture, capsys, monkeypatch):
    assert fixture.claim() == 0
    request = plan_request(fixture, [stage("first", "First note"), stage("second", "Second note")])
    plan = output(fixture, capsys, "closure-prepare", "--input", request)
    plan_path = fixture.write("evidence/S01/attempt-1/closure-plan.json", plan)
    host = fp.authoring.ClosureHost(fixture.program)
    first = plan["stages"][0]
    assert host.publish(first, first["before_revision"], "worker")["status"] == "committed"
    events_after_first = len(fixture.program.load()["events"])
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("Recovery executed a process")
    )
    result = output(
        fixture,
        capsys,
        "closure-resume",
        "--plan",
        plan_path,
        "--owner",
        "worker",
        "--expected-revision",
        first["after_revision"],
    )
    assert result["status"] == "published"
    final = fixture.program.load()
    assert len(final["events"]) == events_after_first + len(plan["stages"][1]["appended_events"])
    assert result["execution_replayed"] is False
    again = output(
        fixture,
        capsys,
        "closure-resume",
        "--plan",
        plan_path,
        "--owner",
        "worker",
        "--expected-revision",
        fp.phases.revision(final),
    )
    assert again["status"] == "published"
    assert fixture.program.load() == final


def test_committed_view_failure_recovers_without_duplicate_record(fixture, capsys, monkeypatch):
    assert fixture.claim() == 0
    plan = output(
        fixture,
        capsys,
        "closure-prepare",
        "--input",
        plan_request(fixture, [stage("first", "First note"), stage("second", "Second note")]),
    )
    name = fixture.write("evidence/S01/attempt-1/view-plan.json", plan)
    real_render = fp.Program.render

    def fail_render(self, state):
        raise fp.ProgramError("Synthetic postcommit view failure")

    monkeypatch.setattr(fp.Program, "render", fail_render)
    result = output(
        fixture,
        capsys,
        "closure-resume",
        "--plan",
        name,
        "--owner",
        "worker",
        "--expected-revision",
        plan["initial_revision"],
    )
    assert result["status"] == "view_recovery_required"
    current = fixture.program.load()
    assert fp.phases.revision(current) == plan["stages"][0]["after_revision"]
    monkeypatch.setattr(fp.Program, "render", real_render)
    assert fixture.run("render") == 0
    result = output(
        fixture,
        capsys,
        "closure-resume",
        "--plan",
        name,
        "--owner",
        "worker",
        "--expected-revision",
        fp.phases.revision(current),
    )
    assert result["status"] == "published"
    assert len(fixture.program.load()["events"]) == len(current["events"]) + 1


def test_persisted_plan_cannot_dispatch_live_execution_even_with_recomputed_digest(
    fixture, capsys, monkeypatch
):
    assert fixture.claim() == 0
    plan = output(
        fixture,
        capsys,
        "closure-prepare",
        "--input",
        plan_request(fixture, [stage("first", "Note")]),
    )
    tampered = deepcopy(plan)
    tampered["stages"][0]["operation"] = "verification-run"
    tampered["plan_digest"] = fp.digest({k: v for k, v in tampered.items() if k != "plan_digest"})
    name = fixture.write("evidence/S01/attempt-1/tampered-plan.json", tampered)
    before = fixture.program.path("backlog.json").read_bytes()
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("Stored plan executed a process")
    )
    capsys.readouterr()
    assert (
        fixture.run(
            "closure-resume",
            "--plan",
            name,
            "--owner",
            "worker",
            "--expected-revision",
            plan["initial_revision"],
        )
        == 1
    )
    assert "Unsupported closure operation" in capsys.readouterr().err
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_actual_cli_runner_executes_once_unlocked_and_publishes_only_explicitly(fixture, capsys):
    assert fixture.claim() == 0
    code = (
        "from pathlib import Path; import sys; "
        "p=Path('counter.bin'); p.write_bytes(str(int(p.read_bytes())+1 if p.exists() else 1).encode()); "
        "assert not Path('flow-fixture/.feature-program.lock').exists(); "
        "sys.stdout.buffer.write(bytes([0,255,10])); sys.stderr.buffer.write(b'actual-error-stream')"
    )
    live_argv = [sys.executable, "-c", code]
    declared = fixture.write(
        "declared-runtime.json", {"facts": {"fixture": "synthetic"}, "unknowns": []}
    )
    environment = output(
        fixture, capsys, "verification-environment", "--environment", declared, "--", *live_argv
    )
    request = {
        "schema_version": 1,
        "operation": "verification-record",
        "kind": "request",
        "id": "actual-request",
        "task_id": "S01",
        "actor_id": "worker",
        "expected_revision": fp.phases.revision(fixture.program.load()),
        "reason": "One actual isolated process",
        "lane": "tests",
        "verifier_id": "qa",
        "check_ids": ["scoped_tests"],
        "input_scope": ["src/S01.py"],
        "input_complete": True,
        "manifest_path": declared_inputs(fixture),
        "environment_identity": environment,
        "method_identity": {
            "argv": live_argv,
            "cwd": str(fixture.program.repo),
            "execution_kind": "test",
        },
        "evidence_destination": "evidence/S01/attempt-1/",
        "next_trigger": "Run actual isolated check",
    }
    request_name = fixture.write("evidence/_verification/attempt-1/actual-request.json", request)
    assert fixture.run("verification-record", "--input", request_name) == 0
    before = fixture.program.path("backlog.json").read_bytes()
    revision = fp.phases.revision(fixture.program.load())
    binding = fixture.write(
        "run-binding.json",
        {
            "task_id": "S01",
            "actor_id": "qa",
            "request_id": "actual-request",
            "request_version": 1,
            "expected_revision": revision,
            "reason": "Capture actual result",
            "next_action": "Publish exact observation",
        },
    )
    result = output(
        fixture,
        capsys,
        "verification-run",
        "S01",
        "--owner",
        "qa",
        "--request",
        binding,
        "--expected-revision",
        revision,
        "--capture-dir",
        "evidence/S01/attempt-1/actual-run",
        "--timeout-seconds",
        "10",
        "--stream-limit-bytes",
        "1024",
        "--",
        *live_argv,
    )
    assert result["capture"]["process"]["outcome"] == "exited"
    assert result["capture"]["process"]["exit_code"] == 0
    assert result["capture"]["completeness"]["status"] == "complete"
    assert result["observation_status"] == "prepared"
    assert result["observation"]["result"] == "passed"
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert (fixture.program.repo / "counter.bin").read_bytes() == b"1"
    assert fixture.program.path(
        "evidence/S01/attempt-1/actual-run/stdout.bin"
    ).read_bytes() == bytes([0, 255, 10])
    observation_name = fixture.write(
        "evidence/_verification/attempt-1/actual-observation.json", result["observation"]
    )
    assert fixture.run("verification-record", "--input", observation_name) == 0
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    lane = snapshot["tasks"][0]["verification"]["lanes"]["items"][0]
    assert lane["execution_summary"]["publication"]["status"] == "recorded"
    assert lane["input_summary"]["status"] == "unchanged"
    assert (fixture.program.repo / "counter.bin").read_bytes() == b"1"


def test_selected_plan_projects_actual_partial_and_source_stale_states(fixture, capsys):
    assert fixture.claim() == 0
    request = plan_request(fixture, [stage("first", "First"), stage("second", "Second")])
    name = "evidence/S01/attempt-1/selected-plan.json"
    plan = output(fixture, capsys, "closure-prepare", "--input", request, "--result-path", name)
    before = fp.read_snapshot(fixture.program.repo, fixture.program.directory, closure_plan=name)
    selected = before["closure"]["plan"]
    assert selected["preflight"]["status"] == "validated"
    assert selected["publication"]["status"] == "not_started"
    assert selected["reference"]["sha256"] == fp.sha(fixture.program.path(name).read_bytes())
    assert "payload" not in fp.canonical(selected).decode()
    first = plan["stages"][0]
    fp.authoring.ClosureHost(fixture.program).publish(first, first["before_revision"], "worker")
    partial = fp.read_snapshot(fixture.program.repo, fixture.program.directory, closure_plan=name)
    assert partial["closure"]["plan"]["publication"]["status"] == "partial"
    assert [
        row["status"] for row in partial["closure"]["plan"]["publication"]["stages"]["items"]
    ] == ["committed", "not_started"]
    assert partial["brief"]["closure"]["plan"]["publication"]["status"] == "partial"
    (fixture.program.repo / "src/S01.py").write_bytes(b"changed = True\n")
    stale = fp.read_snapshot(fixture.program.repo, fixture.program.directory, closure_plan=name)
    assert stale["closure"]["plan"]["preparation"]["status"] == "stale"
    assert stale["closure"]["plan"]["preflight"]["current"] is False


def test_actual_failed_preflight_result_is_immutable_and_readable_without_record_publication(
    fixture, capsys
):
    assert fixture.claim() == 0
    request = plan_request(
        fixture, [stage("first", "Valid"), stage("second", "Wrong actor", "other")]
    )
    name = "evidence/S01/attempt-1/failed-plan.json"
    original = fixture.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    assert fixture.run("closure-prepare", "--input", request, "--result-path", name) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["kind"] == "closure_preparation_failure"
    assert fixture.program.path(name).read_bytes() == fp.canonical(failure)
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory, closure_plan=name)
    assert snapshot["closure"]["plan"]["preflight"]["status"] == "failed"
    assert snapshot["closure"]["plan"]["publication"]["status"] == "blocked"
    assert fixture.program.path("backlog.json").read_bytes() == original


def test_snapshot_queue_counts_all_gaps_before_display_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "git", lambda _repo, args: "" if args[0] == "status" else HEAD)
    fixture = make_small_program(
        tmp_path / "repo", "many-flow-tasks", tuple(f"S{i:02d}" for i in range(60))
    )
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    queue = snapshot["review_queue"]
    assert queue["rows"]["total"] == 180
    assert queue["rows"]["shown"] == 50
    assert queue["rows"]["omitted"] == 130
    assert queue["counts"]["kind"]["required_check_gap"] == 180
    assert snapshot["brief"]["review_queue"]["rows"]["total"] == 180
