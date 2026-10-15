"""Closure preparation and recovery preserve ordinary records and actual evidence."""

from copy import deepcopy
import hashlib

import pytest

from tools.tests.test_feature_program import fixture, fp  # noqa: F401
from tools.tests.test_feature_program_phases import gate  # noqa: F401
from tools.tests.test_feature_program_authoring import complete_tasks, provider_request
from tools.tests.test_feature_program_delivery import (
    fixture as captured_provider,
    cleanup as captured_cleanup,
)

closure = fp.flow_module("closure")


def test_exact_attachments_keep_original_bytes_and_individual_prefixes(fixture):
    # Given one immutable actual capture and two explicitly selected task namespaces.
    origin = fixture.write("evidence/_coordination/attempt-1/capture.json", {"outcome": "failed"})
    raw = fixture.program.path(origin).read_bytes()
    request = {
        "attachments": [
            {
                "origin_ref": {"path": origin, "sha256": hashlib.sha256(raw).hexdigest()},
                "destination": "evidence/A/attempt-1/capture.json",
                "task_id": "A",
                "attempt": 1,
            }
        ]
    }
    # When inert evidence is assembled without a canonical publication.
    result = closure.prepare_attachments(fixture.program, request, materialize=True)
    # Then the attachment and origin remain exact and no success is invented.
    assert (
        fixture.program.path(result["attachments"][0]["attachment_ref"]["path"]).read_bytes() == raw
    )
    assert fixture.program.path(origin).read_bytes() == raw
    assert result["accepted"] is False


def test_attachment_conflict_rejects_before_writing_any_output(fixture):
    # Given a conflicting later attachment.
    origin = fixture.write("evidence/_coordination/attempt-1/capture.json", {"exit_code": 1})
    fixture.write("evidence/A/attempt-1/conflict.json", {"exit_code": 0})
    reference = {"path": origin, "sha256": fp.sha(fixture.program.path(origin).read_bytes())}
    request = {
        "attachments": [
            {
                "origin_ref": reference,
                "destination": "evidence/A/attempt-1/new.json",
                "task_id": "A",
                "attempt": 1,
            },
            {
                "origin_ref": reference,
                "destination": "evidence/A/attempt-1/conflict.json",
                "task_id": "A",
                "attempt": 1,
            },
        ]
    }
    # When the complete assembly is checked.
    with pytest.raises(closure.ClosureError):
        closure.prepare_attachments(fixture.program, request, materialize=True)
    # Then the earlier output was never written.
    assert not fixture.program.path("evidence/A/attempt-1/new.json").exists()


def test_policy_rebinding_changes_only_declared_policy_fields():
    # Given a policy locator that also appears in unrelated authored prose.
    original = {
        "stages": [{"checks": [{"policy_evidence": ["old.json"], "name": "old.json"}]}],
        "summary": "old.json",
    }
    frozen = deepcopy(original)
    # When exact policy attachments are rebound.
    result = closure.rebind_policy_paths(original, {"old.json": "evidence/A/attempt-1/policy.json"})
    # Then only the typed policy field changes.
    assert result["stages"][0]["checks"][0]["policy_evidence"] == [
        "evidence/A/attempt-1/policy.json"
    ]
    assert result["summary"] == "old.json"
    assert result["stages"][0]["checks"][0]["name"] == "old.json"
    assert original == frozen


class Host(fp.authoring.ClosureHost):
    """Use real ordinary transitions with a scoped producer identity for these scenarios."""

    def identity(self):
        return {"schema_version": 1, "producer": "test-real-host", "protocol_version": 1}


def note_plan(fixture, host, count=4, refs=None):
    return closure.prepare_plan(
        fixture.program,
        fixture.program.load(),
        {
            "owner_id": "worker",
            "scope": ["src/S01.py"],
            "refs": refs or [],
            "stages": [
                {
                    "id": f"note-{number}",
                    "operation": "note",
                    "payload": {
                        "id": "S01",
                        "owner": "worker",
                        "reason": f"Recorded observation {number}",
                        "next_action": "Review actual evidence",
                    },
                    "observed_at": f"2026-10-14T00:00:0{number}+00:00",
                }
                for number in range(1, count + 1)
            ],
        },
        host,
    )


