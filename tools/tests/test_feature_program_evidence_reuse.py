"""Exact byte/decision adapters on isolated programs; no real project progress claimed."""

from copy import deepcopy
from pathlib import Path
import argparse
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.tests.test_feature_program import HEAD, fp, make_small_program  # noqa: E402
from tools.tests.test_feature_program_phases import gate  # noqa: E402,F401
from tools.tests.test_feature_program_verification import record, request, observation  # noqa: E402
import feature_program_evidence_reuse as reuse  # noqa: E402


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "git", lambda _root, args: "" if args[0] == "status" else HEAD)
    result = make_small_program(tmp_path / "repo", "evidence-fixture", ("S01", "S02"))
    assert result.claim() == 0
    return result


def reference(fixture, name, raw=b"retained original"):
    path = fixture.program.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": name, "sha256": fp.sha(raw)}


def selection(refs, *, task_id="S01", prefix="proof", role="artifact"):
    return {
        "schema_version": 1,
        "origins": [
            {"key": f"item{index}", "origin_ref": ref, "role": role}
            for index, ref in enumerate(refs)
        ],
        "targets": [
            {
                "task_id": task_id,
                "attempt": 1,
                "prefix": f"evidence/{task_id}/attempt-1/{prefix}",
                "keys": [f"item{index}" for index in range(len(refs))],
                "mappings": [],
            }
        ],
    }


def copied(fixture, refs, **kwargs):
    plan = reuse.plan_evidence(fixture.program, fixture.program.load(), selection(refs, **kwargs))
    return reuse.materialize_evidence(fixture.program, fixture.program.load(), plan)


def decision(fixture, binding, facts, *, actor="worker", name="actual-decision"):
    value = {
        "schema_version": 1,
        "kind": "evidence_decision",
        "subject_binding": binding,
        "actor_id": actor,
        "facts": facts,
    }
    name = fixture.write(f"design/{name}.json", value)
    return {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}


class NormalAdapter:
    """Freeze actual inert receipt bytes and call real ordinary preparation; never publish."""

    def __init__(self):
        self.assessed = []

    def assess(self, program, state, operation, receipt, *, owner):
        self.assessed.append(operation)
        task_id = receipt.get("task_id", "_coordination")
        attempt = receipt.get("attempt", 1)
        raw = fp.canonical(receipt)
        namespace = "_verification" if operation == "observation" else task_id
        name = (
            f"evidence/{namespace}/attempt-{attempt}/ordinary-{operation}-{fp.sha(raw)[:16]}.json"
        )
        fp.flow_module("closure")._exact_write(program.path(name), raw)
        if operation == "integrated_review":
            goal = state["objective"]
            fp.coordination._integrated_review(
                program,
                state,
                goal,
                {
                    "integrated_review": name,
                    "actor_id": "director",
                    "source_head": HEAD,
                    "source_digest": receipt["source_digest"],
                    "scope": receipt["scope"],
                },
                set(),
                HEAD,
            )
        else:
            command = {
                "submission": "submit",
                "review": "complete" if receipt.get("verdict") == "accepted" else "rework",
                "completion": "objective-complete",
                "delivery": "delivery",
                "observation": "verification-record",
            }[operation]
            args = argparse.Namespace(
                command=command, id=task_id, owner=owner, receipt=name, review=name, input=name
            )
            prepared = fp.authoring.prepare(program, state, args, observed_head=HEAD)
            fp.authoring._preflight(program, prepared)
        return {"status": "ready", "missing_fields": [], "reasons": []}


@pytest.mark.parametrize("raw", [b"[]", b"17", b"null", b"\x00\xff", b""])
def test_opaque_artifact_bytes_survive_planning_copy_and_graph_validation(fixture, raw):
    # Given exact raw bytes, including nonobject JSON and empty binary output.
    original = reference(fixture, "design/raw.json", raw)
    before = fixture.program.path("backlog.json").read_bytes()
    # When planning and materializing via the existing exact copier.
    result = copied(fixture, [original])
    row = result["targets"][0]["attachments"][0]
    manifest_ref = result["targets"][0]["manifest_ref"]
    manifest = fp.read_json(fixture.program.path(manifest_ref["path"]))
    refs = reuse.validate_manifest(fixture.program, manifest)
    # Then originals/copies retain exact bytes and no task state or successful check is invented.
    assert fixture.program.path(row["attachment_ref"]["path"]).read_bytes() == raw
    assert fixture.program.path(original["path"]).read_bytes() == raw
    assert row["byte_count"] == len(raw)
    assert original in refs and row["attachment_ref"] in refs
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert result["accepted"] is False
    assert row["attachment_ref"]["path"].endswith(".json")


@pytest.mark.parametrize("role", ["decision", "control_receipt"])
def test_typed_control_or_decision_is_not_allowed_to_be_a_raw_array(fixture, role):
    # Given an explicit object role on nonobject bytes.
    ref = reference(fixture, "design/array.json", b"[]")
    # When that typed selection is checked, its strict control parser rejects.
    with pytest.raises(fp.ProgramError):
        reuse.plan_evidence(fixture.program, fixture.program.load(), selection([ref], role=role))


@pytest.mark.parametrize("change", ["missing", "tamper", "count", "scope"])
def test_retained_graph_rejects_missing_changed_or_reattributed_target(fixture, change):
    # Given a validated copy graph including a zero-byte original.
    result = copied(fixture, [reference(fixture, "design/empty.bin", b"")])
    manifest = fp.read_json(fixture.program.path(result["targets"][0]["manifest_ref"]["path"]))
    row = manifest["attachments"][0]
    path = fixture.program.path(row["attachment_ref"]["path"])
    # When its target bytes, size or task namespace change.
    match change:
        case "missing":
            path.unlink()
        case "tamper":
            path.write_bytes(b"changed")
        case "count":
            row["byte_count"] = 1
        case "scope":
            manifest["task_id"] = "S02"
    # Then the exact transitive graph cannot be retained as valid evidence.
    with pytest.raises(fp.ProgramError):
        reuse.validate_manifest(fixture.program, manifest)


