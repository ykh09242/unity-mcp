"""Synthetic governance scenarios; no real program records or agent identities."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import pytest

from tools import feature_program_coordination as governance


def actor(identifier: str, role: str = "worker", parent: str | None = None) -> governance.Record:
    return {
        "id": identifier,
        "role": role,
        "parent_id": parent,
        "model_id": None,
        "reasoning_effort": None,
        "provenance": "unknown",
        "observed_status": "unknown",
        "observed_at": None,
        "staffing_status": "active",
        "history": [],
    }


@dataclass(slots=True)
class SyntheticProgram:
    """Mutable isolated adapter implements only the existing Program safety contract."""

    directory: Path
    repo: Path
    head: str = "a" * 40

    def path(self, name: str) -> Path:
        path = self.directory / name
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise governance.GovernanceError("Path outside program")
        return path

    def current_head(self) -> str:
        return self.head

    def source_digest(self, scope: list[str]) -> str:
        snapshot = {
            name: hashlib.sha256((self.repo / name).read_bytes()).hexdigest() for name in scope
        }
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    def evidence(self, task_id: str, attempt: int, names: list[str]) -> list[governance.Json]:
        refs: list[governance.Json] = []
        for name in names:
            if not name.startswith(f"evidence/{task_id}/attempt-{attempt}/"):
                raise governance.GovernanceError("Evidence outside attempt")
            raw = self.path(name).read_bytes()
            if not raw:
                raise governance.GovernanceError("Empty evidence")
            refs.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        return refs

    def verify_refs(self, refs: list[governance.Json]) -> None:
        for ref in refs:
            assert isinstance(ref, dict)
            name = ref["path"]
            assert isinstance(name, str)
            if hashlib.sha256(self.path(name).read_bytes()).hexdigest() != ref["sha256"]:
                raise governance.GovernanceError("Frozen evidence changed")

    def event(self, state: governance.Record, action: str, detail: governance.Record) -> None:
        events = state["events"]
        assert isinstance(events, list)
        events.append({"action": action, **detail})

    def current_delivery(self, task: governance.Record) -> governance.Record:
        delivery = task.get("delivery", {"status": "unrecorded"})
        assert isinstance(delivery, dict)
        return delivery

    def verify_terminal(self, task: governance.Record, attempt: governance.Record) -> None:
        if attempt.get("status") != "done" or not attempt.get("review"):
            raise governance.GovernanceError("Missing reviewed terminal attempt")

    def pending_questions(
        self, state: governance.Record, objective_id: str, task_ids: list[str]
    ) -> list[governance.Record]:
        questions = state.get("questions", {"items": []})
        assert isinstance(questions, dict)
        items = questions.get("items", [])
        assert isinstance(items, list)
        result: list[governance.Record] = []
        for item in items:
            assert isinstance(item, dict) and isinstance(item["issued"], dict)
            issued = item["issued"]
            affected = issued.get("affected_task_ids", [])
            assert isinstance(affected, list)
            relevant = issued.get("objective_id") == objective_id or (
                issued.get("objective_id") is None and bool(set(affected) & set(task_ids))
            )
            if item.get("status") != "resolved" and relevant:
                result.append(item)
        return result

    def receipt(self, name: str, value: governance.Record) -> str:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return name

    def binding(self) -> governance.Record:
        return {
            "source_head": self.head,
            "scope": ["src/tool.py"],
            "source_digest": self.source_digest(["src/tool.py"]),
        }


@pytest.fixture
def program(tmp_path: Path) -> SyntheticProgram:
    repo = tmp_path / "repository"
    (repo / "src").mkdir(parents=True)
    (repo / "src/tool.py").write_text("VALUE = 1\n", encoding="utf-8")
    directory = tmp_path / "program"
    directory.mkdir()
    return SyntheticProgram(directory, repo)


@pytest.fixture
def state() -> governance.Record:
    return {
        "workstreams": [{"id": "engineering", "title": "Engineering"}],
        "tasks": [
            {
                "id": "TASK-A",
                "workstream": "engineering",
                "status": "active",
                "owner": "worker-old",
                "attempts": [
                    {
                        "number": 1,
                        "owner": "worker-old",
                        "status": "active",
                        "scope": ["src/tool.py"],
                        "evidence": [],
                    }
                ],
            }
        ],
        "events": [],
    }


def run(
    program: SyntheticProgram,
    state: governance.Record,
    command: str,
    receipt: governance.Record,
    name: str = "input.json",
) -> bool:
    return governance.apply_command(
        program, state, argparse.Namespace(command=command, input=program.receipt(name, receipt))
    )


def roster(state: governance.Record) -> None:
    state["actors"] = [
        actor("director-old", "director"),
        actor("director-next", "director"),
        actor("worker-old", parent="director-old"),
        actor("worker-next", parent="director-old"),
    ]


def appoint(program: SyntheticProgram, state: governance.Record) -> None:
    roster(state)
    run(
        program,
        state,
        "director-appoint",
        {
            **program.binding(),
            "actor_id": "director-old",
            "acknowledged": True,
            "authority": {
                "authorized_by": "user",
                "basis": "Explicit recorded permission",
                "merge_target": "beta",
            },
            "evidence": [],
        },
        "evidence/_coordination/attempt-1/appoint.json",
    )


def outgoing(program: SyntheticProgram, role: str = "worker") -> governance.Record:
    return {
        **program.binding(),
        "id": "handoff-one",
        "role": role,
        "from_actor": "worker-old" if role == "worker" else "director-old",
        "to_actor": "worker-next" if role == "worker" else "director-next",
        "task_id": "TASK-A" if role == "worker" else None,
        "goal": "Finish scoped work",
        "constraints": ["Preserve other changes"],
        "changed_paths": ["src/tool.py"],
        "decisions": ["Keep the existing implementation"],
        "evidence": [],
        "unresolved_findings": [],
        "next_action": "Inspect current source",
        "authority": {
            "authorized_by": "user",
            "basis": "Explicit succession decision",
            "merge_target": "beta",
        },
    }


def ack(program: SyntheticProgram, actor_id: str = "worker-next") -> governance.Record:
    return {
        **program.binding(),
        "transfer_id": "handoff-one",
        "actor_id": actor_id,
        "summary": "Inspected current scope and preserved outgoing findings",
        "evidence": [],
    }


def release(state: governance.Record) -> None:
    tasks = state["tasks"]
    assert isinstance(tasks, list) and isinstance(tasks[0], dict)
    task = tasks[0]
    attempts = task["attempts"]
    assert isinstance(attempts, list) and isinstance(attempts[0], dict)
    task.update({"status": "planned", "owner": None})
    attempts[0]["status"] = "released"


def test_legacy_snapshot_preserves_state_and_unknown_assignment(state: governance.Record) -> None:
    # Given a legacy registry with an active attempt and no governance.
    before = deepcopy(state)
    # When a read-only snapshot is requested.
    metadata = governance.snapshot_metadata(state)
    # Then source state stays byte-equivalent and ownership never implies liveness.
    assert state == before
    assert metadata["categories"][0]["task_ids"] == ["TASK-A"]
    assert metadata["actors"][0]["observed_status"] == "unknown"
    assert metadata["actors"][0]["model_id"] is None
    metadata["categories"][0]["task_ids"].clear()
    assert state == before


@pytest.mark.parametrize(
    "categories",
    [
        [{"id": "sub", "title": "Sub", "parent_id": "missing", "task_ids": []}],
        [
            {"id": "a", "title": "A", "parent_id": "b", "task_ids": []},
            {"id": "b", "title": "B", "parent_id": "a", "task_ids": []},
        ],
        [{"id": "sub", "title": "Sub", "parent_id": "engineering", "task_ids": ["missing"]}],
        [
            {
                "id": "sub",
                "title": "Sub",
                "parent_id": "engineering",
                "task_ids": ["TASK-A", "TASK-A"],
            }
        ],
        [
            {"id": "a", "title": "A", "parent_id": "engineering", "task_ids": ["TASK-A"]},
            {"id": "b", "title": "B", "parent_id": "engineering", "task_ids": ["TASK-A"]},
        ],
    ],
)
def test_category_errors_reject_without_losing_state(
    program: SyntheticProgram, state: governance.Record, categories: list[governance.Json]
) -> None:
    # Given an existing task history.
    before = deepcopy(state)
    # When an invalid hierarchy is proposed.
    with pytest.raises(governance.GovernanceError):
        run(program, state, "category-set", {"categories": categories, "reason": "Organize work"})
    # Then the existing state is preserved.
    assert state == before


def test_nested_category_keeps_unique_leaves(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given a task in an existing workstream.
    # When it is placed in a nested category.
    run(
        program,
        state,
        "category-set",
        {
            "categories": [
                {"id": "sub", "title": "Sub", "parent_id": "engineering", "task_ids": ["TASK-A"]}
            ],
            "reason": "Separate scoped work",
        },
    )
    # Then its identity and history remain and membership is unique.
    metadata = governance.snapshot_metadata(state)
    assert sum(len(item["task_ids"]) for item in metadata["categories"]) == 1
    assert metadata["categories"][0]["parent_id"] == "engineering"


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", []),
        ("provenance", []),
        ("observed_status", []),
        ("model_id", "example-model"),
        ("observed_status", "running"),
        ("observed_at", "no-timezone"),
    ],
)
def test_malformed_actor_metadata_rejected(
    state: governance.Record, field: str, value: governance.Json
) -> None:
    # Given a malformed actor boundary record.
    item = actor("synthetic-worker")
    item[field] = value
    state["actors"] = [item]
    # When governance is validated.
    with pytest.raises(governance.GovernanceError):
        governance.validate_governance(state)
    # Then the parser rejected it as a governance error rather than claiming verified metadata.


def test_verified_actor_metadata_and_reuse_preserve_history(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given a recorded exact launch with no claim of authenticated identity.
    receipt = {
        **actor("synthetic-worker"),
        "model_id": "example-model-v1",
        "reasoning_effort": "high",
        "provenance": "verified_launch",
        "decision": "new",
        "reason": "Required scoped implementation",
        "observed_status": "running",
        "observed_at": "2026-01-01T00:00:00Z",
    }
    run(program, state, "actor-record", receipt)
    # When the same actor is reused with a newer observation.
    run(
        program,
        state,
        "actor-record",
        {
            **receipt,
            "decision": "reuse",
            "reason": "Continue related work",
            "observed_status": "idle",
            "observed_at": "2026-01-01T01:00:00Z",
        },
    )
    # Then exact settings and both staffing decisions survive.
    result = governance.snapshot_metadata(state)["actors"][0]
    assert result["model_id"] == "example-model-v1"
    assert [entry["decision"] for entry in result["history"]] == ["new", "reuse"]


@pytest.mark.parametrize(
    "actors",
    [
        [actor("a", parent="missing")],
        [actor("a", "sub_director", "b"), actor("b", "sub_director", "a")],
        [actor("a"), actor("b", parent="a")],
        [actor("a", "director", "b"), actor("b", "sub_director")],
    ],
)
def test_actor_hierarchy_rejects_invalid_management(
    state: governance.Record, actors: list[governance.Json]
) -> None:
    # Given an invalid manager graph.
    state["actors"] = actors
    # When governance is parsed.
    with pytest.raises(governance.GovernanceError):
        governance.validate_governance(state)
    # Then unknown parents, cycles and role authority are rejected.


def test_multiple_sub_directors_are_supported(state: governance.Record) -> None:
    # Given several recorded managers and one worker.
    state["actors"] = [
        actor("chief", "director"),
        actor("manager-a", "sub_director", "chief"),
        actor("manager-b", "sub_director", "chief"),
        actor("worker", parent="manager-b"),
    ]
    # When their hierarchy is validated.
    governance.validate_governance(state)
    # Then no fixed roster limit or single-sub-director assumption is applied.
    assert len(governance.snapshot_metadata(state)["actors"]) == 5


def test_director_cannot_be_reappointed_or_retired(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an explicitly appointed final director.
    appoint(program, state)
    before = deepcopy(state)
    # When a second direct appointment bypasses succession.
    with pytest.raises(governance.GovernanceError, match="already appointed"):
        run(
            program,
            state,
            "director-appoint",
            {**program.binding(), "actor_id": "director-next", "acknowledged": True},
        )
    # Then current authority is unchanged.
    assert state == before


def test_director_transfer_requires_ack_and_preserves_authority_history(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an outgoing director transfer with actual source binding.
    appoint(program, state)
    run(
        program,
        state,
        "transfer",
        outgoing(program, "director"),
        "evidence/_coordination/attempt-1/transfer.json",
    )
    assert state["director"]["actor_id"] == "director-old"
    # When the successor acknowledges fresh source.
    run(
        program,
        state,
        "transfer-ack",
        ack(program, "director-next"),
        "evidence/_coordination/attempt-1/ack.json",
    )
    assert state["director"]["actor_id"] == "director-old"
    run(
        program,
        state,
        "transfer-accept",
        {**ack(program, "director-old"), "summary": "Accept verified succession"},
        "evidence/_coordination/attempt-1/accept.json",
    )
    # Then only accepted authority changes and the prior appointment remains frozen.
    assert state["director"]["actor_id"] == "director-next"
    assert state["director"]["history"][0]["actor_id"] == "director-old"
    assert state["handoffs"][0]["acknowledgement"]["actor_id"] == "director-next"
    program.verify_refs(governance.evidence_references(state))


@pytest.mark.parametrize(
    "kind", ["not-released", "stale-head", "stale-source", "wrong-successor", "changed-evidence"]
)
def test_worker_ack_rejects_unsafe_transfer(
    program: SyntheticProgram, state: governance.Record, kind: str
) -> None:
    # Given a recorded outgoing worker claim.
    roster(state)
    name = "evidence/TASK-A/attempt-1/transfer.json"
    run(program, state, "transfer", outgoing(program), name)
    acknowledgement = ack(program)
    if kind != "not-released":
        release(state)
    if kind == "stale-head":
        program.head = "b" * 40
    if kind == "stale-source":
        (program.repo / "src/tool.py").write_text("VALUE = 2\n", encoding="utf-8")
    if kind == "wrong-successor":
        acknowledgement["actor_id"] = "worker-old"
    if kind == "changed-evidence":
        program.path(name).write_text("{}", encoding="utf-8")
    before = deepcopy(state)
    # When unsafe acknowledgement is attempted.
    with pytest.raises(governance.GovernanceError):
        run(program, state, "transfer-ack", acknowledgement, "evidence/TASK-A/attempt-1/ack.json")
    # Then no task assignment or prior evidence history is rewritten.
    assert state == before


def test_worker_transfer_preserves_claim_and_requires_separate_reclaim(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an outgoing transfer that does not release the core claim.
    roster(state)
    original_attempt = deepcopy(state["tasks"][0]["attempts"][0])
    run(program, state, "transfer", outgoing(program), "evidence/TASK-A/attempt-1/transfer.json")
    assert state["tasks"][0]["attempts"][0] == original_attempt
    release(state)
    # When the successor acknowledges following core release.
    run(program, state, "transfer-ack", ack(program), "evidence/TASK-A/attempt-1/ack.json")
    # Then acknowledgement does not claim, change owner, or erase the released attempt.
    assert state["tasks"][0]["owner"] is None
    assert state["tasks"][0]["attempts"][0]["owner"] == "worker-old"
    assert state["tasks"][0]["attempts"][0]["status"] == "released"


def goal(state: governance.Record) -> None:
    state["objective"] = {
        "id": "goal-one",
        "title": "Deliver a scoped tool",
        "criteria": ["Reviewed tool behavior"],
        "task_ids": ["TASK-A"],
        "status": "active",
        "supplied_by": "user",
        "decision_owner": "director",
        "next_action_owner": "director",
        "history": [],
    }
    task = state["tasks"][0]
    task.update(
        {
            "status": "done",
            "owner": None,
            "delivery": {
                "status": "ci_recorded_success",
                "receipt": {"path": "synthetic-delivery", "sha256": "d" * 64},
            },
        }
    )
    task["attempts"][0].update(
        {
            "status": "done",
            "review": {"reviewer": "synthetic-reviewer", "sha256": "b" * 64},
            "submission": {"source_digest": "c" * 64},
        }
    )


def completion(program: SyntheticProgram) -> governance.Record:
    name = "evidence/_coordination/attempt-1/check.txt"
    program.path(name).parent.mkdir(parents=True, exist_ok=True)
    program.path(name).write_text("Synthetic check output", encoding="utf-8")
    review_name = "evidence/_coordination/attempt-1/integrated-review.json"
    program.receipt(
        review_name,
        {
            **program.binding(),
            "objective_id": "goal-one",
            "reviewer": "synthetic-reviewer",
            "reviewer_role": "independent",
            "verdict": "accepted",
            "acceptance_complete": True,
            "criteria": ["Reviewed tool behavior"],
            "unresolved_findings": [],
            "evidence": [name],
        },
    )
    return {
        **program.binding(),
        "actor_id": "director-old",
        "objective_id": "goal-one",
        "integrated_review": review_name,
        "summary": "Criteria and final revision verified",
        "unresolved_findings": [],
        "criteria_results": [
            {"criterion": "Reviewed tool behavior", "status": "passed", "evidence": [name]}
        ],
        "evidence": [name],
        "remote_state_checked": True,
        "required_ci": ["Synthetic CI"],
        "task_acceptance": [
            {
                "task_id": "TASK-A",
                "attempt": 1,
                "source_digest": "c" * 64,
                "review_sha256": "b" * 64,
                "delivery_sha256": "d" * 64,
            }
        ],
        "ci": [
            {
                "name": "Synthetic CI",
                "url": "https://example.invalid/run",
                "commit": program.head,
                "conclusion": "success",
            }
        ],
    }


@pytest.mark.parametrize(
    "kind", ["task", "review", "delivery", "actor", "criterion", "ci", "remote", "findings"]
)
def test_objective_completion_rejects_missing_acceptance(
    program: SyntheticProgram, state: governance.Record, kind: str
) -> None:
    # Given a candidate completion with one missing required condition.
    appoint(program, state)
    goal(state)
    receipt = completion(program)
    task = state["tasks"][0]
    match kind:
        case "task":
            task["status"] = "active"
        case "review":
            task["attempts"][0]["review"] = None
        case "delivery":
            task["delivery"] = {"status": "ci_pending"}
        case "actor":
            receipt["actor_id"] = "director-next"
        case "criterion":
            receipt["criteria_results"] = []
        case "ci":
            receipt["ci"][0]["conclusion"] = "skipped"
        case "remote":
            receipt["remote_state_checked"] = False
        case "findings":
            receipt["unresolved_findings"] = ["Missing acceptance"]
    before = deepcopy(state)
    # When completion is attempted.
    with pytest.raises(governance.GovernanceError):
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then the objective stays active with all old task records preserved.
    assert state == before


@pytest.mark.parametrize(
    "review_state", ["current", "old-membership", "old-membership-aba", "old-source"]
)
def test_amended_membership_requires_current_independent_aggregate_review(
    program: SyntheticProgram, state: governance.Record, review_state: str
) -> None:
    # Given a completed current member and an explicitly excluded unfinished historical task.
    appoint(program, state)
    goal(state)
    state["tasks"][0]["depends_on"] = []
    state["tasks"].append(
        {
            "id": "TASK-X",
            "workstream": "engineering",
            "status": "planned",
            "owner": None,
            "depends_on": [],
            "attempts": [],
        }
    )
    state["objective"]["task_ids"].append("TASK-X")
    governance.amend_membership(
        state,
        {
            "id": "remove-x",
            "actor_id": "director-old",
            "reason": "Independently reviewed exclusion",
            "membership": {
                "before": ["TASK-A", "TASK-X"],
                "after": ["TASK-A"],
                "added": [],
                "removed": ["TASK-X"],
            },
        },
        [],
    )
    if review_state == "old-membership-aba":
        for identifier, before_members, after_members, added, removed in (
            ("readd-x", ["TASK-A"], ["TASK-A", "TASK-X"], ["TASK-X"], []),
            ("remove-x-again", ["TASK-A", "TASK-X"], ["TASK-A"], [], ["TASK-X"]),
        ):
            governance.amend_membership(
                state,
                {
                    "id": identifier,
                    "actor_id": "director-old",
                    "reason": "Reviewed subsequent membership change",
                    "membership": {
                        "before": before_members,
                        "after": after_members,
                        "added": added,
                        "removed": removed,
                    },
                },
                [],
            )
    receipt = completion(program)
    receipt["membership_revision"] = state["objective"]["membership_revision"]
    review_name = receipt["integrated_review"]
    review = json.loads(program.path(review_name).read_text(encoding="utf-8"))
    review["membership_revision"] = 0 if review_state == "old-membership" else 1
    program.receipt(review_name, review)
    if review_state == "old-source":
        (program.repo / "src/tool.py").write_text("VALUE = 2\n", encoding="utf-8")
        receipt.update(program.binding())
    before = deepcopy(state)
    # When the current director presents aggregate acceptance for this membership revision.
    if review_state == "current":
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/current.json",
        )
        # Then only current members must be complete; exclusion preserves unfinished history.
        assert state["objective"]["status"] == "awaiting_next_goal"
        assert state["tasks"] == before["tasks"]
        program.verify_refs(governance.evidence_references(state))
    else:
        with pytest.raises(governance.GovernanceError):
            run(
                program,
                state,
                "objective-complete",
                receipt,
                "evidence/_coordination/attempt-1/stale.json",
            )
        # Then a stale aggregate review cannot change objective acceptance.
        assert state == before


def test_completed_objective_awaits_user_and_preserves_previous_cycle(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given complete reviewed work and recorded final-revision CI.
    appoint(program, state)
    goal(state)
    run(
        program,
        state,
        "objective-complete",
        completion(program),
        "evidence/_coordination/attempt-1/complete.json",
    )
    previous_tasks = deepcopy(state["tasks"])
    assert state["objective"]["status"] == "awaiting_next_goal"
    assert state["objective"]["next_action_owner"] == "user"
    # When an explicit next human objective is supplied.
    run(
        program,
        state,
        "objective-record",
        {
            "id": "goal-two",
            "title": "Next supplied goal",
            "criteria": ["New acceptance"],
            "task_ids": ["TASK-A"],
            "supplied_by": "user",
            "human_authorization": "Explicit next user instruction",
        },
    )
    # Then the earlier completion and attempts remain and the new goal stays active.
    assert state["tasks"] == previous_tasks
    assert state["objective"]["status"] == "active"
    assert state["objective"]["history"][0]["id"] == "goal-one"
    program.verify_refs(governance.evidence_references(state))


def test_new_goal_cannot_replace_unfinished_objective(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an active human objective.
    goal(state)
    before = deepcopy(state)
    # When a second goal bypasses completion.
    with pytest.raises(governance.GovernanceError, match="Finish current"):
        run(
            program,
            state,
            "objective-record",
            {
                "id": "goal-two",
                "title": "Unapproved replacement",
                "criteria": ["Acceptance"],
                "task_ids": ["TASK-A"],
                "supplied_by": "user",
                "human_authorization": "Recorded instruction",
            },
        )


def test_assignment_metadata_does_not_follow_later_actor_launch_settings(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an assignment made with known launch settings.
    receipt = {
        **actor("worker-old"),
        "model_id": "example-model-v1",
        "reasoning_effort": "high",
        "provenance": "verified_launch",
        "decision": "new",
        "reason": "Initial assignment",
    }
    run(program, state, "actor-record", receipt)
    frozen = governance.assignment_metadata(state, "worker-old")
    state["tasks"][0]["attempts"][0]["assignment"] = frozen
    # When the actor is reused with different settings.
    run(
        program,
        state,
        "actor-record",
        {
            **receipt,
            "decision": "reuse",
            "reason": "Related assignment",
            "model_id": "example-model-v2",
            "reasoning_effort": "medium",
        },
    )
    # Then the first attempt retains its launch provenance.
    metadata = governance.snapshot_metadata(state)["actors"][0]["assignments"][0]["metadata"]
    assert metadata["model_id"] == "example-model-v1"
    assert metadata["reasoning_effort"] == "high"


@pytest.mark.parametrize("phase", ["outgoing", "acknowledged"])
def test_pending_transfer_never_authorizes_reclaim(
    program: SyntheticProgram, state: governance.Record, phase: str
) -> None:
    # Given a released worker transfer awaiting a director decision.
    appoint(program, state)
    run(program, state, "transfer", outgoing(program), "evidence/TASK-A/attempt-1/transfer.json")
    release(state)
    if phase == "acknowledged":
        run(program, state, "transfer-ack", ack(program), "evidence/TASK-A/attempt-1/ack.json")
    before = deepcopy(state)
    # When the successor attempts to reclaim.
    with pytest.raises(governance.GovernanceError, match="Accept successor"):
        governance.validate_claim_transfer(state, "TASK-A", "worker-next")
    # Then no second owner is authorized and history remains.
    assert state == before


def test_accepted_worker_transfer_allows_only_declared_successor(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an acknowledged worker transfer.
    appoint(program, state)
    run(program, state, "transfer", outgoing(program), "evidence/TASK-A/attempt-1/transfer.json")
    release(state)
    run(program, state, "transfer-ack", ack(program), "evidence/TASK-A/attempt-1/ack.json")
    # When the final director accepts succession.
    run(
        program,
        state,
        "transfer-accept",
        {**ack(program, "director-old"), "summary": "Accept scoped succession"},
        "evidence/TASK-A/attempt-1/accept.json",
    )
    # Then core still owns revalidation/reclaim and only the chosen successor is authorized.
    governance.validate_claim_transfer(state, "TASK-A", "worker-next")
    with pytest.raises(governance.GovernanceError, match="accepted successor"):
        governance.validate_claim_transfer(state, "TASK-A", "worker-old")
    assert state["tasks"][0]["owner"] is None


def test_objective_completion_ignores_unrelated_planned_tasks(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given one required reviewed task and another unrelated planned task.
    appoint(program, state)
    goal(state)
    state["tasks"].append(
        {
            "id": "TASK-B",
            "workstream": "engineering",
            "status": "planned",
            "owner": None,
            "attempts": [],
        }
    )
    # When the declared goal completes with fresh acceptance.
    run(
        program,
        state,
        "objective-complete",
        completion(program),
        "evidence/_coordination/attempt-1/complete.json",
    )
    # Then only goal membership counts, preserving unrelated work.
    assert state["objective"]["status"] == "awaiting_next_goal"
    assert state["tasks"][1]["status"] == "planned"


@pytest.mark.parametrize("field", ["attempt", "source_digest", "review_sha256", "delivery_sha256"])
def test_objective_acceptance_binds_exact_attempt_and_evidence(
    program: SyntheticProgram, state: governance.Record, field: str
) -> None:
    # Given a final objective verdict referring to stale task evidence.
    appoint(program, state)
    goal(state)
    receipt = completion(program)
    receipt["task_acceptance"][0][field] = 2 if field == "attempt" else "e" * 64
    before = deepcopy(state)
    # When completion is requested.
    with pytest.raises(governance.GovernanceError, match="stale or mismatched"):
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then the goal remains active with all historical records intact.
    assert state == before
    # Then the original objective remains.
    assert state == before


def test_unhandled_command_has_no_side_effects(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given a normal core command and no governance receipt.
    before = deepcopy(state)
    # When the extension is consulted.
    handled = governance.apply_command(program, state, argparse.Namespace(command="status"))
    # Then control returns to core without mutation or file access.
    assert handled is False and state == before


@pytest.mark.parametrize("status", ["open", "answered", "acknowledged"])
@pytest.mark.parametrize("relevance", ["objective", "task"])
def test_unresolved_questions_block_objective_completion(
    program: SyntheticProgram, state: governance.Record, status: str, relevance: str
) -> None:
    # Given an unresolved question relevant by goal or by affected task membership.
    appoint(program, state)
    goal(state)
    state["questions"] = {
        "schema_version": 1,
        "items": [
            {
                "id": "question-one",
                "status": status,
                "issued": {
                    "objective_id": "goal-one" if relevance == "objective" else None,
                    "affected_task_ids": [] if relevance == "objective" else ["TASK-A"],
                },
            }
        ],
    }
    before = deepcopy(state)
    # When the final director attempts objective completion.
    with pytest.raises(governance.GovernanceError, match="unresolved director questions"):
        run(
            program,
            state,
            "objective-complete",
            completion(program),
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then an answer alone cannot silently approve or complete work.
    assert state == before


def test_resolved_and_unrelated_questions_do_not_block_completion(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given a resolved relevant question and an unresolved unrelated question.
    appoint(program, state)
    goal(state)
    state["questions"] = {
        "schema_version": 1,
        "items": [
            {
                "id": "resolved-question",
                "status": "resolved",
                "issued": {"objective_id": "goal-one", "affected_task_ids": ["TASK-A"]},
            },
            {
                "id": "other-question",
                "status": "open",
                "issued": {"objective_id": "other-goal", "affected_task_ids": ["TASK-A"]},
            },
        ],
    }
    # When verified objective acceptance is supplied.
    run(
        program,
        state,
        "objective-complete",
        completion(program),
        "evidence/_coordination/attempt-1/complete.json",
    )
    # Then only the declared objective completes.
    assert state["objective"]["status"] == "awaiting_next_goal"


@pytest.mark.parametrize(
    "condition", ["no-ack", "source-changed", "wrong-director", "retired-target"]
)
def test_transfer_acceptance_rejects_invalid_succession_atomically(
    program: SyntheticProgram, state: governance.Record, condition: str
) -> None:
    # Given a worker transfer and, where appropriate, a fresh successor acknowledgement.
    appoint(program, state)
    run(program, state, "transfer", outgoing(program), "evidence/TASK-A/attempt-1/transfer.json")
    release(state)
    if condition != "no-ack":
        run(program, state, "transfer-ack", ack(program), "evidence/TASK-A/attempt-1/ack.json")
    if condition == "source-changed":
        (program.repo / "src/tool.py").write_text("VALUE = 2\n", encoding="utf-8")
    receipt = ack(program, "worker-old" if condition == "wrong-director" else "director-old")
    if condition == "retired-target":
        state["actors"][3]["staffing_status"] = "retired"
    before = deepcopy(state)
    # When succession is accepted without all gates.
    with pytest.raises(governance.GovernanceError):
        run(program, state, "transfer-accept", receipt, "evidence/TASK-A/attempt-1/accept.json")
    # Then no handoff, actor or claim history changes.
    assert state == before


def test_retirement_preserves_observation_and_history(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given a previously recorded idle worker without owned work.
    receipt = {
        **actor("other-worker"),
        "decision": "new",
        "reason": "Bounded lookup",
        "observed_status": "idle",
        "observed_at": "2026-01-01T00:00:00Z",
    }
    run(program, state, "actor-record", receipt)
    # When the director records a staffing retirement decision.
    run(
        program, state, "actor-record", {**receipt, "decision": "retire", "reason": "Work finished"}
    )
    # Then retired is staffing metadata and does not fabricate a new runtime observation.
    result = governance.snapshot_metadata(state)["actors"][0]
    assert result["staffing_status"] == "retired"
    assert result["observed_status"] == "idle"
    assert [item["decision"] for item in result["history"]] == ["new", "retire"]


@pytest.mark.parametrize("members", [[], ["missing"], ["TASK-A", "TASK-A"]])
def test_objective_requires_explicit_known_unique_membership(
    state: governance.Record, members: list[str]
) -> None:
    # Given an objective with invalid task membership.
    goal(state)
    state["objective"]["task_ids"] = members
    # When the optional state is validated.
    with pytest.raises(governance.GovernanceError, match="task membership"):
        governance.validate_governance(state)
    # Then no arbitrary task set is inferred.


def test_objective_contract_excludes_progress_but_retains_declared_criteria() -> None:
    state: governance.Record = {
        "objective": {
            "id": "goal",
            "title": "Agreed outcome",
            "criteria": ["Verify behavior"],
            "task_ids": ["TASK-A"],
            "supplied_by": "user",
            "status": "active",
            "next_action_owner": "director",
            "history": [],
        }
    }
    before = governance.objective_contract(state)
    goal = governance._record(state["objective"], "objective")
    goal.update(
        {
            "status": "awaiting_next_goal",
            "next_action_owner": "user",
            "history": [{"status": "active"}],
        }
    )
    assert governance.objective_contract(state) == before
    goal["criteria"] = ["Another acceptance criterion"]
    assert governance.objective_contract(state) != before
    assert governance.objective_contract({}) is None


def test_current_director_identity_is_unknown_for_legacy_state(state: governance.Record) -> None:
    # Given no recorded director authority.
    # When a question adapter checks current leadership.
    identity = governance.current_director_id(state)
    # Then the helper does not promote a saved worker or the legacy owner sentinel.
    assert identity is None


def test_duplicate_receipt_keys_rejected_without_state_mutation(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given an ambiguous JSON receipt.
    program.path("input.json").write_text('{"categories":[],"categories":[]}', encoding="utf-8")
    before = deepcopy(state)
    # When a local mutation reads it.
    with pytest.raises(governance.GovernanceError, match="Duplicate JSON"):
        governance.apply_command(
            program, state, argparse.Namespace(command="category-set", input="input.json")
        )
    # Then the prior canonical state is untouched.
    assert state == before


def test_changed_source_can_be_reacknowledged_without_erasing_transfer_history(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given acknowledged director succession whose source subsequently changes.
    appoint(program, state)
    run(
        program,
        state,
        "transfer",
        outgoing(program, "director"),
        "evidence/_coordination/attempt-1/transfer.json",
    )
    run(
        program,
        state,
        "transfer-ack",
        ack(program, "director-next"),
        "evidence/_coordination/attempt-1/ack-first.json",
    )
    first = deepcopy(state["handoffs"][0]["acknowledgement"])
    stale_acceptance = ack(program, "director-old")
    (program.repo / "src/tool.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(governance.GovernanceError, match="Stale scoped source"):
        run(
            program,
            state,
            "transfer-accept",
            stale_acceptance,
            "evidence/_coordination/attempt-1/stale-accept.json",
        )
    assert state["director"]["actor_id"] == "director-old"
    # When the successor records fresh validation and the director accepts it.
    run(
        program,
        state,
        "transfer-ack",
        ack(program, "director-next"),
        "evidence/_coordination/attempt-1/ack-second.json",
    )
    run(
        program,
        state,
        "transfer-accept",
        ack(program, "director-old"),
        "evidence/_coordination/attempt-1/accept.json",
    )
    # Then current authority changes only after acceptance and both receipts remain frozen.
    assert state["director"]["actor_id"] == "director-next"
    history = state["handoffs"][0]["acknowledgements"]
    assert len(history) == 2 and history[0]["receipt"] == first
    assert history[0]["receipt"]["source_digest"] != history[1]["receipt"]["source_digest"]
    program.verify_refs(governance.evidence_references(state))


@dataclass(slots=True)
class RacingEvidenceProgram(SyntheticProgram):
    """Isolated adapter changes a supplied file at the receipt hash boundary."""

    after_hash: bool = False

    def evidence(self, task_id: str, attempt: int, names: list[str]) -> list[governance.Json]:
        if not self.after_hash:
            self.path(names[0]).write_text("{}", encoding="utf-8")
        refs = SyntheticProgram.evidence(self, task_id, attempt, names)
        if self.after_hash:
            self.path(names[0]).write_text("{}", encoding="utf-8")
        return refs


@pytest.mark.parametrize("after_hash", [False, True])
def test_frozen_receipt_races_cannot_publish_unbound_metadata(
    program: SyntheticProgram, state: governance.Record, after_hash: bool
) -> None:
    # Given a source-valid appointment whose JSON file changes during freezing.
    racing = RacingEvidenceProgram(program.directory, program.repo, after_hash=after_hash)
    roster(state)
    before = deepcopy(state)
    receipt = {
        **program.binding(),
        "actor_id": "director-old",
        "acknowledged": True,
        "authority": {
            "authorized_by": "user",
            "basis": "Recorded appointment",
            "merge_target": "beta",
        },
        "evidence": [],
    }
    # When freezing observes bytes that differ from validated metadata.
    with pytest.raises(governance.GovernanceError):
        run(
            racing,
            state,
            "director-appoint",
            receipt,
            "evidence/_coordination/attempt-1/appointment.json",
        )
    # Then no authority record or canonical-state mutation is published.
    assert state == before


@pytest.mark.parametrize(
    "defect",
    ["objective", "head", "digest", "narrow", "director", "implementer", "criteria", "findings"],
)
def test_integrated_review_rejects_stale_or_incomplete_acceptance(
    program: SyntheticProgram, state: governance.Record, defect: str
) -> None:
    # Given an objective verdict whose integrated review misses a required invariant.
    appoint(program, state)
    goal(state)
    receipt = completion(program)
    review_name = receipt["integrated_review"]
    assert isinstance(review_name, str)
    review = json.loads(program.path(review_name).read_text(encoding="utf-8"))
    match defect:
        case "objective":
            review["objective_id"] = "other-goal"
        case "head":
            review["source_head"] = "b" * 40
        case "digest":
            review["source_digest"] = "e" * 64
        case "narrow":
            (program.repo / "src/other.py").write_text("VALUE = 3\n", encoding="utf-8")
            review["scope"] = ["src/other.py"]
            review["source_digest"] = program.source_digest(["src/other.py"])
        case "director":
            review["reviewer"] = "director-old"
        case "implementer":
            review["reviewer"] = "worker-old"
        case "criteria":
            review["criteria"] = []
        case "findings":
            review["unresolved_findings"] = ["Acceptance remains unresolved"]
    program.receipt(review_name, review)
    before = deepcopy(state)
    # When current objective completion is attempted.
    with pytest.raises(governance.GovernanceError):
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then historical child reviews cannot substitute for fresh independent integrated acceptance.
    assert state == before


def test_source_changes_require_a_fresh_independent_integrated_review(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given completed child work followed by a later scoped source edit.
    appoint(program, state)
    goal(state)
    receipt = completion(program)
    old_task = deepcopy(state["tasks"][0])
    (program.repo / "src/tool.py").write_text("VALUE = 2\n", encoding="utf-8")
    receipt.update(program.binding())
    before = deepcopy(state)
    with pytest.raises(governance.GovernanceError, match="Stale scoped source"):
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/stale-complete.json",
        )
    assert state == before
    # When a new independent review covers the actual integrated bytes.
    run(
        program,
        state,
        "objective-complete",
        completion(program),
        "evidence/_coordination/attempt-1/fresh-complete.json",
    )
    # Then final acceptance succeeds without rewriting historical child receipts.
    assert state["objective"]["status"] == "awaiting_next_goal"
    assert state["tasks"][0] == old_task
    program.verify_refs(governance.evidence_references(state))


def test_aggregate_review_cannot_omit_member_source_scope(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given a goal receipt whose aggregate scope excludes its member's accepted source.
    appoint(program, state)
    goal(state)
    receipt = completion(program)
    (program.repo / "src/other.py").write_text("VALUE = 3\n", encoding="utf-8")
    receipt["scope"] = ["src/other.py"]
    receipt["source_digest"] = program.source_digest(["src/other.py"])
    before = deepcopy(state)
    # When the director tries to narrow integrated acceptance away from changed task bytes.
    with pytest.raises(governance.GovernanceError, match="does not cover"):
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then the objective remains active.
    assert state == before


def test_receipt_growth_at_open_is_bounded_and_preserves_state(
    program: SyntheticProgram, state: governance.Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a small valid receipt that grows after metadata validation, immediately before reading.
    name = program.receipt("input.json", {"categories": [], "reason": "Scoped grouping"})
    target = program.path(name)
    original_open = Path.open
    grew = False

    def grow_before_read(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ):
        nonlocal grew
        if path == target and mode == "rb" and not grew:
            grew = True
            with original_open(path, "ab") as stream:
                stream.write(b" " * (1024 * 1024))
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", grow_before_read)
    before = deepcopy(state)
    # When the mutation reaches the shared actual read boundary.
    with pytest.raises(governance.GovernanceError, match="bounded read"):
        governance.apply_command(
            program, state, argparse.Namespace(command="category-set", input=name)
        )
    # Then the file cannot bypass the limit through stale metadata and no state is changed.
    assert grew and state == before


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_receipt_values_never_enter_state(
    program: SyntheticProgram, state: governance.Record, constant: str
) -> None:
    # Given JSON whose otherwise-unused metadata contains a nonfinite number.
    program.path("input.json").write_text(
        '{"categories":[],"reason":"Scoped grouping","extra":' + constant + "}", encoding="utf-8"
    )
    before = deepcopy(state)
    # When a command reads the receipt through the shared strict parser.
    with pytest.raises(governance.GovernanceError, match="Nonfinite"):
        governance.apply_command(
            program, state, argparse.Namespace(command="category-set", input="input.json")
        )
    # Then even ignored fields cannot publish nonfinite canonical event metadata.
    assert state == before


@dataclass(slots=True)
class ABAEvidenceProgram(SyntheticProgram):
    """Alternate receipt bytes around parsing and verification without concurrent timing."""

    target_name: str = "appointment.json"
    admitted_path: str = ""
    admitted_bytes: bytes = b""

    def evidence(self, task_id: str, attempt: int, names: list[str]) -> list[governance.Json]:
        if not names[0].endswith(self.target_name):
            return SyntheticProgram.evidence(self, task_id, attempt, names)
        original = self.path(names[0]).read_bytes()
        denied = json.loads(original)
        if "acknowledged" in denied:
            denied["acknowledged"] = False
        else:
            denied["verdict"] = "rejected"
        self.admitted_path = names[0]
        self.admitted_bytes = json.dumps(denied).encode()
        self.path(names[0]).write_bytes(self.admitted_bytes)
        refs = SyntheticProgram.evidence(self, task_id, attempt, names)
        # A semantic reread would now see the allowed metadata again.
        self.path(names[0]).write_bytes(original)
        return refs

    def verify_refs(self, refs: list[governance.Json]) -> None:
        if self.admitted_path:
            # A separately performed hash verification would see the denied bytes.
            self.path(self.admitted_path).write_bytes(self.admitted_bytes)
        SyntheticProgram.verify_refs(self, refs)


def test_aba_receipt_cannot_bind_allowed_metadata_to_denied_evidence(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given alternating receipt bytes that fool semantic reread plus separate hash verification.
    racing = ABAEvidenceProgram(program.directory, program.repo)
    roster(state)
    before = deepcopy(state)
    receipt = {
        **program.binding(),
        "actor_id": "director-old",
        "acknowledged": True,
        "authority": {
            "authorized_by": "user",
            "basis": "Recorded appointment",
            "merge_target": "beta",
        },
        "evidence": [],
    }
    # When the admitted reference differs from the exact bytes parsed for validated metadata.
    with pytest.raises(governance.GovernanceError, match="Receipt changed"):
        run(
            racing,
            state,
            "director-appoint",
            receipt,
            "evidence/_coordination/attempt-1/appointment.json",
        )
    # Then no authority is recorded from that unbound proof.
    assert state == before


def test_integrated_review_aba_cannot_accept_rejected_artifact(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given accepted review metadata alternating with a rejected artifact at hash boundaries.
    appoint(program, state)
    goal(state)
    receipt = completion(program)
    racing = ABAEvidenceProgram(
        program.directory, program.repo, target_name="integrated-review.json"
    )
    before = deepcopy(state)
    # When completion attempts to freeze the falsely associated review bytes.
    with pytest.raises(governance.GovernanceError, match="Receipt changed"):
        run(
            racing,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then historical child acceptance cannot turn a rejected final artifact into completion.
    assert state == before


@pytest.mark.parametrize("role", ["director", "sub_director"])
def test_pending_leadership_handoff_blocks_objective_even_outside_its_scope(
    program: SyntheticProgram, state: governance.Record, role: str
) -> None:
    # Given unresolved leadership succession on a disjoint source scope.
    appoint(program, state)
    (program.repo / "src/other.py").write_text("VALUE = 3\n", encoding="utf-8")
    transfer = {
        **outgoing(program, "director"),
        "scope": ["src/other.py"],
        "source_digest": program.source_digest(["src/other.py"]),
        "changed_paths": [],
        "role": role,
    }
    if role == "sub_director":
        state["actors"].extend(
            [
                actor("manager-old", "sub_director", "director-old"),
                actor("manager-next", "sub_director", "director-old"),
            ]
        )
        transfer.update({"from_actor": "manager-old", "to_actor": "manager-next"})
    run(program, state, "transfer", transfer, "evidence/_coordination/attempt-1/transfer.json")
    goal(state)
    before = deepcopy(state)
    # When the current director asks to accept the objective.
    with pytest.raises(governance.GovernanceError, match="unresolved relevant handoff"):
        run(
            program,
            state,
            "objective-complete",
            completion(program),
            "evidence/_coordination/attempt-1/complete.json",
        )
    # Then unsettled leadership blocks acceptance without rewriting the objective or history.
    assert state == before


@pytest.mark.parametrize("relationship", ["member", "overlapping-scope", "unrelated"])
def test_worker_handoff_gate_uses_objective_membership_and_scope(
    program: SyntheticProgram, state: governance.Record, relationship: str
) -> None:
    # Given either a member handoff or another task's pending succession.
    appoint(program, state)
    if relationship == "member":
        run(
            program, state, "transfer", outgoing(program), "evidence/TASK-A/attempt-1/transfer.json"
        )
        goal(state)
    else:
        goal(state)
        (program.repo / "src/other.py").write_text("VALUE = 3\n", encoding="utf-8")
        scope = ["src/tool.py"] if relationship == "overlapping-scope" else ["src/other.py"]
        state["actors"].extend(
            [
                actor("other-worker", parent="director-old"),
                actor("other-successor", parent="director-old"),
            ]
        )
        state["tasks"].append(
            {
                "id": "TASK-B",
                "workstream": "engineering",
                "status": "active",
                "owner": "other-worker",
                "attempts": [
                    {
                        "number": 1,
                        "owner": "other-worker",
                        "status": "active",
                        "scope": scope,
                        "evidence": [],
                    }
                ],
            }
        )
        transfer = {
            **outgoing(program),
            "task_id": "TASK-B",
            "from_actor": "other-worker",
            "to_actor": "other-successor",
            "scope": scope,
            "source_digest": program.source_digest(scope),
            "changed_paths": [],
        }
        run(program, state, "transfer", transfer, "evidence/TASK-B/attempt-1/transfer.json")
    before = deepcopy(state)
    receipt = completion(program)
    # When the final verdict is recorded with that handoff still pending.
    if relationship == "unrelated":
        run(
            program,
            state,
            "objective-complete",
            receipt,
            "evidence/_coordination/attempt-1/complete.json",
        )
        # Then disjoint unrelated work remains pending and does not become silently accepted.
        assert state["objective"]["status"] == "awaiting_next_goal"
        assert state["handoffs"][0]["acceptance"] is None
    else:
        with pytest.raises(governance.GovernanceError, match="unresolved relevant handoff"):
            run(
                program,
                state,
                "objective-complete",
                receipt,
                "evidence/_coordination/attempt-1/complete.json",
            )
        # Then member or overlapping-scope succession blocks acceptance atomically.
        assert state == before


def test_accepted_director_handoff_allows_current_director_objective_acceptance(
    program: SyntheticProgram, state: governance.Record
) -> None:
    # Given succession that has both fresh acknowledgement and explicit director acceptance.
    appoint(program, state)
    run(
        program,
        state,
        "transfer",
        outgoing(program, "director"),
        "evidence/_coordination/attempt-1/transfer.json",
    )
    run(
        program,
        state,
        "transfer-ack",
        ack(program, "director-next"),
        "evidence/_coordination/attempt-1/ack.json",
    )
    run(
        program,
        state,
        "transfer-accept",
        ack(program, "director-old"),
        "evidence/_coordination/attempt-1/accept.json",
    )
    goal(state)
    receipt = completion(program)
    receipt["actor_id"] = "director-next"
    # When the new current director accepts verified objective evidence.
    run(
        program,
        state,
        "objective-complete",
        receipt,
        "evidence/_coordination/attempt-1/complete.json",
    )
    # Then accepted historical transfer does not remain a blocker.
    assert state["objective"]["status"] == "awaiting_next_goal"
    assert state["handoffs"][0]["acceptance"] is not None
    program.verify_refs(governance.evidence_references(state))