def test_later_invalid_stage_preflight_keeps_registry_unchanged(fixture):
    # Given a valid first transition and a later invalid task.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    before = fixture.program.path("backlog.json").read_bytes()
    request = {
        "owner_id": "worker",
        "scope": ["src/S01.py"],
        "stages": [
            {
                "id": "first",
                "operation": "note",
                "payload": {
                    "id": "S01",
                    "owner": "worker",
                    "reason": "Actual observation",
                    "next_action": "Review",
                },
                "observed_at": "2026-10-14T00:00:01+00:00",
            },
            {
                "id": "second",
                "operation": "note",
                "payload": {
                    "id": "MISSING",
                    "owner": "worker",
                    "reason": "Later observation",
                    "next_action": "Review",
                },
                "observed_at": "2026-10-14T00:00:02+00:00",
            },
        ],
    }
    # When the whole detached sequence is preflighted.
    with pytest.raises(fp.ProgramError):
        closure.prepare_plan(fixture.program, fixture.program.load(), request, host)
    # Then the first valid transition was not committed.
    assert fixture.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("publisher,permitted", [("worker-a", False), ("leader", True)])
def test_every_projected_stage_checks_actual_plan_publisher_authority(
    fixture, publisher, permitted
):
    # Given two individually authorized payload authors owning distinct tasks.
    assert fixture.claim("S01", owner="worker-a") == 0
    assert fixture.claim("S02", owner="worker-b") == 0
    host = Host(fixture.program)
    before = fixture.program.path("backlog.json").read_bytes()
    request = {
        "owner_id": publisher,
        "scope": ["src/S01.py", "src/S02.py"],
        "stages": [
            {
                "id": f"note-{task_id}",
                "operation": "note",
                "payload": {
                    "id": task_id,
                    "owner": author,
                    "reason": "Actual owned observation",
                    "next_action": "Inspect evidence",
                },
                "observed_at": f"2026-10-15T00:00:0{index}+00:00",
            }
            for index, (task_id, author) in enumerate((("S01", "worker-a"), ("S02", "worker-b")), 1)
        ],
    }
    # When the whole plan is preflighted for its actual publisher.
    if permitted:
        result = closure.prepare_plan(fixture.program, host.load(), request, host)
        assert result["preflight"]["status"] == "validated"
    else:
        with pytest.raises(fp.ProgramError):
            closure.prepare_plan(fixture.program, host.load(), request, host)
    # Then a payload author's permission never substitutes for the plan publisher's permission.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_partial_sequence_resumes_exact_remaining_events(fixture):
    # Given two actually published stages of a four-stage ordinary record plan.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = note_plan(fixture, host)
    for stage in plan["stages"][:2]:
        host.publish(stage, stage["before_revision"], "worker")
    initial_events = deepcopy(host.load()["events"])
    # When the saved plan resumes.
    result = closure.resume_plan(fixture.program, plan, host, "worker", host.revision(host.load()))
    # Then only the two remaining ordinary records are appended.
    assert result["status"] == "published"
    assert result["execution_replayed"] is False
    assert host.load()["events"][: len(initial_events)] == initial_events
    assert len(host.load()["events"]) == len(initial_events) + 2


def test_committed_inspection_generates_views_once_and_rechecks_bytes(fixture, monkeypatch):
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = note_plan(fixture, host, count=3)
    for stage in plan["stages"]:
        host.publish(stage, stage["before_revision"], "worker")
    state = host.load()
    calls = []
    original = fp.Program.views

    def views(program, value):
        calls.append(value)
        return original(program, value)

    monkeypatch.setattr(fp.Program, "views", views)
    result = closure.inspect_plan(fixture.program, plan, state, host)
    assert result["committed"] == 3
    assert not any(row["view_recovery_required"] for row in result["stages"])
    assert len(calls) == 1
    factory = Host._inspection_context

    class ChangedObservation:
        def __init__(self, context):
            self.context = context

        def inspect_stage(self, checked):
            value = self.context.inspect_stage(checked)
            fixture.program.path("BOARD.md").write_text(
                "Changed during observation", encoding="utf-8"
            )
            return value

        def recheck(self):
            self.context.recheck()

    monkeypatch.setattr(
        Host,
        "_inspection_context",
        lambda self, value, refs: ChangedObservation(factory(self, value, refs)),
    )
    with pytest.raises(fp.ProgramError, match="changed|Changed|differs"):
        closure.inspect_plan(fixture.program, plan, state, host)