def test_changed_source_prevents_materialization_without_writing_outputs(fixture):
    # Given a plan for the actual owned source.
    ref = reference(fixture, "design/report.txt")
    plan = reuse.plan_evidence(fixture.program, fixture.program.load(), selection([ref]))
    # When owned bytes drift before materialization.
    (fixture.program.repo / "src/S01.py").write_bytes(b"changed source")
    with pytest.raises(fp.ProgramError):
        reuse.materialize_evidence(fixture.program, fixture.program.load(), plan)
    # Then no guessed future-file preparation or canonical update occurs.
    assert not fixture.program.path(plan["targets"][0]["manifest_path"]).exists()


def test_identical_materialization_reuses_exact_outputs_without_overwrite(fixture):
    # Given a complete plan with an explicit check mapping.
    ref = reference(fixture, "design/report.txt")
    selected = selection([ref])
    selected["targets"][0]["mappings"] = [
        {"family": "check", "identity": "scoped_tests", "keys": ["item0"]}
    ]
    plan = reuse.plan_evidence(fixture.program, fixture.program.load(), selected)
    first = reuse.materialize_evidence(fixture.program, fixture.program.load(), plan)
    before = fixture.program.path(first["targets"][0]["manifest_ref"]["path"]).read_bytes()
    # When the same immutable request is retried, no new command or receipt is created.
    second = reuse.materialize_evidence(fixture.program, fixture.program.load(), plan)
    assert first == second
    assert fixture.program.path(first["targets"][0]["manifest_ref"]["path"]).read_bytes() == before
    assert reuse.summarize_preparation(second)["affected_checks"] == ["scoped_tests"]


def test_later_copy_conflict_is_detected_before_any_materialization(fixture):
    # Given a later conflicting exact destination.
    refs = [
        reference(fixture, "design/first.txt"),
        reference(fixture, "design/second.txt", b"second"),
    ]
    plan = reuse.plan_evidence(fixture.program, fixture.program.load(), selection(refs))
    second = plan["targets"][0]["attachments"][1]["attachment_ref"]["path"]
    reference(fixture, second, b"conflict")
    # When the full plan is checked, earlier destinations remain absent.
    with pytest.raises(fp.ProgramError):
        reuse.materialize_evidence(fixture.program, fixture.program.load(), plan)
    assert not fixture.program.path(
        plan["targets"][0]["attachments"][0]["attachment_ref"]["path"]
    ).exists()


def test_actual_acceptance_decision_and_observations_prepare_normal_submission(fixture):
    # Given synthetic actual-fact observations on an isolated program, not native product proof.
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    state = fixture.program.load()
    initial = reuse.prepare_receipt(
        fixture.program, state, "submission", {"task_id": "S01", "owner": "worker"}
    )
    raw_ref = reference(fixture, "design/criterion.txt")
    facts = {
        "summary": "Fixture acceptance",
        "next_action": "Independent review",
        "limitations": [],
        "acceptance_complete": True,
        "acceptance_evidence": [
            {
                "criterion": "Complete original scope",
                "status": "passed",
                "evidence": [raw_ref["path"]],
            }
        ],
    }
    actual = decision(fixture, initial["binding"], facts)
    evidence = copied(fixture, [raw_ref, actual])
    adapter = NormalAdapter()
    before = fixture.program.path("backlog.json").read_bytes()
    # When exact selected decisions feed ordinary detached submit validation.
    result = reuse.prepare_receipt(
        fixture.program,
        state,
        "submission",
        {
            "task_id": "S01",
            "owner": "worker",
            "decision_refs": [actual],
            "observation_ids": ["finished-1"],
            "materialization": evidence,
        },
        adapter=adapter,
    )
    # Then a normal ready draft retains exact claims and canonical state is untouched.
    assert result["status"] == "ready"
    assert result["accepted"] is False and result["executed"] is False
    assert (
        reuse.validate_selection(fixture.program, fixture.program.load(), result)["summary"][
            "status"
        ]
        == "ready"
    )
    assert result["receipt"]["verification_refs"][0]["id"] == "finished-1"
    assert result["receipt"]["evidence_provenance_ref"] == evidence["targets"][0]["manifest_ref"]
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_missing_real_acceptance_decision_cannot_reach_normal_adapter(fixture):
    # Given successful check observations but no actual criterion decision.
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    adapter = NormalAdapter()
    # When the ordinary draft is prepared without inventing human acceptance.
    result = reuse.prepare_receipt(
        fixture.program,
        fixture.program.load(),
        "submission",
        {"task_id": "S01", "owner": "worker", "observation_ids": ["finished-1"]},
        adapter=adapter,
    )
    assert result["status"] == "incomplete"
    assert result["receipt"]["acceptance_complete"] is False
    assert adapter.assessed == []


def test_changed_exact_decision_subject_rejects_reuse(fixture):
    # Given one immutable exact-subject decision.
    initial = reuse.prepare_receipt(
        fixture.program, fixture.program.load(), "submission", {"task_id": "S01", "owner": "worker"}
    )
    actual = decision(fixture, initial["binding"], {"acceptance_complete": True})
    (fixture.program.repo / "src/S01.py").write_bytes(b"new source")
    # When its subject source changes, the original is retained but not rebound.
    with pytest.raises(fp.ProgramError):
        reuse.prepare_receipt(
            fixture.program,
            fixture.program.load(),
            "submission",
            {"task_id": "S01", "owner": "worker", "decision_refs": [actual]},
        )


def test_binding_only_completion_uses_explicit_nonpassing_empty_facts(gate):
    # Given a real initialized isolated goal with no director completion decision.
    before = gate.program.path("backlog.json").read_bytes()
    # When binding-only preparation adapts omission to facts={} for the existing callback.
    result = reuse.prepare_receipt(
        gate.program, gate.program.load(), "completion", {"scope": ["src/S01.py", "src/S02.py"]}
    )
    assert result["status"] == "incomplete"
    assert result["receipt"]["actor_id"] is None
    assert all(row["status"] == "not_run" for row in result["receipt"]["criteria_results"])
    assert result["accepted"] is False
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "current,expected", [(True, "ready"), (False, "stale"), (None, "unavailable")]
)
def test_safe_retained_summary_uses_complete_host_currentness_without_raw_payload(
    fixture, current, expected
):
    # Given a complete host-verified copy graph and explicit consuming currentness.
    copied_result = copied(fixture, [reference(fixture, "design/private-name.txt")])
    ref = copied_result["targets"][0]["manifest_ref"]
    manifest = fp.read_json(fixture.program.path(ref["path"]))
    rows = [
        {
            "manifest_ref": ref,
            "manifest": manifest,
            "control_ref": ref,
            "task_id": "S01",
            "attempt": 1,
            "current": current,
        }
    ]
    # When the normal server projects retained evidence, it does not invent a new preparation/pass.
    result = reuse.project_retained(fixture.program.load(), rows)
    assert result["status"] == expected
    assert result["counts"]["retained"] == 1
    assert b"private-name" not in fp.canonical(result)
    assert reuse.project_retained(fixture.program.load(), []) is None


