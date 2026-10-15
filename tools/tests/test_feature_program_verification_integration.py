"""Real CLI integration on isolated programs; observations explicitly remain recorded claims."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tools.tests.test_feature_program import fixture, fp  # noqa: F401
from tools.tests.test_feature_program_authoring import (
    complete_tasks,
    forbidden,
    provider_request,
    tree,
)
from tools.tests.test_feature_program_phases import gate  # noqa: F401
from tools.tests.test_feature_program_verification import observation, request, submission


def record_cli(fixture, value):
    """Publish through the actual supported CLI and current revision guard."""
    value = {**value, "expected_revision": fp.phases.revision(fixture.program.load())}
    name = fixture.write(f"evidence/_verification/attempt-1/{value['id']}.json", value)
    assert fixture.run("verification-record", "--input", name) == 0
    return name


def test_actual_record_preview_submission_review_and_rework_preserve_history(fixture, capsys):
    assert fixture.claim() == 0
    record_cli(fixture, request(fixture))
    common = {
        "schema_version": 1,
        "operation": "verification-record",
        "task_id": "S01",
        "actor_id": "qa",
        "reason": "Explicit fixture observation",
        "request_id": "request-1",
        "request_version": 1,
        "clock_id": "fixture-clock",
    }
    record_cli(
        fixture,
        {**common, "id": "start-1", "kind": "started", "observed_at": "2026-10-14T10:00:00+00:00"},
    )
    record_cli(
        fixture,
        {
            **common,
            "id": "blocked-1",
            "kind": "blocked",
            "observed_at": "2026-10-14T10:00:01+00:00",
            "blocker": {
                "reason": "Fixture resource held",
                "resolver_id": "worker",
                "next_trigger": "Release fixture resource",
            },
        },
    )
    blocked = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    lane = blocked["verification"]["tasks"]["S01"]["lanes"]["items"][0]
    assert lane["phase"] == "blocked" and lane["resolver_id"] == "worker"
    assert lane["next_trigger"] == "Release fixture resource"
    record_cli(fixture, observation(fixture, "failed-1", result="failed"))
    record_cli(
        fixture,
        {
            **common,
            "id": "finding-1",
            "kind": "finding",
            "statement": "Fixture missing acceptance",
            "affected_paths": ["src/S01.py"],
            "check_ids": ["acceptance"],
            "criteria": [],
            "next_action": "Correct fixture",
            "artifacts": [fixture.evidence("S01", label="finding")],
        },
    )
    record_cli(fixture, observation(fixture))
    record_cli(
        fixture,
        {
            **common,
            "id": "resolution-1",
            "kind": "resolution",
            "finding_id": "finding-1",
            "observation_id": "finished-1",
            "next_action": "Review explicit fixture correction",
        },
    )
    # A separate lane remains a recorded assignment without implying execution.
    record_cli(fixture, request(fixture, "qa-request", lane="native-qa", check_ids=["acceptance"]))
    name = fixture.write("submission-request.json", submission(fixture))
    capsys.readouterr()
    assert fixture.run("submission-inputs", "--input", name) == 0
    prepared = json.loads(capsys.readouterr().out)
    execution = fixture.write("evidence/S01/attempt-1/from-observations.json", prepared["receipt"])
    preview_name = fixture.write(
        "preview-submit.json",
        {"argv": ["submit", "S01", "--owner", "worker", "--receipt", execution]},
    )
    capsys.readouterr()
    assert fixture.run("preview", "--request", preview_name) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "validated"
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", execution) == 0
    review = fixture.review(accepted=False)
    assert fixture.run("rework", "S01", "--review", review) == 0
    state = fixture.program.load()
    assert state["tasks"][0]["status"] == "active"
    assert state["tasks"][0]["attempts"][0]["rounds"][0]["review"]
    assert {entry["input"]["id"] for entry in state["verification"]["records"]} >= {
        "failed-1",
        "finding-1",
        "resolution-1",
        "finished-1",
        "qa-request",
    }
    assert fixture.run("check") == 0


def test_record_and_preview_reject_same_stale_revision_without_mutation(fixture, capsys):
    assert fixture.claim() == 0
    value = request(fixture)
    value["expected_revision"] = "0" * 64
    name = fixture.write("evidence/_verification/attempt-1/stale.json", value)
    preview_name = fixture.write(
        "preview-record.json", {"argv": ["verification-record", "--input", name]}
    )
    before = tree(fixture.program.repo)
    capsys.readouterr()
    assert fixture.run("preview", "--request", preview_name) == 0
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["status"] == "failed"
    assert fixture.run("verification-record", "--input", name) == 1
    assert tree(fixture.program.repo) == before


def test_planned_packet_retains_null_attempt_and_becomes_stale_after_claim(gate, capsys):
    value = request(
        gate.fixture,
        actor_id="director",
        verifier_id="director",
        evidence_destination="evidence/S01/",
    )
    record_cli(gate.fixture, value)
    original = deepcopy(gate.program.load()["verification"]["records"][0])
    assert original["context"]["attempt"] is None and original["context"]["submission"] is None
    assert gate.fixture.claim() == 0
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    lane = snapshot["verification"]["tasks"]["S01"]["lanes"]["items"][0]
    assert lane["applicability"] == "stale"
    assert gate.program.load()["verification"]["records"][0] == original


def test_missing_verification_artifact_is_visible_in_snapshot_and_brief(fixture, capsys):
    assert fixture.claim() == 0
    record_cli(fixture, request(fixture))
    value = observation(fixture)
    record_cli(fixture, value)
    fixture.program.path(value["artifacts"][0]).unlink()
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    lane = snapshot["verification"]["tasks"]["S01"]["lanes"]["items"][0]
    assert snapshot["integrity"]["status"] == "stale" and lane["applicability"] == "unavailable"
    assert lane["result"] == "passed"  # The historical reported outcome remains exact.
    selected = fp.read_brief(fixture.program.repo, fixture.program.directory, "S01")
    assert selected["task"]["verification"]["lanes"]["items"][0]["applicability"] == "unavailable"


def test_independent_snapshot_epoch_rejects_previous_checked_evidence_success(fixture):
    # Given actual fixture records whose first public snapshot has complete checked evidence.
    assert fixture.claim() == 0
    record_cli(fixture, request(fixture))
    value = observation(fixture)
    record_cli(fixture, value)
    before = fixture.program.path("backlog.json").read_bytes()
    first = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    assert first["verification"]["tasks"]["S01"]["lanes"]["items"][0]["applicability"] == "current"
    # When retained output changes between independent public requests.
    fixture.program.path(value["artifacts"][0]).write_bytes(b"changed after the first response")
    second = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    lane = second["verification"]["tasks"]["S01"]["lanes"]["items"][0]
    # Then the new epoch cannot inherit a cached pass and no preparation mutates the registry.
    assert lane["result"] == "passed"
    assert lane["applicability"] == "unavailable"
    assert second["integrity"]["status"] == "stale"
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_actual_record_publication_rechecks_source_after_save_preflight(fixture, monkeypatch):
    assert fixture.claim() == 0
    name = fixture.write("evidence/_verification/attempt-1/drift.json", request(fixture))
    before = fixture.program.path("backlog.json").read_bytes()
    prepare_save = fp.Program.prepare_save

    def drifting_preflight(program, state):
        encoded = prepare_save(program, state)
        (program.repo / "src/S01.py").write_text("external_change = True\n", encoding="utf-8")
        return encoded

    monkeypatch.setattr(fp.Program, "prepare_save", drifting_preflight)
    assert fixture.run("verification-record", "--input", name) == 1
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_verification_human_targets_bind_exact_frozen_canonical_input(fixture):
    assert fixture.claim() == 0
    record_cli(fixture, request(fixture))
    state = fixture.program.load()
    catalog = fp.presentation.catalog(
        fixture.program, state, fp.ReadBudget(), fp.phases.revision(state)
    )
    rows = [row for row in catalog.targets.values() if row["family"] == "F23"]
    assert rows
    assert any(
        row["target"]["record_pointer"] == "/verification/records/0/input/next_trigger"
        for row in rows
    )


def test_legacy_packet_submission_and_snapshot_remain_optional(fixture, capsys, monkeypatch):
    assert fixture.claim() == 0
    before = tree(fixture.program.repo)
    capsys.readouterr()
    with monkeypatch.context() as patch:
        for method in ("lock", "save", "render"):
            patch.setattr(fp.Program, method, forbidden)
        assert fixture.run("verification-packet", "S01") == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet
    assert tree(fixture.program.repo) == before
    assert "verification" not in fixture.program.load()
    fixture.submit()
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    assert snapshot["verification"]["status"] == "absent"
    assert fixture.program.task(fixture.program.load(), "S01")["status"] == "review"


@pytest.mark.parametrize("extension", [None, {}, {"schema_version": 2, "records": []}])
def test_explicit_malformed_verification_never_becomes_legacy(fixture, extension):
    state = fixture.program.load()
    state["verification"] = deepcopy(extension)
    before = tree(fixture.program.repo)
    with pytest.raises(fp.ProgramError):
        fixture.program.validate(state)
    assert tree(fixture.program.repo) == before


def test_original_validation_failure_precedes_optional_extension(fixture):
    state = fixture.program.load()
    state["schema_version"] = 2
    state["verification"] = {"schema_version": 2, "records": []}
    with pytest.raises(fp.ProgramError, match="Unsupported registry"):
        fixture.program.validate(state)


def provider_execution_request(gate, *, output_present=True):
    request = provider_request(gate)
    provider_path = gate.program.path(str(request["provider"]["path"]))
    provider = json.loads(provider_path.read_text(encoding="utf-8"))
    provider["pr_url"] = "https://github.com/example/work-program/pull/12"
    pr = {
        "number": 12,
        "url": provider["pr_url"],
        "head_sha": provider["tested_commit"],
        "base_ref": "beta",
        "base_sha": "b" * 40,
        "tested_sha": provider["tested_commit"],
    }
    stage = {
        "id": "pr-unit",
        "kind": "pr_ci",
        "expected_sha": provider["tested_commit"],
        "checks": [
            {
                "id": "unit",
                "job": "unit",
                "steps": ["pytest"],
                "mode": "required",
                "reason": "Explicit required unit execution",
                "policy_evidence": [],
                "requires_native": False,
                "requires_output": True,
            }
        ],
    }
    context = {
        "repository": "github.com/example/work-program",
        "pr": pr,
        "beta_sha": None,
        "root_actor_id": "director",
        "stages": [stage],
    }
    captured_stage = {
        **deepcopy(stage),
        "observations": [
            {
                "run_id": 12,
                "run_attempt": 1,
                "event": "pull_request",
                "head_sha": pr["head_sha"],
                "checkout_sha": pr["tested_sha"],
                "status": "completed",
                "conclusion": "success",
                "captured_at": "2026-10-14T12:00:00Z",
                "complete": True,
                "jobs": [
                    {
                        "job_id": 21,
                        "name": "unit",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-14T11:59:00Z",
                        "completed_at": "2026-10-14T11:59:30Z",
                        "native": False,
                        "steps": [
                            {
                                "name": "pytest",
                                "status": "completed",
                                "conclusion": "success",
                                "output_present": output_present,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    provider["execution"] = {
        "schema_version": 1,
        "repository": context["repository"],
        "pr": pr,
        "test_merge": None,
        "merge": None,
        "stages": [captured_stage],
        "premerge": None,
        "cleanup": None,
    }
    provider_path.write_bytes(fp.canonical(provider))
    request["provider"]["sha256"] = fp.sha(provider_path.read_bytes())
    for binding in request["tasks"]:
        binding["provider_context"] = deepcopy(context)
    return request


@pytest.mark.parametrize("output_present", [False, True])
def test_adopted_provider_execution_controls_ordinary_delivery(gate, capsys, output_present):
    complete_tasks(gate)
    request = provider_execution_request(gate, output_present=output_present)
    name = gate.fixture.write("provider-request.json", request)
    capsys.readouterr()
    assert gate.fixture.run("delivery-inputs", "--input", name) == 0
    prepared = json.loads(capsys.readouterr().out)
    row = prepared["inputs"][0]
    assert row["provider_execution"]["adopted"] is True
    assert row["ci_status"] == ("ci_recorded_success" if output_present else "ci_pending")
    receipt_name = gate.fixture.write(
        "evidence/S01/attempt-1/observed-delivery.json", row["receipt"]
    )
    assert gate.fixture.run("delivery", "S01", "--receipt", receipt_name) == 0
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    task = next(item for item in snapshot["tasks"] if item["id"] == "S01")
    assert task["provider_execution"]["task_id"] == "S01"
    assert task["provider_execution"]["reference"]["revision"] == snapshot["revision"]
    assert task["provider_execution"]["remote_verified_by_tool"] is False


def test_provider_preparations_are_readonly_and_do_not_imply_remote_actions(
    gate, capsys, monkeypatch
):
    complete_tasks(gate)
    request = provider_execution_request(gate)
    name = gate.fixture.write(
        "premerge-request.json",
        {
            "provider": request["provider"],
            "provider_context": request["tasks"][0]["provider_context"],
        },
    )
    before = tree(gate.program.repo)
    for command in ("premerge-inputs", "cleanup-inputs"):
        capsys.readouterr()
        with monkeypatch.context() as patch:
            patch.setattr(fp.Program, "lock", forbidden)
            patch.setattr(fp, "git", forbidden)
            patch.setattr(fp, "atomic_write", forbidden)
            assert gate.fixture.run(command, "--input", name) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "unknown"
        assert result["advisory"] is True and result["remote_verified_by_tool"] is False
    assert tree(gate.program.repo) == before


def test_explicit_null_provider_context_is_not_legacy(gate):
    complete_tasks(gate)
    value = provider_request(gate)
    value["tasks"][0]["provider_context"] = None
    before = tree(gate.program.repo)
    with pytest.raises(fp.ProgramError, match="explicit current context"):
        fp.authoring.delivery_inputs(gate.program, gate.program.load(), value)
    assert tree(gate.program.repo) == before


def test_provider_drift_after_publication_preflight_rejects_delivery(gate, monkeypatch):
    complete_tasks(gate)
    value = provider_execution_request(gate)
    prepared = fp.authoring.delivery_inputs(gate.program, gate.program.load(), value)
    name = gate.fixture.write(
        "evidence/S01/attempt-1/guarded-delivery.json", prepared["inputs"][0]["receipt"]
    )
    original = gate.program.path("backlog.json").read_bytes()
    prepare_save = fp.Program.prepare_save

    def drifting_preflight(program, state):
        encoded = prepare_save(program, state)
        program.path("provider.json").write_bytes(b"{}\n")
        return encoded

    monkeypatch.setattr(fp.Program, "prepare_save", drifting_preflight)
    assert gate.fixture.run("delivery", "S01", "--receipt", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == original


def test_shared_provider_capture_has_one_read_plus_one_final_recheck(gate, monkeypatch):
    complete_tasks(gate)
    value = provider_execution_request(gate)
    provider_path = gate.program.path(value["provider"]["path"])
    original_reader = fp.bounded_bytes
    reads = []

    def counted_reader(path, limit):
        if path == provider_path:
            reads.append(path)
        return original_reader(path, limit)

    monkeypatch.setattr(fp, "bounded_bytes", counted_reader)
    result = fp.authoring.delivery_inputs(gate.program, gate.program.load(), value)
    assert len(result["inputs"]) == 2 and len(reads) == 2


def early_provider_capture(gate):
    assert gate.fixture.claim("S01") == 0
    gate.fixture.submit("S01")
    assert gate.fixture.claim("S02", owner="second-worker") == 0
    receipt = gate.fixture.receipt("S02")
    assert gate.fixture.run("submit", "S02", "--owner", "second-worker", "--receipt", receipt) == 0
    capture = provider_execution_request(gate)
    provider = json.loads(gate.program.path(capture["provider"]["path"]).read_bytes())
    name = gate.fixture.write(
        "evidence/_verification/attempt-1/early-provider-capture.json", provider
    )
    capture["provider"] = {"path": name, "sha256": fp.sha(gate.program.path(name).read_bytes())}
    return capture


@pytest.mark.parametrize("task_status", ["active", "review"])
def test_early_provider_cli_projection_before_reviewed_delivery(gate, capsys, task_status):
    capture = early_provider_capture(gate)
    if task_status == "active":
        review = gate.fixture.review("S01", accepted=False)
        assert gate.fixture.run("rework", "S01", "--review", review) == 0
    context = gate.fixture.write(
        "evidence/S01/attempt-1/early-provider-context.json",
        capture["tasks"][0]["provider_context"],
    )
    record_cli(
        gate.fixture,
        request(
            gate.fixture,
            "early-pr",
            lane="pr-ci",
            check_ids=["scoped_tests"],
            provider_context_path=context,
        ),
    )
    record_cli(
        gate.fixture,
        observation(
            gate.fixture, "early-pr-result", request_id="early-pr", provider_ref=capture["provider"]
        ),
    )
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    task = next(item for item in snapshot["tasks"] if item["id"] == "S01")
    assert task["status"] == task_status and not gate.program.task(gate.program.load(), "S01").get(
        "deliveries"
    )
    assert task["provider_execution"]["status"] == "passed"
    stage = task["provider_execution"]["stages"][0]
    assert stage["request_id"] == "early-pr" and stage["checks"][0]["run_id"] == 12
    assert stage["source"]["tested_sha"] == "a" * 40
    assert task["provider_execution"]["reference"]["revision"] == snapshot["revision"]
    # A newer canceled observation has no capture; the prior green cannot return.
    current = gate.program.load()
    record_cli(
        gate.fixture,
        {
            "schema_version": 1,
            "operation": "verification-record",
            "id": "cancel-early",
            "kind": "canceled",
            "task_id": "S01",
            "actor_id": "qa",
            "reason": "Explicitly canceled new observation",
            "request_id": "early-pr",
            "request_version": 1,
            "expected_revision": fp.phases.revision(current),
            "next_trigger": "Collect new actual PR execution",
        },
    )
    canceled = fp.read_snapshot(gate.program.repo, gate.program.directory)
    task = next(item for item in canceled["tasks"] if item["id"] == "S01")
    assert task["provider_execution"]["status"] != "passed"
    assert task["provider_execution"].get("stages", []) == []


def test_current_required_lane_gates_complete_without_voluntary_review_refs(gate, capsys):
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    record_cli(
        gate.fixture,
        request(
            gate.fixture, "required-review", lane="independent-check", check_ids=["acceptance"]
        ),
    )
    review = gate.fixture.review(accepted=True)
    original = gate.program.path("backlog.json").read_bytes()
    preview_request = gate.fixture.write(
        "pending-complete-preview.json", {"argv": ["complete", "S01", "--review", review]}
    )
    capsys.readouterr()
    assert gate.fixture.run("preview", "--request", preview_request) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "failed"
    assert gate.fixture.run("complete", "S01", "--review", review) == 1
    assert gate.program.path("backlog.json").read_bytes() == original
    record_cli(
        gate.fixture,
        observation(gate.fixture, "required-review-result", request_id="required-review"),
    )
    assert gate.fixture.run("complete", "S01", "--review", review) == 0


def test_early_provider_context_is_frozen_and_stale_source_cannot_pass(gate):
    capture = early_provider_capture(gate)
    context = gate.fixture.write(
        "evidence/S01/attempt-1/early-provider-context.json",
        capture["tasks"][0]["provider_context"],
    )
    record_cli(
        gate.fixture,
        request(
            gate.fixture,
            "early-pr",
            lane="pr-ci",
            check_ids=["scoped_tests"],
            provider_context_path=context,
        ),
    )
    record_cli(
        gate.fixture,
        observation(
            gate.fixture, "early-pr-result", request_id="early-pr", provider_ref=capture["provider"]
        ),
    )
    (gate.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    task = next(item for item in snapshot["tasks"] if item["id"] == "S01")
    assert task["provider_execution"]["status"] != "passed"
    assert task["provider_execution"]["applicability"] == "stale"
    gate.program.path(context).write_bytes(b"{}\n")
    unavailable = fp.read_snapshot(gate.program.repo, gate.program.directory)
    task = next(item for item in unavailable["tasks"] if item["id"] == "S01")
    assert task["provider_execution"]["status"] != "passed"
    assert task["provider_execution"]["applicability"] == "unavailable"


def test_failed_early_provider_execution_blocks_complete_even_with_passing_reported_result(gate):
    capture = early_provider_capture(gate)
    provider_path = gate.program.path(capture["provider"]["path"])
    provider = json.loads(provider_path.read_bytes())
    provider["execution"]["stages"][0]["observations"][0]["jobs"][0]["steps"][0]["conclusion"] = (
        "failure"
    )
    provider_path.write_bytes(fp.canonical(provider))
    capture["provider"]["sha256"] = fp.sha(provider_path.read_bytes())
    context = gate.fixture.write(
        "evidence/S01/attempt-1/early-provider-context.json",
        capture["tasks"][0]["provider_context"],
    )
    record_cli(
        gate.fixture,
        request(
            gate.fixture,
            "early-pr",
            lane="pr-ci",
            check_ids=["scoped_tests"],
            provider_context_path=context,
        ),
    )
    record_cli(
        gate.fixture,
        observation(
            gate.fixture, "early-pr-result", request_id="early-pr", provider_ref=capture["provider"]
        ),
    )
    task = next(
        item
        for item in fp.read_snapshot(gate.program.repo, gate.program.directory)["tasks"]
        if item["id"] == "S01"
    )
    assert task["provider_execution"]["status"] == "failed"
    review = gate.fixture.review(accepted=True)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("complete", "S01", "--review", review) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_malformed_early_provider_context_rejects_before_canonical_write(gate):
    early_provider_capture(gate)
    context = gate.fixture.write("evidence/S01/attempt-1/early-provider-context.json", {})
    value = request(gate.fixture, "bad-early-pr", lane="pr-ci", provider_context_path=context)
    name = gate.fixture.write("evidence/_verification/attempt-1/bad-early-pr.json", value)
    before = tree(gate.program.repo)
    assert gate.fixture.run("verification-record", "--input", name) == 1
    assert tree(gate.program.repo) == before


def test_existing_native_actor_ids_flow_through_verification_roles(gate):
    for identifier in ("/root/implementer", "/root/native_qa", "/root/resolver"):
        name = gate.fixture.write(
            f"native-actor-{identifier.rsplit('/', 1)[-1]}.json",
            {
                "id": identifier,
                "role": "worker",
                "parent_id": "director",
                "decision": "new",
                "reason": "Actual native-style fixture identity, not authentication",
                "provenance": "unknown",
                "model_id": None,
                "reasoning_effort": None,
                "observed_status": "unknown",
            },
        )
        assert gate.fixture.run("actor-record", "--input", name) == 0
    assert gate.fixture.claim(owner="/root/implementer") == 0
    record_cli(
        gate.fixture,
        request(
            gate.fixture,
            "native-request",
            actor_id="/root/implementer",
            verifier_id="/root/native_qa",
        ),
    )
    record_cli(
        gate.fixture,
        {
            "schema_version": 1,
            "operation": "verification-record",
            "id": "native-block",
            "kind": "blocked",
            "task_id": "S01",
            "actor_id": "director",
            "reporter_id": "/root/native_qa",
            "reason": "Director preserves the actual native reporter",
            "request_id": "native-request",
            "request_version": 1,
            "blocker": {
                "reason": "Explicit native fixture wait",
                "resolver_id": "/root/resolver",
                "next_trigger": "Release resource",
            },
        },
    )
    record_cli(
        gate.fixture,
        observation(
            gate.fixture,
            "native-result",
            actor_id="director",
            reporter_id="/root/native_qa",
            request_id="native-request",
        ),
    )
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    lane = snapshot["verification"]["tasks"]["S01"]["lanes"]["items"][0]
    assert lane["verifier_id"] == "/root/native_qa"
    records = gate.program.load()["verification"]["records"]
    assert records[1]["input"]["blocker"]["resolver_id"] == "/root/resolver"
    assert records[-1]["input"]["reporter_id"] == "/root/native_qa"


def test_real_cli_manifest_capture_record_and_precise_summary(fixture, capsys):
    # Given an active fixture and explicit inspected input facts under immutable evidence paths.
    from tools.tests.test_feature_program_verification import manifest_request

    assert fixture.claim() == 0
    value, captured = manifest_request(fixture)
    manifest_path = fixture.write("evidence/S01/attempt-1/manifest.json", value["input_manifest"])
    environment_path = fixture.write(
        "evidence/S01/attempt-1/environment.json", value["environment_identity"]
    )
    method_path = fixture.write("evidence/S01/attempt-1/method.json", value["method_identity"])
    capsys.readouterr()
    # When the actual read-only producer CLI captures the supplied declared closure.
    assert (
        fixture.run(
            "inputs-capture",
            "--manifest",
            manifest_path,
            "--environment",
            environment_path,
            "--method",
            method_path,
        )
        == 0
    )
    actual = json.loads(capsys.readouterr().out)
    # Then its exact identity can be adopted and shown without claiming execution.
    assert actual["input_digest"] == captured["input_digest"]
    capture_path = fixture.write("evidence/S01/attempt-1/cli-capture.json", actual)
    value.pop("input_manifest")
    value.pop("environment_identity")
    value.pop("method_identity")
    value["manifest_path"] = manifest_path
    value["input_capture_path"] = capture_path
    record_cli(fixture, value)
    lane = fp.read_snapshot(fixture.program.repo, fixture.program.directory)["verification"][
        "tasks"
    ]["S01"]["lanes"]["items"][0]
    assert lane["phase"] == "requested"
    assert lane["input_summary"]["status"] == "unchanged"
    assert lane["input_summary"]["completeness"] == "complete"