def test_committed_render_failure_requires_render_then_continues(fixture, monkeypatch):
    # Given a plan and a normal publisher whose derived render fails once after commit.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = note_plan(fixture, host, count=2)
    with monkeypatch.context() as patch:

        def render_failure(self, state):
            raise OSError("Synthetic derived view access failure")

        patch.setattr(fp.Program, "render", render_failure)
        # When publication reaches the actual committed-render boundary.
        result = closure.resume_plan(
            fixture.program, plan, host, "worker", host.revision(host.load())
        )
    # Then canonical success is retained, and explicit view recovery precedes remaining work.
    assert result["status"] == "view_recovery_required"
    count = len(host.load()["events"])
    assert (
        closure.resume_plan(fixture.program, plan, host, "worker", host.revision(host.load()))[
            "status"
        ]
        == "view_recovery_required"
    )
    assert len(host.load()["events"]) == count
    fixture.program.render(host.load())
    resumed = closure.resume_plan(fixture.program, plan, host, "worker", host.revision(host.load()))
    assert resumed["status"] == "published"
    assert len(host.load()["events"]) == count + 1


@pytest.mark.parametrize("drift", ["source", "reference", "producer", "owner", "revision"])
def test_resume_rejects_binding_drift_before_publication(fixture, drift):
    # Given a prepared plan with exact source, evidence and authority bindings.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    evidence = fixture.evidence("S01", label="new-unpublished-capture")
    reference = {"path": evidence, "sha256": fp.sha(fixture.program.path(evidence).read_bytes())}
    plan = note_plan(fixture, host, count=1, refs=[reference])
    before = fixture.program.path("backlog.json").read_bytes()
    owner = "worker"
    revision = host.revision(host.load())
    if drift == "source":
        (fixture.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
    elif drift == "reference":
        fixture.program.path(evidence).write_bytes(b"Changed actual evidence")
    elif drift == "producer":
        plan["producer"]["protocol_version"] = 2
        plan["plan_digest"] = fp.digest(
            {key: value for key, value in plan.items() if key != "plan_digest"}
        )
    elif drift == "owner":
        owner = "other"
    else:
        revision = "0" * 64
    # When an incompatible record-only resume is requested.
    with pytest.raises(closure.ClosureError):
        closure.resume_plan(fixture.program, plan, host, owner, revision)
    # Then no transition or process is replayed.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_stored_execution_operation_is_not_a_record_plan(fixture):
    # Given inert stored command text, rather than a normal record transition.
    host = Host(fixture.program)
    before_events = deepcopy(host.load()["events"])
    request = {
        "owner_id": "worker",
        "scope": ["src/S01.py"],
        "stages": [
            {
                "id": "run",
                "operation": "verification-run",
                "payload": {"argv": ["python", "harmful.py"]},
                "observed_at": "2026-10-14T00:00:01+00:00",
            }
        ],
    }
    # When closure preparation parses the explicit operation allowlist.
    with pytest.raises(closure.ClosureError):
        closure.prepare_plan(fixture.program, host.load(), request, host)
    # Then no command-bearing stage is prepared.
    assert host.load()["events"] == before_events


def test_readiness_bounds_requirements_without_inferring_acceptance(fixture):
    # Given valid canonical tasks in an explicitly supplied current goal projection.
    state = fixture.program.load()
    state["objective"] = {
        "id": "goal",
        "task_ids": [task["id"] for task in state["tasks"]],
        "status": "active",
    }
    # When the host's already-validated projection is bounded once.
    result = closure.readiness(fixture.program, state, limit=5, prevalidated=True)
    # Then omitted obligations count and an all-finished claim is never invented.
    assert result["requirements"]["shown"] == 5
    assert result["requirements"]["total"] > 5
    assert result["requirements"]["omitted"] == result["requirements"]["total"] - 5
    assert result["accepted"] is False


def test_checked_readiness_retains_current_guards_without_revalidating_initial_state(
    gate, monkeypatch
):
    complete_tasks(gate)
    state = gate.program.load()
    checked = gate.program.validate(state)
    terminals = []
    current_checks = []
    original_terminal = fp.Program.verify_terminal
    original_completion = fp.verification.validate_completion

    def duplicate_validation(program, value):
        pytest.fail("Initial canonical state was validated twice")

    def terminal(program, task, attempt, budget=None):
        terminals.append(task["id"])
        return original_terminal(program, task, attempt, budget)

    def current(program, value, task_id, *args, **kwargs):
        current_checks.append(task_id)
        original_completion(program, value, task_id, *args, **kwargs)
        if task_id == "S01":
            raise fp.ProgramError("Current required lane is pending")

    monkeypatch.setattr(fp.Program, "validate", duplicate_validation)
    monkeypatch.setattr(fp.Program, "verify_terminal", terminal)
    monkeypatch.setattr(fp.verification, "validate_completion", current)
    result = closure._readiness_from_checked(gate.program, state, checked)
    assert terminals == current_checks == ["S01", "S02"]
    assert any(
        row["kind"] == "current_verification"
        and row["task_id"] == "S01"
        and row["reason"] == "Current required lane is pending"
        for row in result["requirements"]["items"]
    )
    with pytest.raises(closure.ClosureError, match="initial-state observation differs"):
        closure._readiness_from_checked(gate.program, deepcopy(state), checked)


def test_selected_plan_projects_actual_partial_publication_and_bounds(fixture):
    # Given one actual published stage of a complete preflighted plan.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = note_plan(fixture, host, count=3)
    first = plan["stages"][0]
    host.publish(first, first["before_revision"], "worker")
    # When the explicitly selected immutable plan is projected.
    result = closure.project_plan(fixture.program, host.load(), plan, host, limit=2)
    # Then actual committed and remaining records stay distinct from acceptance.
    assert result["publication"]["status"] == "partial"
    assert result["publication"]["stages"]["total"] == 3
    assert result["publication"]["stages"]["omitted"] == 1
    assert result["publication"]["stages"]["items"][0]["status"] == "committed"
    assert result["next_actor_id"] == "worker"
    assert result["next_action"] == "publish_remaining_records"
    assert result["accepted"] is False


def test_selected_plan_drift_cannot_show_current_preflight(fixture):
    # Given an immutable preflight followed by real source drift.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = note_plan(fixture, host, count=1)
    (fixture.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
    # When the web's selected plan is re-inspected.
    result = closure.project_plan(fixture.program, host.load(), plan, host)
    # Then old validated preflight is not a current passing fact.
    assert result["preparation"]["status"] == "stale"
    assert result["preflight"]["current"] is False
    assert result["publication"]["status"] == "blocked"
    assert result["publication"]["stages"]["items"] == []


def test_recorded_failed_preparation_stays_nonpassing(fixture):
    # Given a frozen actual host failure capture with no source-completeness claim.
    host = Host(fixture.program)
    name = fixture.write("evidence/_coordination/attempt-1/failure-request.json", {"stages": []})
    document = {
        "schema_version": 1,
        "kind": "closure_preparation_failure",
        "status": "failed",
        "initial_revision": host.revision(host.load()),
        "producer": host.identity(),
        "refs": [{"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}],
        "failure": {"reason": "Invalid later-stage input", "failure_type": "ProgramError"},
        "accepted": False,
    }
    # When the selected failure is presented.
    result = closure.project_plan(fixture.program, host.load(), document, host)
    # Then it remains failed and does not certify source currentness.
    assert result["preflight"]["status"] == "failed"
    assert result["preflight"]["current"] is False
    assert result["accepted"] is False


def test_mutable_registry_observation_is_not_immutable_plan_evidence(fixture):
    # Given a current registry hash useful only as a historical observation/revision.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    reference = {
        "path": "backlog.json",
        "sha256": fp.sha(fixture.program.path("backlog.json").read_bytes()),
    }
    # When it is incorrectly admitted as immutable closure evidence.
    with pytest.raises(closure.ClosureError):
        note_plan(fixture, host, count=1, refs=[reference])
    # Then it cannot invalidate later legitimate steps as if it were a frozen CI artifact.
    assert len(host.load()["events"]) == 2


@pytest.mark.parametrize(
    "conclusion,status", [("success", "ci_recorded_success"), ("failure", "ci_failed")]
)
def test_mechanical_delivery_assembly_writes_ordinary_receipts_without_publishing(
    gate, conclusion, status
):
    # Given actual synthetic reviewed tasks and explicit separate provider outcomes.
    complete_tasks(gate)
    supplied = provider_request(gate, conclusion)
    for binding in supplied["tasks"]:
        for name in ("attempt", "submission_sha256", "reviewed_source_digest"):
            binding.pop(name)
    before = gate.program.path("backlog.json").read_bytes()
    destinations = {
        task_id: f"evidence/{task_id}/attempt-1/prepared-delivery.json"
        for task_id in ("S01", "S02")
    }
    # When one supported preparation emits ordinary individual receipts.
    result = closure.prepare_inputs(
        gate.program,
        gate.program.load(),
        {"kind": "delivery", "input": supplied, "receipt_destinations": destinations},
        Host(gate.program),
    )
    # Then actual failed/successful outcomes persist and canonical delivery is untouched.
    assert [item["ci_status"] for item in result["inputs"]] == [status, status]
    for item in result["inputs"]:
        assert fp.read_json(gate.program.path(item["receipt_ref"]["path"])) == item["receipt"]
        assert item["delivery_accepted"] is False
    assert gate.program.path("backlog.json").read_bytes() == before


def test_completion_assembly_keeps_unperformed_criteria_and_review_pending(gate):
    # Given an actual governed objective whose decision facts are still incomplete.
    gate.ready()
    before = gate.program.path("backlog.json").read_bytes()
    destination = "evidence/_coordination/attempt-1/prepared-completion.json"
    # When mechanical completion bindings are assembled into an ordinary receipt.
    result = closure.prepare_inputs(
        gate.program,
        gate.program.load(),
        {
            "kind": "completion",
            "input": {"scope": ["src/S01.py", "src/S02.py"], "facts": {"actor_id": "director"}},
            "receipt_destination": destination,
        },
        Host(gate.program),
    )
    # Then assembly never supplies a criterion verdict or independent review.
    assert all(item["status"] == "not_run" for item in result["receipt"]["criteria_results"])
    assert result["related_inputs"]["integrated_review"]["verdict"] == "pending"
    assert result["accepted"] is False
    assert fp.read_json(gate.program.path(destination)) == result["receipt"]
    assert gate.program.path("backlog.json").read_bytes() == before


def test_delivery_attachment_assembly_retains_raw_shared_origin_and_failed_ci(gate, monkeypatch):
    # Given reviewed tasks and one raw provider observation with failed actual CI.
    complete_tasks(gate)
    supplied = provider_request(gate, "failure")
    origin = gate.fixture.write(
        "evidence/_coordination/attempt-1/shared-observation.json", {"actual_exit": 1}
    )
    raw = gate.program.path(origin).read_bytes()
    supplied["shared_evidence"] = [{"path": origin, "sha256": fp.sha(raw)}]
    for item in supplied["tasks"]:
        item["assembly_prefix"] = f"evidence/{item['task_id']}/attempt-1/closure-assembly"
    state = gate.program.load()
    provider_calls = []
    validation_calls = []
    original_provider = fp.authoring.provider_observation
    original_validate = fp.Program.validate

    def observe(program, ref, budget):
        provider_calls.append(ref["path"])
        return original_provider(program, ref, budget)

    def validate(program, state):
        validation_calls.append(state)
        return original_validate(program, state)

    monkeypatch.setattr(fp.authoring, "provider_observation", observe)
    monkeypatch.setattr(fp.Program, "validate", validate)
    # When one supported preparation attaches shared bytes individually.
    result = closure.prepare_delivery_inputs(gate.program, state, supplied)
    # Then each task retains exact attribution and no failed result becomes green.
    assert [item["ci_status"] for item in result["inputs"]] == ["ci_failed", "ci_failed"]
    for item in result["inputs"]:
        copied = f"evidence/{item['task_id']}/attempt-1/closure-assembly/attachment-0001.txt"
        assert gate.program.path(copied).read_bytes() == raw
        assert item["receipt"]["provider_observation"]["path"].startswith(
            f"evidence/{item['task_id']}/attempt-1/"
        )
    assert gate.program.path(origin).read_bytes() == raw
    assert provider_calls.count(supplied["provider"]["path"]) == 1
    assert len(validation_calls) == 1


def test_cleanup_resources_keep_eligible_and_in_use_observations_independent(fixture):
    # Given distinct actual recorded cleanup envelopes for two selected resources.
    ready, context = captured_provider(beta=True)
    captured_cleanup(ready)
    in_use = deepcopy(ready)
    in_use["cleanup"]["in_use"] = True
    resources = []
    for identifier, value in (("eligible", ready), ("active", in_use)):
        name = fixture.write(
            f"evidence/_coordination/attempt-1/{identifier}.json", {"execution": value}
        )
        resources.append(
            {
                "id": identifier,
                "provider": {
                    "path": name,
                    "sha256": fp.sha(fixture.program.path(name).read_bytes()),
                },
                "provider_context": context,
            }
        )
    # When offline preparation assesses each selected resource.
    result = closure.prepare_cleanup_inputs(fixture.program, {"resources": resources})
    # Then one resource's eligibility cannot clean or authorize another resource.
    assert result["resources"][0]["status"] == "ready"
    assert result["resources"][1]["status"] == "deferred"
    assert result["accepted"] is False
    assert result["remote_verified_by_tool"] is False


def test_compatible_actual_run_preserves_empty_stream_and_records_nonempty_metadata(fixture):
    from tools.tests.test_feature_program_execution import execution, prepare
    from tools.tests.test_feature_program_verification import record

    # Given one actual shared invocation, explicit compatible target request and empty raw stderr.
    assert fixture.claim() == 0
    assert fixture.claim("S02", owner="worker-two") == 0
    prepared = prepare(
        fixture,
        "from pathlib import Path; counter = Path('shared-counter.txt'); counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1'); print('shared')",
    )
    origin = fixture.program.load()["verification"]["records"][-1]["input"]
    record(
        fixture,
        {
            **origin,
            "id": "target-shared-request",
            "task_id": "S02",
            "actor_id": "worker-two",
            "expected_revision": fp.phases.revision(fixture.program.load()),
            "evidence_destination": "evidence/S02/attempt-1/",
        },
    )
    actual = execution.execute_run(prepared)
    stderr_ref = actual["capture"]["outputs"]["stderr"]
    assert fixture.program.path(stderr_ref["path"]).read_bytes() == b""
    supplied = {
        "task_ids": ["S02"],
        "run_capture_ref": actual["capture_ref"],
        "actor_id": "qa",
        "observations": [
            {
                "task_id": "S02",
                "id": "target-shared-finished",
                "reason": "Actual compatible sealed invocation",
                "attachment_prefix": "evidence/S02/attempt-1/shared-capture",
            }
        ],
    }
    path = "evidence/_verification/attempt-1/shared-finished.json"
    result = closure.prepare_inputs(
        fixture.program,
        fixture.program.load(),
        {"kind": "applicability", "input": supplied, "receipt_destinations": {"S02": path}},
        Host(fixture.program),
    )
    item = result["inputs"][0]
    assert item["receipt_ref"]["path"] == path
    with pytest.raises(closure.ClosureError):
        closure.prepare_inputs(
            fixture.program,
            fixture.program.load(),
            {
                "kind": "applicability",
                "input": supplied,
                "receipt_destinations": {
                    "S02": "evidence/S02/attempt-1/wrong-control-receipt.json"
                },
            },
            Host(fixture.program),
        )
    assert not fixture.program.path("evidence/S02/attempt-1/wrong-control-receipt.json").exists()
    # When the ordinary guarded recorder admits the actual compatible evidence.
    assert fixture.run("verification-record", "--input", path) == 0
    # Then empty bytes remain exact while their sealed nonempty provenance is frozen once.
    empty = [row for row in item["attachments"] if row["byte_count"] == 0]
    assert len(empty) == 1
    assert fixture.program.path(empty[0]["attachment_ref"]["path"]).read_bytes() == b""
    manifest = fp.read_json(fixture.program.path(item["attachment_manifest_ref"]["path"]))
    assert {
        key: empty[0][key] for key in ("origin_ref", "attachment_ref", "byte_count")
    } in manifest["attachments"]
    assert manifest["origin_ref"] == actual["capture_ref"]
    assert all(
        fixture.program.path(name).stat().st_size > 0 for name in item["receipt"]["artifacts"]
    )
    saved = fixture.program.load()["verification"]["records"][-1]
    assert empty[0]["origin_ref"] in saved["evidence"]
    assert item["attachment_manifest_ref"] in saved["evidence"]
    assert (fixture.program.repo / "shared-counter.txt").read_text() == "1"
    fixture.program.path(empty[0]["attachment_ref"]["path"]).write_bytes(b"Changed target copy")
    with pytest.raises(fp.ProgramError):
        fixture.program.load()
    assert (fixture.program.repo / "shared-counter.txt").read_text() == "1"