def test_exact_submitted_review_decision_reuses_original_and_normal_validator(fixture):
    # Given a real isolated submission and an attributable exact-subject reviewer decision.
    fixture.submit()
    state = fixture.program.load()
    initial = reuse.prepare_receipt(
        fixture.program, state, "review", {"task_id": "S01", "scope": ["src/S01.py"], "owner": "qa"}
    )
    note = reference(fixture, "design/reviewer-report.txt")
    facts = {
        "verdict": "accepted",
        "reviewer_role": "independent",
        "acceptance_complete": True,
        "unresolved_findings": [],
        "evidence": [note["path"]],
        "next_action": "Delivery",
    }
    actual = decision(fixture, initial["binding"], facts, actor="qa")
    evidence = copied(fixture, [note, actual])
    before = fixture.program.path("backlog.json").read_bytes()
    original = fixture.program.path(actual["path"]).read_bytes()
    # When the adapter prepares an ordinary review against that exact receipt/source.
    result = reuse.prepare_receipt(
        fixture.program,
        state,
        "review",
        {
            "task_id": "S01",
            "scope": ["src/S01.py"],
            "owner": "qa",
            "decision_refs": [actual],
            "materialization": evidence,
        },
        adapter=NormalAdapter(),
    )
    assert result["status"] == "ready"
    assert (
        reuse.validate_selection(fixture.program, fixture.program.load(), result)["summary"][
            "status"
        ]
        == "ready"
    )
    assert result["receipt"]["reviewer"] == "qa"
    assert (
        result["receipt"]["receipt_sha256"]
        == state["tasks"][0]["attempts"][-1]["submission"]["receipt"]["sha256"]
    )
    assert fixture.program.path(actual["path"]).read_bytes() == original
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_pre_submission_subject_cannot_approve_an_unseen_submitted_receipt(fixture):
    # Given an actual decision for the active, not-yet-submitted subject.
    before = reuse.prepare_receipt(
        fixture.program, fixture.program.load(), "submission", {"task_id": "S01", "owner": "worker"}
    )
    actual = decision(fixture, before["binding"], {"verdict": "accepted"}, actor="qa")
    fixture.submit()
    # When the later submitted review subject is supplied, the old actor is not impersonated.
    with pytest.raises(fp.ProgramError):
        reuse.prepare_receipt(
            fixture.program,
            fixture.program.load(),
            "review",
            {"task_id": "S01", "scope": ["src/S01.py"], "decision_refs": [actual]},
        )


def test_decision_cannot_override_mechanical_receipt_identity(fixture):
    # Given a reviewer decision attempting to inject a different submitted receipt hash.
    fixture.submit()
    initial = reuse.prepare_receipt(
        fixture.program,
        fixture.program.load(),
        "review",
        {"task_id": "S01", "scope": ["src/S01.py"]},
    )
    actual = decision(
        fixture, initial["binding"], {"verdict": "accepted", "receipt_sha256": "f" * 64}, actor="qa"
    )
    with pytest.raises(fp.ProgramError):
        reuse.prepare_receipt(
            fixture.program,
            fixture.program.load(),
            "review",
            {"task_id": "S01", "scope": ["src/S01.py"], "decision_refs": [actual]},
        )


def test_independent_aggregate_decision_uses_normal_exact_source_validator(gate):
    # Given explicit aggregate scope and an actual isolated independent reviewer decision.
    scope = ["src/S01.py", "src/S02.py"]
    initial = reuse.prepare_receipt(
        gate.program, gate.program.load(), "integrated_review", {"scope": scope}
    )
    facts = {
        "verdict": "accepted",
        "reviewer_role": "independent",
        "acceptance_complete": True,
        "unresolved_findings": [],
        "criteria": initial["binding"]["criteria"],
        "evidence": [],
    }
    actual = decision(gate.fixture, initial["binding"], facts, actor="independent")
    evidence = copied(gate.fixture, [actual], task_id="_coordination")
    before = gate.program.path("backlog.json").read_bytes()
    # When adapting its exact immutable outcome, normal integrated review checks still run.
    result = reuse.prepare_receipt(
        gate.program,
        gate.program.load(),
        "integrated_review",
        {
            "scope": scope,
            "owner": "director",
            "decision_refs": [actual],
            "materialization": evidence,
        },
        adapter=NormalAdapter(),
    )
    assert result["status"] == "ready"
    assert result["receipt"]["reviewer"] == "independent"
    assert result["receipt"]["source_digest"] == initial["binding"]["source_digest"]
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("exit_code,expected", [(0, "unknown"), (7, "failed")])
def test_retained_structured_tool_result_records_honestly_without_pass_inference(
    fixture, exit_code, expected
):
    # Given synthetic retained command facts with no original input closure or compatible clock.
    record(fixture, request(fixture))
    actual = [
        {
            "argv": ["tool", "same", "same", ""],
            "cwd": "/recorded/workspace",
            "started_at": "2026-10-14T10:00:00+00:00",
            "ended_at": "2026-10-14T10:00:01+00:00",
            "exit_code": exit_code,
            "stdout_sha256": fp.sha(b""),
        }
    ]
    outcome = reference(fixture, "design/tool-manifest.json", fp.canonical(actual))
    stream = reference(fixture, "design/stdout.bin", b"")
    evidence = copied(fixture, [outcome, stream])
    # When its exact original array/index is interpreted once without log-text inference.
    result = reuse.prepare_receipt(
        fixture.program,
        fixture.program.load(),
        "observation",
        {
            "task_id": "S01",
            "owner": "qa",
            "outcome_ref": outcome,
            "outcome_index": 0,
            "outcome_streams": {"stdout": stream},
            "materialization": evidence,
            "request_binding": {
                "observation_id": "retained-finish",
                "actor_id": "qa",
                "request_id": "request-1",
                "request_version": 1,
                "reason": "Retained fixture outcome; input closure unknown",
                "execution_kind": "test",
            },
        },
        adapter=NormalAdapter(),
    )
    assert result["status"] == "ready"
    assert result["receipt"]["result"] == expected
    assert result["receipt"]["method"]["argv"] == actual[0]["argv"]
    assert "input_before" not in result["receipt"] and "observed_at" not in result["receipt"]
    assert result["related_inputs"]["retained_outcome"]["actual"] == actual[0]
    assert result["related_inputs"]["retained_outcome"]["actual_executor_id"] is None


