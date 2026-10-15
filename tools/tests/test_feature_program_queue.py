"""Behavioral queue and existing-command assignment scenarios on isolated programs."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests.test_feature_program import HEAD, fp, make_small_program  # noqa: E402
from tools.tests.test_feature_program_verification import request, record  # noqa: E402
import feature_program_queue as queue  # noqa: E402


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "git", lambda _repo, args: "" if args[0] == "status" else HEAD)
    return make_small_program(tmp_path / "repo", "queue-fixture", ("S01", "S02"))


def lane(state, **changes):
    return {
        "request_id": "request-1",
        "request_version": 1,
        "lane": "tests",
        "check_ids": ["acceptance", "scoped_tests", "format_lint"],
        "verifier_id": "qa",
        "resolver_id": None,
        "next_trigger": "Actual execution",
        "phase": "requested",
        "result": "pending",
        "applicability": "current",
        "reasons": [],
        "input_complete": True,
        "context": {"attempt": 1},
        "request_ref": {"path": "evidence/request.json", "sha256": "a" * 64},
        "observation_ref": None,
        "request_reference": {
            "path": "backlog.json",
            "pointer": "/verification/records/0",
            "revision": fp.phases.revision(state),
            "task_id": "S01",
        },
        **changes,
    }


def projection(state, *lanes, facts=None):
    return {
        "tasks": {
            "S01": {
                "lanes": {
                    "total": len(lanes),
                    "shown": len(lanes),
                    "omitted": 0,
                    "items": list(lanes),
                }
            }
        },
        "task_facts": facts or {},
    }


def s01_rows(result):
    return [row for row in result["rows"]["items"] if row["task_id"] == "S01"]


def test_queue_includes_lane_legacy_review_and_check_gaps(fixture):
    # Given a submitted legacy task and an explicitly requested subset of its checks.
    assert fixture.claim() == 0
    state = fixture.program.load()
    state["tasks"][0]["status"] = "review"
    # When all obligations are projected without changing canonical state.
    result = queue.project(state, projection(state, lane(state, check_ids=["scoped_tests"])))
    # Then lane, actual task review, and both uncovered required checks are visible.
    rows = s01_rows(result)
    assert [row["kind"] for row in rows].count("required_check_gap") == 2
    assert {row["kind"] for row in rows} == {
        "verification_lane",
        "task_review",
        "required_check_gap",
    }
    assert all(row["request_id"] is None for row in rows if row["kind"] != "verification_lane")


@pytest.mark.parametrize(
    ("changes", "action", "readiness", "actor"),
    [
        ({}, "run", "ready", "qa"),
        ({"input_complete": False}, "run", "not_assessed", "qa"),
        ({"phase": "running", "result": "unknown"}, "wait", "blocked", "qa"),
        ({"phase": "blocked", "resolver_id": "worker"}, "resolve_blocker", "blocked", "worker"),
        ({"phase": "blocked"}, "resolve_blocker", "not_assessed", None),
        ({"phase": "finished", "result": "failed"}, "recheck", "not_assessed", "worker"),
        ({"phase": "finished", "result": "passed"}, "none", "terminal", None),
        ({"phase": "canceled"}, "assign", "not_assessed", "worker"),
        ({"applicability": "stale"}, "recheck", "not_assessed", "worker"),
        ({"applicability": "unavailable"}, "recheck", "not_assessed", "worker"),
    ],
)
def test_lane_readiness_preserves_phase_and_actual_next_responsibility(
    fixture, changes, action, readiness, actor
):
    # Given current ownership and explicit authoritative lane facts.
    assert fixture.claim() == 0
    state = fixture.program.load()
    # When recorded readiness and action are projected.
    row = s01_rows(queue.project(state, projection(state, lane(state, **changes))))[0]
    # Then responsibility is specific while runtime capacity remains unknown.
    assert (row["action"], row["readiness"]["status"], row["next_actor_id"]) == (
        action,
        readiness,
        actor,
    )
    assert row["runtime"] == {"contact": "unknown", "liveness": "unknown", "capacity": "unknown"}


def test_retired_verifier_requires_reassignment_without_inventing_idle_capacity(fixture):
    # Given an assigned participant retired after the original request.
    state = fixture.program.load()
    state["actors"] = [{"id": "qa", "staffing_status": "retired", "observed_status": "idle"}]
    # When the queue inspects the saved assignment.
    row = s01_rows(queue.project(state, projection(state, lane(state))))[0]
    # Then the recorded assignment needs correction, not automatic native reuse.
    assert row["action"] == "assign" and row["readiness"]["status"] == "not_assessed"
    assert row["runtime"]["capacity"] == "unknown"


def test_current_ordinary_passes_suppress_legacy_check_gaps(fixture):
    # Given current ordinary check evidence without optional verification metadata.
    state = fixture.program.load()
    facts = {
        "S01": {
            "submission": {"status": "current"},
            "checks": [
                {"id": name, "status": "passed"} for name in state["tasks"][0]["required_checks"]
            ],
        }
    }
    # When legacy obligations are assessed from actual host-supplied receipt facts.
    result = queue.project(state, projection(state, facts=facts))
    # Then no new verification lane requirement is imposed on the legacy task.
    assert s01_rows(result) == []


def test_stale_ordinary_checks_do_not_hide_required_obligation_gaps(fixture):
    # Given stale receipt facts claiming prior success.
    state = fixture.program.load()
    facts = {
        "S01": {
            "submission": {"status": "stale"},
            "checks": [
                {"id": name, "status": "passed"} for name in state["tasks"][0]["required_checks"]
            ],
        }
    }
    # When current obligations are projected.
    result = queue.project(state, projection(state, facts=facts))
    # Then prior passing claims do not cover the current check gaps.
    assert len(s01_rows(result)) == 3


def test_complete_counts_and_stable_identity_precede_one_bound(fixture):
    # Given seventy independently named lane requests for the same task.
    state = fixture.program.load()
    lanes = [
        lane(state, request_id=f"request-{index:03}", lane=f"lane-{index:03}")
        for index in range(70)
    ]
    full = projection(state, *lanes)
    # When the shared queue is bounded once.
    result = queue.project(state, full)
    # Then complete counts survive truncation and exact identities remain unique.
    assert result["rows"]["total"] == 73 and result["rows"]["shown"] == 50
    assert result["rows"]["omitted"] == 23
    assert result["counts"]["kind"] == {"verification_lane": 70, "required_check_gap": 3}
    assert len({row["row_id"] for row in result["rows"]["items"]}) == 50
    reverse = queue.project(state, projection(state, *reversed(lanes)))
    assert [row["row_id"] for row in result["rows"]["items"]] == [
        row["row_id"] for row in reverse["rows"]["items"]
    ]


def test_bounded_lane_input_is_rejected_before_false_complete_counts(fixture):
    # Given an already truncated producer slice.
    state = fixture.program.load()
    full = projection(state, lane(state))
    full["tasks"]["S01"]["lanes"].update({"total": 80, "omitted": 79})
    # When the queue requires full assessed input.
    with pytest.raises(fp.ProgramError):
        queue.project(state, full)
    # Then callers cannot treat the displayed one-row slice as the complete obligation set.


def use_event(identifier, kind, request_id=None, resource=None):
    value = {
        "id": identifier,
        "kind": kind,
        "task_id": "S01",
        "lane": "tests",
        "verifier_id": "qa",
        "resource": resource,
    }
    return {
        "input": value,
        "request_id": request_id or identifier,
        "request_version": 1,
        "receipt": {"path": f"evidence/{identifier}.json", "sha256": "a" * 64},
    }


def test_superseded_running_observation_remains_resource_uncertainty(fixture):
    # Given request A still recorded running after request B supersedes it.
    state = fixture.program.load()
    resource = {"name": "browser", "boundary": "session-1"}
    state["verification"] = {
        "records": [
            use_event("request-a", "request", resource=resource),
            use_event("start-a", "started", "request-a"),
            use_event("request-b", "request", resource=resource),
        ]
    }
    full = projection(state, lane(state, request_id="request-b", resource=resource))
    # When latest obligations and all recorded uses are projected together.
    result = queue.project(state, full)
    # Then old running use remains uncertain and the new dispatch needs actual resource inspection.
    assert result["recorded_workload"]["counts"] == {
        "superseded_running": 1,
        "resource_overlap": 1,
    }
    assert s01_rows(result)[0]["action"] == "inspect"
    assert result["recorded_workload"]["items"][0]["actual_occupancy"] == "unknown"


def test_actual_terminal_observation_clears_old_recorded_use(fixture):
    # Given old work has an actual cancellation event before replacement work is assessed.
    state = fixture.program.load()
    resource = {"name": "browser", "boundary": "session-1"}
    state["verification"] = {
        "records": [
            use_event("request-a", "request", resource=resource),
            use_event("start-a", "started", "request-a"),
            use_event("request-b", "request", resource=resource),
            use_event("cancel-a", "canceled", "request-a"),
        ]
    }
    # When the queue reads preserved history.
    result = queue.project(state, projection(state, lane(state, resource=resource)))
    # Then cancellation clears uncertain use without deleting any event.
    assert result["recorded_workload"]["total"] == 0
    assert len(state["verification"]["records"]) == 4


def test_recorded_actor_overlap_does_not_assert_capacity_limit(fixture):
    # Given two actual running observations assigned to one participant.
    state = fixture.program.load()
    second = use_event("request-b", "request")
    second["input"]["lane"] = "review"
    state["verification"] = {
        "records": [
            use_event("request-a", "request"),
            use_event("start-a", "started", "request-a"),
            second,
            use_event("start-b", "started", "request-b"),
        ]
    }
    # When recorded workload is grouped by exact participant.
    result = queue.project(state, projection(state))
    # Then overlapping responsibility is exposed without claiming unavailability or a staff cap.
    overlap = result["recorded_workload"]["items"][0]
    assert overlap["kind"] == "actor_overlap" and len(overlap["requests"]) == 2
    assert overlap["capacity"] == "unknown"


def test_projection_is_detached_and_does_not_invent_wait_intervals(fixture):
    # Given a request without observed wait endpoints and saved idle actor status.
    state = fixture.program.load()
    state["actors"] = [{"id": "qa", "observed_status": "idle", "observed_at": "2020-01-01"}]
    full = projection(state, lane(state, waiting={"status": "unknown", "seconds": None}))
    before = deepcopy((state, full))
    # When read-only queue projection runs.
    result = queue.project(state, full)
    # Then source facts are unchanged and no publication clock becomes an actual wait.
    assert (state, full) == before
    assert s01_rows(result)[0]["waiting"]["seconds"] is None


def assignment(fixture, **changes):
    return request(fixture, **changes)


def test_explicit_assignment_draft_is_consumed_by_existing_canonical_request(fixture):
    # Given a real isolated program and an explicitly chosen verifier.
    assert fixture.claim() == 0
    state = fixture.program.load()
    chosen = assignment(fixture)
    chosen.pop("operation")
    chosen.pop("kind")
    before = fixture.program.path("backlog.json").read_bytes()
    # When preparation resolves the current packet and produces an inert canonical request.
    result = queue.prepare_assignment(fixture.program, state, chosen)
    # Then no mutation occurred, and the ordinary verification transition consumes the draft.
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert result["receipt"]["verifier_id"] == "qa"
    assert result["status"] == "not_assessed"
    event = record(fixture, result["receipt"])
    assert event["input"]["verifier_id"] == "qa" and event["request_version"] == 1


def test_reassignment_draft_names_exact_supersedes_and_preserves_original_request(fixture):
    # Given an existing immutable request assigned to the first verifier.
    assert fixture.claim() == 0
    first = record(fixture, request(fixture))
    chosen = assignment(fixture, id="request-2", verifier_id="qa-next")
    chosen.pop("operation")
    chosen.pop("kind")
    state = fixture.program.load()
    # When explicit reassignment prepares only a new request draft.
    result = queue.prepare_assignment(fixture.program, state, chosen)
    # Then exact supersession and all original history are retained.
    assert result["receipt"]["supersedes"] == first["request_id"]
    assert len(state["verification"]["records"]) == 1
    assert state["verification"]["records"][0]["input"]["verifier_id"] == "qa"


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_id": "intruder"},
        {"expected_revision": "stale"},
        {"supersedes": "wrong"},
        {"check_ids": ["unowned-check"]},
        {"extra_control": "start-agent"},
    ],
)
def test_assignment_preparation_preserves_existing_authority_and_obligation_guards(
    fixture, changes
):
    # Given an explicit draft whose actor/revision/lineage/scope is invalid.
    assert fixture.claim() == 0
    chosen = assignment(fixture, **changes)
    chosen.pop("operation")
    chosen.pop("kind")
    # When preparation inspects the actual current program.
    with pytest.raises((fp.ProgramError, fp.coordination.GovernanceError)):
        queue.prepare_assignment(fixture.program, fixture.program.load(), chosen)
    # Then no unauthorized control or replacement request is published.


def test_allocation_packet_retains_authored_peer_words_without_generic_ack(fixture):
    # Given an authored note whose wording claims acknowledgement.
    state = fixture.program.load()
    state["tasks"][0]["notes"] = [
        {
            "actor": "worker",
            "reason": "accepted and acknowledged",
            "next_action": "continue",
            "timestamp": "2026-10-14",
        }
    ]
    # When the allocation packet assembles recorded context.
    packet = queue.allocation_packet(state, "S01")
    # Then exact notes remain visible without inferring peer assent or live contacts.
    assert packet["coordination_notes"] == state["tasks"][0]["notes"]
    assert packet["peer_agreement"]["status"] == "unknown"
    assert packet["native_contact"] == packet["runtime_capacity"] == "unknown"


def test_claimed_pass_with_incomplete_observation_inputs_is_not_terminal(fixture):
    # Given a claimed passing outcome whose authoritative observation assessment is incomplete.
    state = fixture.program.load()
    claimed = lane(
        state,
        phase="finished",
        result="passed",
        observation_problems=["Actual input endpoints are missing"],
    )
    # When recorded queue readiness is assessed.
    row = s01_rows(queue.project(state, projection(state, claimed)))[0]
    # Then the passing claim is retained without treating it as complete review evidence.
    assert row["result"] == "passed"
    assert row["action"] == "inspect" and row["readiness"]["status"] == "not_assessed"


def test_assignment_frozen_packet_rejects_actual_source_drift(fixture):
    # Given an explicit assignment draft whose current source packet is frozen separately.
    assert fixture.claim() == 0
    chosen = assignment(fixture)
    chosen.pop("operation")
    chosen.pop("kind")
    result = queue.prepare_assignment(fixture.program, fixture.program.load(), chosen)
    packet_path = fixture.write(
        "evidence/S01/attempt-1/assignment-context.json", result["related_inputs"]["context_packet"]
    )
    result["receipt"]["context_path"] = packet_path
    fixture.program.repo.joinpath("src/S01.py").write_text("changed = True\n", encoding="utf-8")
    # When the ordinary canonical request is published against the changed source.
    with pytest.raises(fp.ProgramError):
        record(fixture, result["receipt"])
    # Then source-bound preparation cannot silently adopt a different candidate.


def test_assignment_draft_keeps_missing_verifier_and_binding_explicit(fixture):
    # Given a caller who has not chosen the actual verifier.
    assert fixture.claim() == 0
    chosen = assignment(fixture, verifier_id=None)
    chosen.pop("operation")
    chosen.pop("kind")
    # When inert assignment inputs are prepared.
    result = queue.prepare_assignment(fixture.program, fixture.program.load(), chosen)
    # Then neither saved ownership nor runtime observation supplies the missing verifier.
    assert "verifier_id" in result["missing_fields"] and "context_path" in result["missing_fields"]
    assert result["receipt"]["verifier_id"] is None


def test_allocation_packet_uses_canonical_dependency_and_exact_write_conflicts(fixture):
    # Given one claimed writer and another task whose planned path overlaps that writer.
    assert fixture.claim() == 0
    state = fixture.program.load()
    state["tasks"][1]["candidate_paths"] = ["src/S01.py"]
    state["tasks"][1]["depends_on"] = ["S01"]
    # When recorded allocation factors are assembled for the waiting task.
    packet = queue.allocation_packet(state, "S02")
    # Then the exact dependency and collision are retained without granting a claim.
    assert packet["readiness"]["unmet_dependencies"] == ["S01"]
    assert [row["id"] for row in packet["readiness"]["scope_conflicts"]] == ["S01"]
    assert packet["readiness"]["claim"] == "not_assessed"


def test_captured_allocation_packets_reuse_one_index_and_match_standalone(fixture, monkeypatch):
    # Given two tasks with complete recorded allocation context.
    state = fixture.program.load()
    state["verification"] = {
        "records": [use_event("request-a", "request"), use_event("start-a", "started", "request-a")]
    }
    state["verification"]["records"][0]["input"].update(
        {
            "check_ids": ["acceptance"],
            "input_scope": ["src/S01.py"],
            "evidence_destination": "evidence/S01/attempt-1",
            "next_trigger": "Actual observation",
        }
    )
    expected = {task: queue.allocation_packet(state, task) for task in ("S01", "S02")}
    original = fp._TaskIndex.capture
    captures = []

    def capture(cls, tasks):
        captures.append(len(tasks))
        return original(tasks)

    monkeypatch.setattr(fp._TaskIndex, "capture", classmethod(capture))
    # When the host constructs multiple packets from its one immutable state image.
    captured = queue._allocation_context(state)
    actual = {
        task: queue._allocation_packet_from_context(state, task, None, captured)
        for task in ("S01", "S02")
    }
    # Then dependency/ownership/history/uncertainty agree, and the shared task index is built once.
    assert actual == expected
    assert captures == [2]
    assert all(packet["runtime_capacity"] == "unknown" for packet in actual.values())


def test_allocation_capture_cannot_cross_state_images_and_next_call_is_fresh(fixture):
    # Given an allocation context captured before participant supervision changes.
    state = fixture.program.load()
    captured = queue._allocation_context(state)
    changed = deepcopy(state)
    changed["tasks"][0]["owner"] = "new-worker"
    changed["actors"] = [{"id": "new-worker", "parent_id": "new-supervisor"}]
    # When a foreign image attempts to reuse it, or a new standalone packet is requested.
    with pytest.raises(fp.ProgramError):
        queue._allocation_packet_from_context(changed, "S01", None, captured)
    fresh = queue.allocation_packet(changed, "S01")
    # Then the old capture is rejected and fresh recorded responsibility remains visible.
    assert fresh["supervisor_id"] == "new-supervisor"
    assert fresh["runtime_capacity"] == "unknown"


def test_running_history_references_keep_original_positions_amid_unrelated_events(fixture):
    # Given superseded and current running requests interleaved with another lane.
    state = fixture.program.load()
    other = use_event("request-other", "request")
    other["input"]["lane"] = "other"
    state["verification"] = {
        "records": [
            use_event("request-a", "request"),
            use_event("start-a", "started", "request-a"),
            other,
            use_event("request-b", "request"),
            use_event("start-b", "started", "request-b"),
        ]
    }
    # When all retained running observations are projected, before any display reduction.
    result = queue.project(state, projection(state))
    overlap = next(
        item for item in result["recorded_workload"]["items"] if item["kind"] == "actor_overlap"
    )
    uses = {item["request_id"]: item for item in overlap["requests"]}
    # Then exact original pointers and superseded uncertainty survive the single history pass.
    assert uses["request-a"]["reference"]["pointer"] == "/verification/records/1"
    assert uses["request-b"]["reference"]["pointer"] == "/verification/records/4"
    assert uses["request-a"]["superseded"] is True
    assert uses["request-b"]["superseded"] is False
    assert all(item["actual_occupancy"] == "unknown" for item in uses.values())
