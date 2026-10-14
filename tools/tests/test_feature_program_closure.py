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


def test_delivery_attachment_assembly_retains_raw_shared_origin_and_failed_ci(gate):
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
    # When one supported preparation attaches shared bytes individually.
    result = closure.prepare_delivery_inputs(gate.program, gate.program.load(), supplied)
    # Then each task retains exact attribution and no failed result becomes green.
    assert [item["ci_status"] for item in result["inputs"]] == ["ci_failed", "ci_failed"]
    for item in result["inputs"]:
        copied = f"evidence/{item['task_id']}/attempt-1/closure-assembly/attachment-0001.txt"
        assert gate.program.path(copied).read_bytes() == raw
        assert item["receipt"]["provider_observation"]["path"].startswith(
            f"evidence/{item['task_id']}/attempt-1/"
        )
    assert gate.program.path(origin).read_bytes() == raw


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