def test_pass_prose_is_not_a_retained_structured_outcome(fixture):
    # Given raw text that claims success without original execution facts.
    original = reference(fixture, "design/prose.txt", b"PASS all checks")
    evidence = copied(fixture, [original])
    with pytest.raises(fp.ProgramError):
        reuse.prepare_receipt(
            fixture.program,
            fixture.program.load(),
            "observation",
            {"task_id": "S01", "outcome_ref": original, "materialization": evidence},
        )


def test_actual_counter_capture_prepares_repeatedly_without_command_replay(fixture):
    # Given one actual harmless child invocation and its shipped sealed capture producer.
    from tools.tests.test_feature_program_execution import prepare as prepare_run

    execution = fp.flow_module("execution")
    prepared = prepare_run(
        fixture, "from pathlib import Path; Path('reuse-counter.txt').write_text('1')"
    )
    actual = execution.execute_run(prepared)
    ref = actual["capture_ref"]
    evidence = copied(fixture, [ref])
    before = fixture.program.path("backlog.json").read_bytes()
    inputs = {
        "task_id": "S01",
        "owner": "qa",
        "capture_ref": ref,
        "request_binding": prepared.binding,
        "materialization": evidence,
    }
    # When the same immutable outcome is prepared twice through ordinary validation.
    first = reuse.prepare_receipt(
        fixture.program, fixture.program.load(), "observation", inputs, adapter=NormalAdapter()
    )
    second = reuse.prepare_receipt(
        fixture.program, fixture.program.load(), "observation", inputs, adapter=NormalAdapter()
    )
    assert first["status"] == second["status"] == "ready"
    assert first["receipt"] == second["receipt"]
    assert (
        reuse.validate_selection(fixture.program, fixture.program.load(), first)["summary"][
            "status"
        ]
        == "ready"
    )
    assert first["receipt"]["result"] == "passed"
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert (fixture.program.repo / "reuse-counter.txt").read_text() == "1"


def test_omitted_actor_remains_explicitly_incomplete(fixture):
    # Given a current task with no explicit acting owner or criterion decision.
    result = reuse.prepare_receipt(
        fixture.program, fixture.program.load(), "submission", {"task_id": "S01"}
    )
    # Then a binding can be returned but its inferred template owner grants no readiness.
    assert result["status"] == "incomplete"
    assert "acting_owner" in result["missing_fields"]
    assert result["next_actor_id"] is None


@pytest.mark.parametrize("kind", ["plan", "materialized", "receipt"])
def test_saved_selection_validates_explicit_typed_graph_and_source_currentness(fixture, kind):
    # Given one actual explicit producer result rather than a claimed ready dictionary.
    original = reference(fixture, "design/selected.json", b"[]")
    state = fixture.program.load()
    plan = reuse.plan_evidence(fixture.program, state, selection([original]))
    materialized = reuse.materialize_evidence(fixture.program, state, plan)
    value = plan if kind == "plan" else materialized
    if kind == "receipt":
        value = reuse.prepare_receipt(
            fixture.program,
            state,
            "submission",
            {"task_id": "S01", "owner": "worker", "materialization": materialized},
        )
    # When selected for display the original refs are frozen and later source drift is visible.
    assessed = reuse.validate_selection(fixture.program, state, value)
    assert original in assessed["refs"]
    assert assessed["summary"]["status"] == ("incomplete" if kind == "receipt" else "ready")
    assert b"design/selected" not in fp.canonical(assessed["summary"])
    (fixture.program.repo / "src/S01.py").write_bytes(b"changed subject")
    changed = reuse.validate_selection(fixture.program, fixture.program.load(), value)
    assert changed["summary"]["status"] == "stale"
    assert changed["summary"]["reasons"] == ["stale_binding"]


@pytest.mark.parametrize(
    "mutation", ["bare_ready", "copy_hash", "target_reattribute", "manifest_missing"]
)
def test_explicit_selection_rejects_malformed_or_corrupt_evidence(fixture, mutation):
    # Given a real selected copy graph, never arbitrary status-only browser data.
    value = copied(fixture, [reference(fixture, "design/source.json", b"[]")])
    if mutation == "bare_ready":
        value = {"schema_version": 1, "kind": "receipt_preparation", "status": "ready"}
    elif mutation == "copy_hash":
        fixture.program.path(
            value["targets"][0]["attachments"][0]["attachment_ref"]["path"]
        ).write_bytes(b"tampered")
    elif mutation == "target_reattribute":
        value["targets"][0]["binding"]["task_id"] = "S02"
    else:
        fixture.program.path(value["targets"][0]["manifest_ref"]["path"]).unlink()
    # Then a malformed envelope or changed immutable evidence cannot project a ready result.
    with pytest.raises((fp.ProgramError, OSError)):
        reuse.validate_selection(fixture.program, fixture.program.load(), value)


def test_later_adverse_observation_stales_saved_preparation_without_global_revision_binding(
    fixture,
):
    # Given an incomplete preparation bound to the actual task history.
    state = fixture.program.load()
    value = reuse.prepare_receipt(
        fixture.program, state, "submission", {"task_id": "S01", "owner": "worker"}
    )
    unrelated = deepcopy(state)
    unrelated["revision"] = 123456
    assert (
        reuse.validate_selection(fixture.program, unrelated, value)["summary"]["status"]
        == "incomplete"
    )
    # When a real request and newer failed outcome arrive, an old draft cannot stay current.
    record(fixture, request(fixture))
    record(fixture, observation(fixture, result="failed"))
    assert (
        reuse.validate_selection(fixture.program, fixture.program.load(), value)["summary"][
            "status"
        ]
        == "stale"
    )


@pytest.mark.parametrize("conclusion", ["success", "failure", "pending"])
def test_delivery_reuses_exact_provider_outcome_and_real_cleanup_decision(gate, conclusion):
    # Given completed isolated tasks and a labeled synthetic original provider response.
    from tools.tests.test_feature_program_authoring import complete_tasks, provider_request

    complete_tasks(gate)
    supplied = provider_request(gate, conclusion)
    state = gate.program.load()
    initial = reuse.prepare_receipt(gate.program, state, "delivery", {"task_id": "S01"})
    cleanup_note = reference(
        gate.fixture, "design/cleanup-inspection.txt", b"Actual labeled fixture cleanup inspection"
    )
    facts = {
        "required_ci": ["unit"],
        "cleanup": {
            "status": "deferred",
            "reason": "Keep fixture branch for inspection",
            "actor_id": "director",
            "merge_identity": "b" * 40,
            "recorded_at": "2026-10-15T00:00:00+00:00",
            "evidence": [cleanup_note["path"]],
        },
    }
    actual = decision(gate.fixture, initial["binding"], facts, actor="director")
    evidence = copied(gate.fixture, [actual, supplied["provider"], cleanup_note])
    before = gate.program.path("backlog.json").read_bytes()
    # When ordinary delivery preparation consumes its existing provider assessor.
    result = reuse.prepare_receipt(
        gate.program,
        state,
        "delivery",
        {
            "task_id": "S01",
            "owner": "director",
            "decision_refs": [actual],
            "provider_ref": supplied["provider"],
            "materialization": evidence,
        },
        adapter=NormalAdapter(),
    )
    assert result["status"] == "ready"
    assert result["receipt"]["ci"][0]["conclusion"] == conclusion
    assert result["receipt"]["cleanup"]["status"] == "deferred"
    assert result["receipt"]["provider_observation"] == supplied["provider"]
    assert result["remote_verified_by_tool"] is False
    assert gate.program.path("backlog.json").read_bytes() == before


def _completion_preparation(gate):
    # Given actual isolated task acceptance and ordinary delivery records, never project acceptance.
    from tools.tests.test_feature_program_authoring import complete_tasks, provider_request

    complete_tasks(gate)
    supplied = provider_request(gate)
    for row in fp.authoring.delivery_inputs(gate.program, gate.program.load(), supplied)["inputs"]:
        name = gate.fixture.write(
            f"evidence/{row['task_id']}/attempt-1/provider-delivery.json", row["receipt"]
        )
        assert gate.fixture.run("delivery", row["task_id"], "--receipt", name) == 0
    scope = ["src/S01.py", "src/S02.py"]
    state = gate.program.load()
    initial = reuse.prepare_receipt(gate.program, state, "integrated_review", {"scope": scope})
    actual_review = decision(
        gate.fixture,
        initial["binding"],
        {
            "verdict": "accepted",
            "reviewer_role": "independent",
            "acceptance_complete": True,
            "unresolved_findings": [],
            "criteria": initial["binding"]["criteria"],
            "evidence": [],
        },
        actor="independent",
        name="aggregate-review-decision",
    )
    review_materialized = copied(
        gate.fixture, [actual_review], task_id="_coordination", prefix="review-proof"
    )
    review = reuse.prepare_receipt(
        gate.program,
        state,
        "integrated_review",
        {
            "scope": scope,
            "owner": "director",
            "decision_refs": [actual_review],
            "materialization": review_materialized,
        },
        adapter=NormalAdapter(),
    )
    review_ref = reference(
        gate.fixture, "design/actual-independent-review.json", fp.canonical(review["receipt"])
    )
    initial_completion = reuse.prepare_receipt(gate.program, state, "completion", {"scope": scope})
    criteria = deepcopy(initial_completion["receipt"]["criteria_results"])
    for criterion in criteria:
        criterion.update(status="passed", evidence=[review_ref["path"]])
    facts = {
        "summary": "Labeled isolated final criterion inspection",
        "criteria_results": criteria,
        "remote_state_checked": True,
        "required_ci": ["unit"],
        "integrated_review": review_ref["path"],
        "unresolved_findings": [],
        "evidence": [review_ref["path"]],
    }
    beta_provider, beta_context = receiving_beta_observation(gate.fixture)
    facts["required_ci"] = ["dashboard"]
    actual_director = decision(
        gate.fixture,
        initial_completion["binding"],
        facts,
        actor="director",
        name="director-decision",
    )
    materialized = copied(
        gate.fixture,
        [actual_director, review_ref, beta_provider],
        task_id="_coordination",
        prefix="completion-proof",
    )
    before = gate.program.path("backlog.json").read_bytes()
    # When the original verdicts are attached once and ordinary completion validation runs.
    result = reuse.prepare_receipt(
        gate.program,
        state,
        "completion",
        {
            "scope": scope,
            "owner": "director",
            "decision_refs": [actual_director],
            "provider_ref": beta_provider,
            "provider_context": beta_context,
            "materialization": materialized,
        },
        adapter=NormalAdapter(),
    )
    assert result["status"] == "ready"
    assert result["receipt"]["actor_id"] == "director"
    assert result["receipt"]["ci"][0]["commit"] == HEAD
    assert result["receipt"]["unresolved_findings"] == []
    assert result["accepted"] is False
    assert gate.program.path("backlog.json").read_bytes() == before
    return {"completion": result, "integrated_review": review}


def test_completion_retains_real_director_and_independent_decisions_through_normal_preflight(gate):
    _completion_preparation(gate)


@pytest.mark.parametrize("kind", ["plan", "materialized", "receipt"])
def test_selected_old_attempt_is_retained_as_stale_without_reattribution(fixture, kind):
    # Given an exact saved result from the first actual isolated task attempt.
    state = fixture.program.load()
    original = reference(fixture, "design/attempt-one.txt")
    plan = reuse.plan_evidence(fixture.program, state, selection([original]))
    materialized = reuse.materialize_evidence(fixture.program, state, plan)
    value = plan if kind == "plan" else materialized
    if kind == "receipt":
        value = reuse.prepare_receipt(
            fixture.program,
            state,
            "submission",
            {"task_id": "S01", "owner": "worker", "materialization": materialized},
        )
    assert (
        fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Explicit isolated retry",
            "--next-action",
            "Start new attempt",
        )
        == 0
    )
    assert fixture.claim(number=2) == 0
    # When explicitly selected after retry, its original byte identity survives but is not current.
    assessed = reuse.validate_selection(fixture.program, fixture.program.load(), value)
    assert assessed["summary"]["status"] == "stale"
    assert original in assessed["refs"]
    assert (
        value["binding"]["attempt"] == 1
        if kind == "receipt"
        else value["targets"][0]["binding"]["attempt"] == 1
    )


def test_saved_preparation_tracks_broader_declared_check_inputs_not_only_owned_files(fixture):
    # Given a real adopted check that explicitly reads another task's source as a test dependency.
    value = request(fixture)
    value["input_scope"] = ["src/S01.py", "src/S02.py"]
    record(fixture, value)
    finished = observation(fixture)
    digest = fixture.program.load()["verification"]["records"][0]["context"]["input_digest"]
    finished.update(input_before=digest, input_after=digest)
    record(fixture, finished)
    state = fixture.program.load()
    result = reuse.prepare_receipt(
        fixture.program,
        state,
        "submission",
        {"task_id": "S01", "owner": "worker", "observation_ids": ["finished-1"]},
    )
    owned_before = fixture.program.source_digest(["src/S01.py"])
    # When an explicitly declared broader dependency changes, the saved draft becomes stale.
    (fixture.program.repo / "src/S02.py").write_bytes(b"changed broader check dependency")
    assert fixture.program.source_digest(["src/S01.py"]) == owned_before
    assert (
        reuse.validate_selection(fixture.program, fixture.program.load(), result)["summary"][
            "status"
        ]
        == "stale"
    )


def receiving_beta_observation(fixture):
    """A labeled synthetic actual provider capture with distinct PR and receiving-beta identities."""
    from tools.tests.test_feature_program_delivery import fixture as provider_fixture, BETA

    execution, context = provider_fixture(beta=True)
    context["beta_sha"] = HEAD
    context["stages"][-1]["expected_sha"] = HEAD
    beta = execution["stages"][-1]
    beta["expected_sha"] = HEAD
    beta["observations"][-1].update(head_sha=HEAD, checkout_sha=HEAD)
    execution["merge"].update(squash_sha=HEAD, receiving_sha=HEAD)
    assert BETA != HEAD
    provider = {
        "execution": execution,
        "ci": [
            {
                "name": "dashboard",
                "url": "https://example.invalid/actual-beta-run/101",
                "commit": HEAD,
                "conclusion": "success",
            }
        ],
    }
    ref = reference(fixture, "design/receiving-beta-provider.json", fp.canonical(provider))
    return ref, context


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_commit",
        "duplicate_job",
        "missing_job",
        "pr_only",
        "unqualified_skip",
        "missing_context",
    ],
)
def test_objective_ci_adaptation_requires_assessed_actual_receiving_beta(gate, fault):
    # Given one selected actual receiving-beta execution, with any contradictory fact explicit.
    provider_ref, context = receiving_beta_observation(gate.fixture)
    provider = fp.read_json(gate.program.path(provider_ref["path"]))
    if fault == "wrong_commit":
        provider["ci"][0]["commit"] = "f" * 40
    elif fault == "duplicate_job":
        provider["ci"].append(deepcopy(provider["ci"][0]))
    elif fault == "missing_job":
        provider["execution"]["stages"][-1]["observations"][-1]["jobs"] = []
    elif fault == "pr_only":
        context["stages"] = context["stages"][:1]
        context["beta_sha"] = None
        provider["execution"]["stages"] = provider["execution"]["stages"][:1]
        provider["execution"]["merge"] = None
    elif fault == "unqualified_skip":
        provider["execution"]["stages"][-1]["observations"][-1]["jobs"][0]["conclusion"] = "skipped"
        provider["ci"][0]["conclusion"] = "skipped"
    else:
        context = None
    provider_ref = reference(
        gate.fixture, "design/receiving-beta-provider.json", fp.canonical(provider)
    )
    materialized = copied(gate.fixture, [provider_ref], task_id="_coordination")
    inputs = {
        "scope": ["src/S01.py", "src/S02.py"],
        "provider_ref": provider_ref,
        "provider_context": context,
        "materialization": materialized,
    }
    # Then PR-only/current-hash coincidences, missing/duplicate jobs and skips cannot become a pass.
    if fault in {"unqualified_skip", "missing_context"}:
        result = reuse.prepare_receipt(gate.program, gate.program.load(), "completion", inputs)
        assert result["status"] == "incomplete"
        assert any("receiving_beta" in str(field) for field in result["missing_fields"])
        assert result["accepted"] is False
    else:
        with pytest.raises(fp.ProgramError):
            reuse.prepare_receipt(gate.program, gate.program.load(), "completion", inputs)


@pytest.mark.parametrize(
    "outcome",
    [
        {"argv": ["tool"], "cwd": "/retained", "exit_code": None, "outcome": "interrupted"},
        {"method": {"procedure": "Original manual inspection"}, "result": "passed"},
    ],
)
def test_retained_interrupted_or_missing_identity_stays_incomplete_without_replay(fixture, outcome):
    # Given a retained unknown exit or manual outcome whose full execution identity was not captured.
    record(fixture, request(fixture))
    ref = reference(fixture, "design/incomplete-outcome.json", fp.canonical(outcome))
    materialized = copied(fixture, [ref])
    adapter = NormalAdapter()
    # When actual missing facts cannot be recovered by current assertion or PASS text.
    result = reuse.prepare_receipt(
        fixture.program,
        fixture.program.load(),
        "observation",
        {
            "task_id": "S01",
            "owner": "qa",
            "outcome_ref": ref,
            "materialization": materialized,
            "request_binding": {
                "observation_id": "retained-incomplete",
                "actor_id": "qa",
                "request_id": "request-1",
                "request_version": 1,
                "reason": "Original outcome incomplete",
                "execution_kind": "manual",
            },
        },
        adapter=adapter,
    )
    assert result["status"] == "incomplete"
    assert result["receipt"] == {}
    assert result["related_inputs"]["retained_outcome"]["actual"] == outcome
    assert adapter.assessed == []
    assert result["executed"] is False


def test_multi_target_plan_shares_existing_source_read_limit(fixture, monkeypatch):
    # Given two explicitly claimed tasks whose combined unique sources exceed a tiny test budget.
    assert fixture.claim("S02", owner="worker-two") == 0
    ref = reference(fixture, "design/source-budget.txt")
    selected = selection([ref])
    second = deepcopy(selected["targets"][0])
    second.update(task_id="S02", prefix="evidence/S02/attempt-1/proof")
    selected["targets"].append(second)
    state = fixture.program.load()
    read_budget = fp.ReadBudget
    limit = (
        sum(
            (fixture.program.repo / f"src/{task_id}.py").stat().st_size
            for task_id in ("S01", "S02")
        )
        - 1
    )
    monkeypatch.setattr(
        fp, "ReadBudget", lambda size: read_budget(limit if size == 128 * 1024 * 1024 else size)
    )
    # When one selected plan checks all targets, its source allowance cannot reset per target.
    with pytest.raises(fp.ProgramError, match="bounded read"):
        reuse.plan_evidence(fixture.program, state, selected)
    assert not fixture.program.path("evidence/S01/attempt-1/proof").exists()


def _director_transfer(gate, successor="director-next"):
    scope = ["src/S01.py", "src/S02.py"]
    binding = {
        "scope": scope,
        "source_head": HEAD,
        "source_digest": gate.program.source_digest(scope),
    }
    actor = {
        "id": successor,
        "role": "director",
        "parent_id": None,
        "model_id": None,
        "reasoning_effort": None,
        "provenance": "unknown",
        "observed_status": "unknown",
        "decision": "new",
        "reason": "Explicit isolated successor registration",
    }
    name = gate.fixture.write("evidence/_coordination/attempt-1/successor-actor.json", actor)
    assert gate.fixture.run("actor-record", "--input", name) == 0
    outgoing = {
        **binding,
        "id": "authority-handoff",
        "role": "director",
        "from_actor": "director",
        "to_actor": successor,
        "task_id": None,
        "goal": "Preserve scoped work",
        "constraints": ["Preserve original evidence"],
        "changed_paths": [],
        "decisions": ["Actual isolated authority transfer"],
        "evidence": [],
        "unresolved_findings": [],
        "next_action": "Inspect accepted work",
        "authority": {
            "authorized_by": "synthetic-human",
            "basis": "Explicit isolated succession",
            "merge_target": "beta",
        },
    }
    for command, receipt in [
        ("transfer", outgoing),
        (
            "transfer-ack",
            {
                **binding,
                "transfer_id": "authority-handoff",
                "actor_id": successor,
                "summary": "Inspected current exact source and preserved findings",
                "evidence": [],
            },
        ),
        (
            "transfer-accept",
            {
                **binding,
                "transfer_id": "authority-handoff",
                "actor_id": "director",
                "summary": "Accepted actual isolated succession",
                "evidence": [],
            },
        ),
    ]:
        name = gate.fixture.write(f"evidence/_coordination/attempt-1/{command}.json", receipt)
        assert gate.fixture.run(command, "--input", name) == 0


def test_saved_ready_completion_rechecks_actual_current_director_after_accepted_handoff(
    gate, monkeypatch
):
    # Given an actual ready ordinary preparation from the accepted original director.
    saved = _completion_preparation(gate)["completion"]
    normal = fp.authoring.EvidenceReceiptAdapter()
    before = gate.program.load()
    assert (
        normal.assess(gate.program, before, "completion", saved["receipt"], owner="director")[
            "status"
        ]
        == "ready"
    )
    assert reuse.validate_selection(gate.program, before, saved)["summary"]["status"] == "ready"
    # When normal transfer/ack/accept changes only actual final authority, not the source/goal/tasks.
    _director_transfer(gate)
    current = gate.program.load()
    assert fp.coordination.current_director_id(current) == "director-next"
    assert current["objective"] == before["objective"]
    assert current["tasks"] == before["tasks"]
    assert (
        gate.program.source_digest(saved["binding"]["scope"]) == saved["binding"]["source_digest"]
    )
    rejected = normal.assess(
        gate.program, current, "completion", saved["receipt"], owner="director"
    )
    assert rejected["status"] == "incomplete"
    assert any("current final director" in reason for reason in rejected["reasons"])

    # Then selection is read-only and cannot call the inert-file writing ordinary assessor.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Saved-result selection must not prepare files")

    before_bytes = gate.program.path("backlog.json").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(fp.authoring.EvidenceReceiptAdapter, "assess", forbidden)
        patch.setattr(fp.authoring, "freeze_document", forbidden)
        assert (
            reuse.validate_selection(gate.program, current, saved)["summary"]["status"] == "stale"
        )
    assert gate.program.path("backlog.json").read_bytes() == before_bytes


def test_saved_integrated_reviewer_cannot_become_current_director(gate):
    # Given an actually independent aggregate review that ordinary assessment accepted.
    saved = _completion_preparation(gate)["integrated_review"]
    normal = fp.authoring.EvidenceReceiptAdapter()
    assert (
        normal.assess(
            gate.program,
            gate.program.load(),
            "integrated_review",
            saved["receipt"],
            owner="director",
        )["status"]
        == "ready"
    )
    # When that actual reviewer legitimately succeeds as director, the original bytes stay historical.
    _director_transfer(gate, successor="independent")
    current = gate.program.load()
    rejected = normal.assess(
        gate.program, current, "integrated_review", saved["receipt"], owner="director"
    )
    assert rejected["status"] == "incomplete"
    assert any("independent" in reason for reason in rejected["reasons"])
    assert reuse.validate_selection(gate.program, current, saved)["summary"]["status"] == "stale"


def test_same_epoch_metadata_reads_preserve_each_hash_and_fresh_later_admission(
    fixture, monkeypatch
):
    # Given one explicitly captured immutable metadata image and its exact expected reference.
    ref = reference(fixture, "evidence/S01/attempt-1/checked-image.bin", b"original exact bytes")
    reader = fp.ReadBudget()
    original = fp._checked_safe_path
    admissions = []

    def counted(root, name, components=None):
        if root == fixture.program.directory and name == ref["path"]:
            admissions.append(name)
        return original(root, name, components)

    monkeypatch.setattr(fp, "_checked_safe_path", counted)
    # When several consumers use the same existing ledger and still supply their own hash.
    assert reuse._read(fixture.program, ref, reader) == b"original exact bytes"
    assert reuse._read(fixture.program, ref, reader) == b"original exact bytes"
    assert len(admissions) == 1
    with pytest.raises(fp.ProgramError, match="Frozen evidence changed"):
        reuse._read(fixture.program, {**ref, "sha256": "f" * 64}, reader)
    # Then a later independent epoch observes changed bytes rather than the prior success.
    fixture.program.path(ref["path"]).write_bytes(b"later changed bytes")
    with pytest.raises(fp.ProgramError, match="Frozen evidence changed"):
        reuse._read(fixture.program, ref, fp.ReadBudget())


def test_checked_metadata_decode_is_strict_once_and_returns_isolated_documents(
    fixture, monkeypatch
):
    # Given a typed control image, as distinct from opaque retained artifact bytes.
    ref = reference(fixture, "evidence/S01/attempt-1/checked-control.json", b'{"values": [1]}')
    reader = fp.ReadBudget()
    original = fp.json.loads
    decoded = []

    def counted(*args, **kwargs):
        decoded.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(fp.json, "loads", counted)
    # When a consumer mutates its returned document before another consumer reads it.
    first = reuse._json(fixture.program, ref, reader)
    first["values"].append(2)
    assert reuse._json(fixture.program, ref, reader) == {"values": [1]}
    assert len(decoded) == 1
    # Then strict control parsing and per-consumer bounds still reject malformed images.
    bad = reference(fixture, "evidence/S01/attempt-1/duplicate-control.json", b'{"x":1,"x":2}')
    with pytest.raises(fp.ProgramError, match="Duplicate JSON key"):
        reuse._json(fixture.program, bad, reader)
    with pytest.raises(fp.ProgramError, match="bounded read"):
        reader.capture(fixture.program.directory, ref["path"], 1)


def test_unrelated_recorded_actor_observation_does_not_invalidate_ready_preparation(gate):
    # Given ready completion and the same actual authority before/after an unrelated actor record.
    saved = _completion_preparation(gate)["completion"]
    before = reuse.validate_selection(gate.program, gate.program.load(), saved)["summary"]
    actor = {
        "id": "unrelated-observer",
        "role": "worker",
        "parent_id": "director",
        "model_id": None,
        "reasoning_effort": None,
        "provenance": "unknown",
        "observed_status": "idle",
        "observed_at": "2026-10-15T00:00:00+00:00",
        "decision": "new",
        "reason": "Actual unrelated isolated staffing observation",
    }
    name = gate.fixture.write("evidence/_coordination/attempt-1/unrelated-observer.json", actor)
    assert gate.fixture.run("actor-record", "--input", name) == 0
    current = gate.program.load()
    assert (
        fp.authoring.EvidenceReceiptAdapter().assess(
            gate.program, current, "completion", saved["receipt"], owner="director"
        )["status"]
        == "ready"
    )
    assert reuse.validate_selection(gate.program, current, saved)["summary"] == before


def test_saved_observation_rechecks_actual_retired_verifier(fixture):
    # Given a registered active verifier and an actually ready ordinary retained-outcome draft.
    actor = {
        "id": "qa",
        "role": "worker",
        "parent_id": None,
        "model_id": None,
        "reasoning_effort": None,
        "provenance": "unknown",
        "observed_status": "unknown",
        "decision": "new",
        "reason": "Actual isolated verifier registration",
    }
    name = fixture.write("evidence/_coordination/attempt-1/qa-active.json", actor)
    assert fixture.run("actor-record", "--input", name) == 0
    record(fixture, request(fixture))
    original = reference(
        fixture,
        "design/retained-verifier-outcome.json",
        fp.canonical({"argv": ["tool"], "cwd": "/recorded", "exit_code": 0}),
    )
    materialized = copied(fixture, [original])
    inputs = {
        "task_id": "S01",
        "owner": "qa",
        "outcome_ref": original,
        "materialization": materialized,
        "request_binding": {
            "observation_id": "retained-before-retirement",
            "actor_id": "qa",
            "request_id": "request-1",
            "request_version": 1,
            "reason": "Retained actual fixture outcome",
            "execution_kind": "test",
        },
    }
    normal = fp.authoring.EvidenceReceiptAdapter()
    saved = reuse.prepare_receipt(
        fixture.program, fixture.program.load(), "observation", inputs, adapter=normal
    )
    assert saved["status"] == "ready" and saved["receipt"]["result"] == "unknown"
    # When normal staffing retirement removes participant authority without changing source/request.
    actor.update(decision="retire", reason="Actual isolated verifier retirement")
    name = fixture.write("evidence/_coordination/attempt-1/qa-retired.json", actor)
    assert fixture.run("actor-record", "--input", name) == 0
    current = fixture.program.load()
    reassessed = reuse.prepare_receipt(
        fixture.program, current, "observation", inputs, adapter=normal
    )
    rejected = reassessed["assessment"]
    assert rejected["status"] == "incomplete"
    assert any("retired" in reason.lower() for reason in rejected["reasons"]), rejected
    assert reuse.validate_selection(fixture.program, current, saved)["summary"]["status"] == "stale"


def test_saved_leader_review_rechecks_actual_director_override(gate):
    # Given a real submitted task and an explicitly labeled leader review by its current director.
    gate.ready()
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    state = gate.program.load()
    inputs = {"task_id": "S01", "owner": "director", "scope": ["src/S01.py"]}
    initial = reuse.prepare_receipt(gate.program, state, "review", inputs)
    note = reference(gate.fixture, "design/leader-inspection.txt")
    actual = decision(
        gate.fixture,
        initial["binding"],
        {
            "verdict": "accepted",
            "reviewer_role": "leader",
            "reason": "Actual explicit isolated director override review",
            "acceptance_complete": True,
            "unresolved_findings": [],
            "evidence": [note["path"]],
            "next_action": "Inspect delivery",
        },
        actor="director",
    )
    inputs.update(decision_refs=[actual], materialization=copied(gate.fixture, [note, actual]))
    normal = fp.authoring.EvidenceReceiptAdapter()
    saved = reuse.prepare_receipt(gate.program, state, "review", inputs, adapter=normal)
    assert saved["status"] == "ready"
    # When the director role transfers normally, the old leader no longer has override authority.
    _director_transfer(gate)
    current = gate.program.load()
    rejected = normal.assess(gate.program, current, "review", saved["receipt"], owner="director")
    assert rejected["status"] == "incomplete"
    assert any("current accepted director" in reason for reason in rejected["reasons"])
    assert reuse.validate_selection(gate.program, current, saved)["summary"]["status"] == "stale"
