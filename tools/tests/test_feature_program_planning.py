"""Reviewed additive admission and exact active membership regressions."""

# noqa: SIZE_OK - One approved test module holds the complete additive admission
# acceptance matrix, reusing existing isolated core and phase fixtures.

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Literal, assert_never

import pytest

from tools.tests.test_feature_program_phases import GateFixture, create_program, gate  # noqa: F401
from tools.tests.test_feature_program import fp


def planned_input(gate: GateFixture, operation: str = "task-plan") -> fp.Record:
    state = gate.program.load()
    return {
        "schema_version": 1,
        "operation": operation,
        "id": "planned-edit",
        "program_id": state["program_id"],
        "objective_id": "goal",
        "actor_id": "director",
        "expected_revision": fp.phases.revision(state),
        "reason": "Make the explicit planned action current",
        "changes": [
            {
                "id": "S01",
                "before": {"next_action": gate.program.task(state, "S01")["next_action"]},
                "after": {"next_action": "Inspect the current implementation"},
            }
        ],
    }


def test_planned_next_action_preserves_binding_and_membership(gate: GateFixture) -> None:
    # Given an accepted baseline and an explicit before/after runtime action.
    gate.ready()
    before = gate.program.load()
    data = planned_input(gate)
    # When the proposal is constructed without publication.
    future = fp.planning.construct(gate.program, before, data)
    # Then only the action and immutable amendment change, not scope or membership.
    assert future.before_binding == future.after_binding
    assert future.state["objective"] == before["objective"]
    assert (
        gate.program.task(future.state, "S01")["next_action"]
        == "Inspect the current implementation"
    )


@pytest.mark.parametrize("before_phase", [True, 1.0])
def test_cli_rejects_wrong_json_type_in_previous_metadata(
    gate: GateFixture, before_phase: fp.Json
) -> None:
    # Given valid independently renewed phase metadata and a wrong-type previous value.
    gate.ready()
    data = planned_input(gate)
    data["changes"] = [{"id": "S01", "before": {"phase": 1}, "after": {"phase": 2}}]
    name, data = reviewed_input(gate, data)
    data["changes"][0]["before"]["phase"] = before_phase
    gate.program.path(name).write_bytes(fp.canonical(data))
    before = gate.program.path("backlog.json").read_bytes()
    command = [
        sys.executable,
        "-B",
        fp.__file__,
        "--repo",
        str(gate.program.repo),
        "--program-dir",
        "discovery",
        "task-plan",
        "--input",
        name,
    ]
    # When the actual standalone CLI receives the incorrect previous JSON type.
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    after = gate.program.path("backlog.json").read_bytes()
    print(
        json.dumps(
            {
                "command": command,
                "before_phase": before_phase,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "canonical_before_sha256": fp.sha(before),
                "canonical_after_sha256": fp.sha(after),
            },
            sort_keys=True,
        )
    )
    # Then it rejects the complete transaction without changing canonical bytes.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Stale before metadata" in result.stderr
    assert after == before


@pytest.mark.parametrize("wrong_type", [True, 1.0])
@pytest.mark.parametrize("seam", ["receipt", "chain", "effective", "priority"])
def test_mutation_replay_rejects_json_type_drift(
    gate: GateFixture, seam: str, wrong_type: fp.Json
) -> None:
    # Given a valid proposal with explicit integer fields retained in its history.
    gate.ready()
    data = planned_input(gate, "task-prioritize" if seam == "priority" else "task-plan")
    data["changes"] = (
        [{"id": "S01", "before": None, "after": 1}]
        if seam == "priority"
        else [
            {
                "id": "S01",
                "before": {"phase": 1, "planning_note": "Synthetic only"},
                "after": {"phase": 1, "planning_note": "Explicit updated planning"},
            }
        ]
    )
    future = fp.planning.construct(gate.program, gate.program.load(), data)
    state = future.state
    event = state["amendments"][-1]
    if seam == "receipt":
        event["receipt"]["changes"][0]["after"]["phase"] = wrong_type
    elif seam == "chain":
        second = deepcopy(event)
        second["id"] = "second"
        second["receipt"]["id"] = "second"
        patch = [{"id": "S01", "before": {"phase": wrong_type}, "after": {"phase": 2}}]
        second["changes"] = deepcopy(patch)
        second["receipt"]["changes"] = deepcopy(patch)
        state["amendments"].append(second)
        gate.program.task(state, "S01")["phase"] = 2
    elif seam == "effective":
        gate.program.task(state, "S01")["phase"] = wrong_type
    else:
        state["execution_priorities"] = {"S01": wrong_type}
    # When exact replay integrity is validated.
    # Then equal numeric Python values with distinct JSON types are rejected.
    with pytest.raises(fp.planning.PlanningError):
        fp.planning.validate_mutations(state)


def test_priority_preserves_binding_and_orders_ready_tasks(gate: GateFixture) -> None:
    # Given an accepted baseline and a lower explicit priority for the second task.
    gate.ready()
    before = gate.program.load()
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S02", "before": None, "after": -1}]
    # When the proposal is built.
    future = fp.planning.construct(gate.program, before, data)
    # Then priority is independent of scope and affects the actual ready consumer.
    assert future.before_binding == future.after_binding
    assert [task["id"] for task in gate.program.status(future.state, True)["candidates"]] == [
        "S02",
        "S01",
    ]


def proposal(gate: GateFixture) -> fp.Record:
    program = gate.program
    name = "source/admissions/intake/report.md"
    path = program.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "### S03. New unit\n\nExpected result: third unit.\nAcceptance: verify third unit.\n",
        encoding="utf-8",
    )
    task: fp.Record = {
        "id": "S03",
        "phase": 2,
        "workstream": "delivery",
        "candidate_paths": ["src/S03.py"],
        "depends_on": [],
        "related_to": [],
        "conflict_groups": [],
        "required_checks": ["acceptance", "scoped_tests", "format_lint"],
        "next_action": "Revalidate new implementation",
        "expected_result": "Third unit available",
        "acceptance_criteria": ["Verify third unit"],
    }
    plan_name = "source/admissions/intake/plan.json"
    program.path(plan_name).write_bytes(fp.canonical({"schema_version": 1, "tasks": [task]}))
    current = program.load()
    return {
        "schema_version": 1,
        "operation": "task-admit",
        "id": "intake",
        "program_id": current["program_id"],
        "actor_id": "director",
        "objective_id": "goal",
        "expected_revision": fp.phases.revision(current),
        "reason": "Reviewed new required unit",
        "intake_authors": ["author"],
        "source": {
            "path": path.relative_to(program.repo).as_posix(),
            "sha256": fp.sha(path.read_bytes()),
            "task_ids": ["S03"],
            "review_baseline": current["baseline_head"],
            "reference_base": program.directory.relative_to(program.repo).as_posix(),
        },
        "planning": {
            "path": program.path(plan_name).relative_to(program.repo).as_posix(),
            "sha256": fp.sha(program.path(plan_name).read_bytes()),
        },
        "membership": {
            "before": ["S01", "S02"],
            "after": ["S01", "S02", "S03"],
            "added": ["S03"],
            "removed": [],
        },
    }


def test_admission_binding_is_read_only_and_binds_future_membership(
    gate: GateFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an explicitly adopted and accepted baseline.
    gate.ready()
    data = proposal(gate)
    name = gate.fixture.write("evidence/_planning/attempt-1/proposal.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    # When the reviewed admission binding is requested.
    assert gate.fixture.run("admission-binding", "--input", name) == 0
    # Then the prospective membership is bound without files or a lock being written.
    preview = json.loads(capsys.readouterr().out)
    assert preview["after_binding"]["task_ids"] == ["S01", "S02", "S03"]
    assert (
        preview["after_binding"]["objective_digest"]
        != preview["before_binding"]["objective_digest"]
    )
    assert gate.program.path("backlog.json").read_bytes() == before
    assert not gate.program.path(".feature-program.lock").exists()


def reviewed_input(gate: GateFixture, data: fp.Record | None = None) -> tuple[str, fp.Record]:
    data = proposal(gate) if data is None else data
    label = str(data["id"])
    candidate = f"evidence/_phases/attempt-1/{label}-candidate.md"
    gate.program.path(candidate).write_text(
        "Synthetic future inventory; independently inspected source, paths and membership.\n",
        encoding="utf-8",
    )
    spec: fp.Record = {
        "candidate_path": candidate,
        "candidate_digest": fp.sha(gate.program.path(candidate).read_bytes()),
        "inventory_authors": ["author", "director"],
    }
    data["inventory"] = spec
    result = fp.planning.preview(gate.program, gate.program.load(), data)
    data.update(
        {"before_binding": result["before_binding"], "after_binding": result["after_binding"]}
    )
    if "predecessor_before" in result:
        data["predecessor_before"] = result["predecessor_before"]
    bound = {
        **fp.record(result["after_binding"], "binding"),
        "expected_revision": result["expected_revision"],
        "units": result["units"],
        "reason": "Synthetic independently reviewed future contract",
    }
    inventory = {**bound, **spec, "actor_id": "director"}
    review = {
        **bound,
        "actor_id": "reviewer",
        "reviewer_role": "independent",
        "verdict": "accepted",
        "unresolved_findings": [],
        "candidate_digest": spec["candidate_digest"],
        "inventory_digest": result["inventory_digest"],
    }
    acceptance = {
        **bound,
        "actor_id": "director",
        "acceptance_complete": True,
        "candidate_digest": spec["candidate_digest"],
        "inventory_digest": result["inventory_digest"],
    }
    renewal: fp.Record = {}
    for key, value in (("inventory", inventory), ("review", review), ("acceptance", acceptance)):
        renewal[key] = gate.fixture.write(f"evidence/_phases/attempt-1/{label}-{key}.json", value)
    evidence = f"evidence/_planning/attempt-1/{label}-review.md"
    gate.program.path(evidence).parent.mkdir(parents=True, exist_ok=True)
    gate.program.path(evidence).write_text(
        "Synthetic independent findings and director decision for future source and membership.\n",
        encoding="utf-8",
    )
    data.update({"renewal": renewal, "evidence": [evidence]})
    name = gate.fixture.write(f"evidence/_planning/attempt-1/{label}.json", data)
    return name, data


def runtime_input(gate: GateFixture, data: fp.Record) -> str:
    result = fp.planning.preview(gate.program, gate.program.load(), data)
    data.update(
        {"before_binding": result["before_binding"], "after_binding": result["after_binding"]}
    )
    evidence = f"evidence/_planning/attempt-1/{data['id']}-rationale.md"
    gate.program.path(evidence).parent.mkdir(parents=True, exist_ok=True)
    gate.program.path(evidence).write_text(
        "Synthetic director rationale for this exact planning change.\n", encoding="utf-8"
    )
    data["evidence"] = [evidence]
    return gate.fixture.write(f"evidence/_planning/attempt-1/{data['id']}.json", data)


def decomposition_input(
    gate: GateFixture,
    operation: str = "task-split",
    predecessor: str = "S01",
    children: tuple[str, ...] = ("S03", "S04"),
) -> fp.Record:
    """Declare an exact reviewed decomposition without inheriting execution evidence."""
    data = planned_input(gate, operation)
    data.pop("changes")
    data["id"] = f"{operation}-{predecessor}"
    current = gate.program.load()
    members = list(fp.record(current["objective"], "objective")["task_ids"])
    data.update(
        {
            "operation": operation,
            "predecessor": predecessor,
            "lineage": {"predecessor": predecessor, "successors": []},
            "dependency_changes": [],
            "membership": {
                "before": members,
                "after": [key for key in members if key != predecessor],
                "added": [],
                "removed": [predecessor],
            },
        }
    )
    if operation == "task-retire":
        return data
    report_path = gate.program.path(f"source/decompositions/{data['id']}/report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            f"### {key}. New child\n\nExpected result: {key}.\nAcceptance: {key}.\n"
            for key in children
        ),
        encoding="utf-8",
    )
    definitions: list[fp.Json] = [
        {
            "id": key,
            "phase": 2,
            "workstream": "delivery",
            "candidate_paths": [f"src/{key}.py"],
            "depends_on": [],
            "related_to": [],
            "conflict_groups": [],
            "required_checks": ["acceptance", "scoped_tests", "format_lint"],
            "next_action": "Revalidate independent child",
            "expected_result": f"New output {key}",
            "acceptance_criteria": [f"Verify {key}"],
        }
        for key in children
    ]
    plan_path = report_path.with_name("plan.json")
    plan_path.write_bytes(fp.canonical({"schema_version": 1, "tasks": definitions}))
    data.update(
        {
            "intake_authors": ["author"],
            "source": {
                "path": report_path.relative_to(gate.program.repo).as_posix(),
                "sha256": fp.sha(report_path.read_bytes()),
                "task_ids": list(children),
                "review_baseline": current["baseline_head"],
                "reference_base": gate.program.directory.relative_to(gate.program.repo).as_posix(),
            },
            "planning": {
                "path": plan_path.relative_to(gate.program.repo).as_posix(),
                "sha256": fp.sha(plan_path.read_bytes()),
            },
        }
    )
    data["lineage"] = {"predecessor": predecessor, "successors": list(children)}
    fp.record(data["membership"], "membership").update(
        {
            "after": [key for key in members if key != predecessor] + list(children),
            "added": list(children),
        }
    )
    return data


def test_split_proposal_retains_predecessor_and_starts_independent_children(
    gate: GateFixture,
) -> None:
    # Given an accepted baseline and a genuinely new two-child reviewed intake.
    gate.ready()
    before = gate.program.load()
    data = decomposition_input(gate)
    # When the exact prospective transaction is constructed without publication.
    future = fp.planning.construct(gate.program, before, data)
    # Then retained history remains, children are fresh, and only current membership changes.
    parent = gate.program.task(future.state, "S01")
    assert parent["disposition"] == "split" and parent["status"] == "planned"
    assert parent["lineage"] == {"predecessor": None, "successors": ["S03", "S04"]}
    assert parent["attempts"] == gate.program.task(before, "S01")["attempts"]
    for key in ("S03", "S04"):
        child = gate.program.task(future.state, key)
        assert (child["status"], child["owner"], child["attempts"]) == ("planned", None, [])
        assert child["lineage"] == {"predecessor": "S01", "successors": []}
    assert future.state["objective"]["task_ids"] == ["S02", "S03", "S04"]
    assert future.state["objective"]["membership_revision"] == 1
    assert future.before_binding != future.after_binding


@pytest.mark.parametrize("operation", ["task-split", "task-retire"])
def test_standalone_decomposition_preserves_history_and_guards_disposed_work(
    gate: GateFixture, operation: str
) -> None:
    # Given a prioritized, claimed then released predecessor and actual frozen evidence.
    gate.ready()
    priority = planned_input(gate, "task-prioritize")
    priority["changes"] = [{"id": "S01", "before": None, "after": -7}]
    assert gate.fixture.run("task-prioritize", "--input", runtime_input(gate, priority)) == 0
    assert gate.fixture.claim() == 0
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Decompose",
            "--next-action",
            "Review children",
        )
        == 0
    )
    before = gate.program.load()
    saved = {
        path: path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file() and ("evidence" in path.parts or path.name in {"source.md", "plan.json"})
    }
    data = decomposition_input(gate, operation)
    name, _ = reviewed_input(gate, data)
    command = [
        sys.executable,
        "-B",
        fp.__file__,
        "--repo",
        str(gate.program.repo),
        "--program-dir",
        "discovery",
        operation,
        "--input",
        name,
    ]
    # When the standalone CLI publishes the independently reviewed decomposition.
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    print(
        json.dumps(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            sort_keys=True,
        )
    )
    # Then history/priority/source remain unchanged and disposed work cannot execute.
    assert result.returncode == 0, result.stderr
    current = gate.program.load()
    parent = gate.program.task(current, "S01")
    assert {
        key: value for key, value in parent.items() if key not in {"disposition", "lineage"}
    } == gate.program.task(before, "S01")
    assert current["execution_priorities"] == {"S01": -7}
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    assert current["events"][:-1] == before["events"]
    assert current["objective"]["criteria"] == before["objective"]["criteria"]
    assert not any(item["id"] == "S01" for item in gate.program.status(current, True)["candidates"])
    canonical = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.claim("S01", number=2) == 1
    with pytest.raises(fp.phases.PhaseError, match="Disposed"):
        gate.program.claim(current, argparse.Namespace(id="S01", scope=["src/S01.py"]))
    assert gate.program.path("backlog.json").read_bytes() == canonical
    assert gate.fixture.run(operation, "--input", name) == 1
    assert gate.fixture.run("check") == 0
    if operation == "task-split":
        for key in ("S03", "S04"):
            task = gate.program.task(current, key)
            assert task["attempts"] == [] and task["owner"] is None
            assert key not in current["execution_priorities"]
        assert gate.fixture.claim("S03") == 0


@pytest.mark.parametrize(
    "fault",
    [
        "active",
        "owned-peer",
        "missing-patch",
        "cycle",
        "stale",
        "actor",
        "binding",
        "one-child",
        "duplicate",
        "delta",
        "irrelevant-retirement",
        "empty",
        "source-drift",
        "unsafe-child",
        "unknown-predecessor",
        "unknown-dependency",
    ],
)
def test_decomposition_rejects_invalid_transaction_without_publication(
    gate: GateFixture, fault: str
) -> None:
    # Given an accepted baseline and explicit dependent work when appropriate.
    if fault in {"missing-patch", "cycle"}:
        state = gate.program.load()
        gate.program.task(state, "S02")["depends_on"] = ["S01"]
        gate.program.save(state)
    elif fault == "empty":
        state = gate.program.load()
        state["objective"]["task_ids"] = ["S01"]
        gate.program.save(state)
    gate.ready()
    if fault == "active":
        assert gate.fixture.claim() == 0
    if fault == "owned-peer":
        assert gate.fixture.claim("S02") == 0
    data = decomposition_input(
        gate, "task-retire" if fault in {"irrelevant-retirement", "empty"} else "task-split"
    )
    if fault == "owned-peer":
        state = gate.program.load()
        data["dependency_changes"] = [
            {"id": "S02", "before": [], "after": ["S03"], "reason": "First output"}
        ]
    else:
        state = gate.program.load()
    if fault == "cycle":
        data["dependency_changes"] = [
            {"id": "S02", "before": ["S01"], "after": ["S03"], "reason": "First output"}
        ]
        plan = fp.record(data["planning"], "planning")
        path = fp.safe_path(gate.program.repo, str(plan["path"]))
        metadata = fp.read_json(path)
        metadata["tasks"][0]["depends_on"] = ["S02"]
        path.write_bytes(fp.canonical(metadata))
        plan["sha256"] = fp.sha(path.read_bytes())
    elif fault == "stale":
        data["expected_revision"] = "0" * 64
    elif fault == "actor":
        data["actor_id"] = "reviewer"
    elif fault == "binding":
        data["predecessor_before"] = {"definition_digest": "0" * 64}
    elif fault == "one-child":
        data = decomposition_input(gate, children=("S03",))
    elif fault == "duplicate":
        data["source"]["task_ids"] = ["S03", "S03"]
    elif fault == "delta":
        data["membership"]["removed"] = ["S01", "S02"]
    elif fault == "irrelevant-retirement":
        data["category_placements"] = []
    elif fault == "empty":
        data["membership"]["after"] = []
    elif fault == "source-drift":
        fp.safe_path(gate.program.repo, str(data["source"]["path"])).write_text(
            "Changed source", encoding="utf-8"
        )
    elif fault == "unknown-predecessor":
        data["predecessor"] = "UNKNOWN"
    elif fault in {"unsafe-child", "unknown-dependency"}:
        path = fp.safe_path(gate.program.repo, str(data["planning"]["path"]))
        metadata = fp.read_json(path)
        metadata["tasks"][0]["candidate_paths" if fault == "unsafe-child" else "depends_on"] = [
            "../outside.py" if fault == "unsafe-child" else "UNKNOWN"
        ]
        path.write_bytes(fp.canonical(metadata))
        data["planning"]["sha256"] = fp.sha(path.read_bytes())
    before = gate.program.path("backlog.json").read_bytes()
    # When an invalid prospective transaction is parsed, it fails before publication.
    with pytest.raises(
        (
            fp.ProgramError,
            fp.planning.PlanningError,
            fp.coordination.GovernanceError,
            fp.phases.PhaseError,
        )
    ):
        fp.planning.construct(gate.program, state, data)
    # Then actual canonical bytes are unchanged.
    assert gate.program.path("backlog.json").read_bytes() == before
    name = gate.fixture.write("evidence/_planning/attempt-1/rejected-decomposition.json", data)
    command = [
        sys.executable,
        "-B",
        fp.__file__,
        "--repo",
        str(gate.program.repo),
        "--program-dir",
        "discovery",
        str(data["operation"]),
        "--input",
        name,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    print(
        json.dumps(
            {
                "fault": fault,
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "canonical_before_sha256": fp.sha(before),
                "canonical_after_sha256": fp.sha(gate.program.path("backlog.json").read_bytes()),
            },
            sort_keys=True,
        )
    )
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert gate.program.path("backlog.json").read_bytes() == before


def test_split_dependency_replay_then_plan(gate: GateFixture) -> None:
    # Given an earlier PLAN dependency patch bound to a real independent renewal.
    gate.ready()
    data = planned_input(gate)
    data["changes"] = [
        {"id": "S02", "before": {"depends_on": []}, "after": {"depends_on": ["S01"]}}
    ]
    assert gate.fixture.run("task-plan", "--input", reviewed_input(gate, data)[0]) == 0
    data = decomposition_input(gate)
    data["dependency_changes"] = [
        {"id": "S02", "before": ["S01"], "after": ["S03"], "reason": "Only first output needed"}
    ]
    # When split explicitly rewrites that edge and later PLAN changes it again.
    assert gate.fixture.run("task-split", "--input", reviewed_input(gate, data)[0]) == 0
    data = planned_input(gate)
    data["id"] = "later-plan"
    data["changes"] = [
        {"id": "S02", "before": {"depends_on": ["S03"]}, "after": {"depends_on": ["S04"]}}
    ]
    assert gate.fixture.run("task-plan", "--input", reviewed_input(gate, data)[0]) == 0
    # Then replay accepts the ordered chain and immutable patch tampering is rejected.
    assert gate.fixture.run("check") == 0
    state = gate.program.load()
    state["amendments"][1]["decomposition"]["dependency_changes"][0]["before"] = []
    with pytest.raises(fp.planning.PlanningError, match="history/receipt"):
        fp.planning.validate_mutations(state)


def test_nested_split_retains_reciprocal_ancestors_and_allows_later_admission(
    gate: GateFixture,
) -> None:
    # Given an actual committed split and its independently reviewed child lineage.
    gate.ready()
    assert (
        gate.fixture.run(
            "task-split", "--input", reviewed_input(gate, decomposition_input(gate))[0]
        )
        == 0
    )
    prior = gate.program.load()
    original_intake = deepcopy(prior["source_intakes"])
    data = decomposition_input(gate, predecessor="S03", children=("S05", "S06"))
    # When a child is split and further unrelated reviewed work is admitted.
    assert gate.fixture.run("task-split", "--input", reviewed_input(gate, data)[0]) == 0
    current = gate.program.load()
    assert current["source_intakes"][:1] == original_intake
    assert gate.program.task(current, "S03")["lineage"] == {
        "predecessor": "S01",
        "successors": ["S05", "S06"],
    }
    assert gate.program.task(current, "S01")["lineage"]["successors"] == ["S03", "S04"]
    additional = proposal(gate)
    plan = fp.record(additional["planning"], "plan")
    path = fp.safe_path(gate.program.repo, str(plan["path"]))
    metadata = fp.read_json(path)
    metadata["tasks"][0].update({"id": "S07", "candidate_paths": ["src/S07.py"]})
    path.write_bytes(fp.canonical(metadata))
    plan["sha256"] = fp.sha(path.read_bytes())
    source = fp.record(additional["source"], "source")
    path = fp.safe_path(gate.program.repo, str(source["path"]))
    path.write_text("### S07. Later unit\n\nAcceptance: later unit.\n", encoding="utf-8")
    source.update({"task_ids": ["S07"], "sha256": fp.sha(path.read_bytes())})
    members = list(current["objective"]["task_ids"])
    additional["membership"] = {
        "before": members,
        "after": members + ["S07"],
        "added": ["S07"],
        "removed": [],
    }
    assert gate.fixture.run("task-admit", "--input", reviewed_input(gate, additional)[0]) == 0
    # Then the disposed first retained unit does not poison baseline acceptance or fresh claims.
    assert gate.fixture.claim("S05") == 0
    assert gate.fixture.run("check") == 0


def test_split_preserves_authored_category_and_places_children_explicitly(
    gate: GateFixture,
) -> None:
    # Given an authored existing leaf containing the predecessor and peer.
    state = gate.program.load()
    state["categories"] = [
        {
            "id": "leaf",
            "parent_id": "delivery",
            "title": "Existing leaf",
            "task_ids": ["S01", "S02"],
        }
    ]
    gate.program.save(state)
    gate.ready()
    data = decomposition_input(gate)
    data["category_placements"] = [{"id": key, "category_id": "leaf"} for key in ("S03", "S04")]
    # When the reviewed split declares exact placements using the existing validator.
    assert gate.fixture.run("task-split", "--input", reviewed_input(gate, data)[0]) == 0
    # Then category identity and historical predecessor membership are retained.
    current = gate.program.load()
    assert current["categories"] == [
        {
            "id": "leaf",
            "parent_id": "delivery",
            "title": "Existing leaf",
            "task_ids": ["S01", "S02", "S03", "S04"],
        }
    ]
    assert gate.fixture.run("check") == 0


@pytest.mark.parametrize("operation", ["task-plan", "task-prioritize", "objective-membership"])
def test_disposed_work_rejects_later_planning_and_membership_restore(
    gate: GateFixture, operation: str
) -> None:
    gate.ready()
    assert (
        gate.fixture.run(
            "task-retire",
            "--input",
            reviewed_input(gate, decomposition_input(gate, "task-retire"))[0],
        )
        == 0
    )
    state = gate.program.load()
    data = planned_input(gate, operation)
    if operation == "task-prioritize":
        data["changes"] = [{"id": "S01", "before": None, "after": -1}]
    elif operation == "objective-membership":
        data.pop("changes")
        data["membership"] = {
            "before": ["S02"],
            "after": ["S02", "S01"],
            "added": ["S01"],
            "removed": [],
        }
    before = gate.program.path("backlog.json").read_bytes()
    with pytest.raises((fp.planning.PlanningError, fp.coordination.GovernanceError)):
        fp.planning.construct(gate.program, state, data)
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "fault", ["section-bool", "section-float", "patch", "lineage", "effective"]
)
def test_split_history_rejects_typed_binding_and_lineage_drift(
    gate: GateFixture, fault: str
) -> None:
    gate.ready()
    data = decomposition_input(gate)
    if fault.startswith("section"):
        binding = fp.planning.predecessor_binding(gate.program, gate.program.load(), "S01")
        binding["section"]["line"] = True if fault == "section-bool" else 1.0
        data["predecessor_before"] = binding
        with pytest.raises(fp.planning.PlanningError, match="Stale predecessor"):
            fp.planning.construct(gate.program, gate.program.load(), data)
        return
    future = fp.planning.construct(gate.program, gate.program.load(), data).state
    detail = future["amendments"][-1]["decomposition"]
    if fault == "patch":
        detail["dependency_changes"] = [
            {"id": "S02", "before": [], "after": ["S03"], "reason": "Tampered"}
        ]
    elif fault == "lineage":
        detail["after"][0]["lineage"]["successors"] = ["S03"]
    else:
        gate.program.task(future, "S03")["lineage"]["predecessor"] = "S02"
    with pytest.raises(fp.planning.PlanningError):
        fp.planning.validate_mutations(future, prospective=True)


@pytest.mark.parametrize("stage", ["atomic", "render", "precommit", "publication", "corrupt"])
def test_decomposition_publication_boundaries(
    gate: GateFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    # Given a completely independently reviewed split receipt.
    gate.ready()
    name, _ = reviewed_input(gate, decomposition_input(gate))
    before = gate.program.path("backlog.json").read_bytes()
    original_write, original_views = fp.atomic_write, fp.Program.views

    def failing_write(path: Path, content: bytes) -> None:
        if path == gate.program.path("backlog.json"):
            raise OSError("Injected split atomic failure")
        original_write(path, content)

    def failing_render(program: fp.Program, state: fp.Record) -> None:
        raise OSError("Injected split render failure")

    def changing_views(program: fp.Program, state: fp.Record) -> dict[str, str]:
        views = original_views(program, state)
        path = (
            program.path(name)
            if stage == "precommit"
            else fp.planning.PublicationStore(program.repo).path("ledger.json")
        )
        path.write_text("{}", encoding="utf-8")
        return views

    if stage == "publication":
        with gate.program.lock(publication_recovery=True):
            fp.planning.publication_register(
                gate.program,
                "relocation",
                {"program_id": "discovery", "destination": "pending", "reason": "Pending"},
                fp.Program(gate.program.repo, gate.program.repo / "pending"),
            )
    capsys.readouterr()
    # When publication fails at a known boundary.
    with monkeypatch.context() as scoped:
        if stage == "atomic":
            scoped.setattr(fp, "atomic_write", failing_write)
        elif stage == "render":
            scoped.setattr(fp.Program, "render", failing_render)
        elif stage in {"precommit", "corrupt"}:
            scoped.setattr(fp.Program, "views", changing_views)
        assert gate.fixture.run("task-split", "--input", name) == 1
    # Then precommit is unchanged; committed render failure has an actionable revision.
    if stage == "render":
        assert "Registry committed at revision" in capsys.readouterr().err
        assert gate.program.path("backlog.json").read_bytes() != before
        assert gate.fixture.run("task-split", "--input", name) == 1
        assert gate.fixture.run("render") == 0
        assert gate.fixture.run("check") == 0
    else:
        assert gate.program.path("backlog.json").read_bytes() == before


def test_standalone_planned_cli_preserves_source_history_and_acceptance(gate: GateFixture) -> None:
    # Given released history and an accepted contract.
    gate.ready()
    assert gate.fixture.claim() == 0
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Preserve history",
            "--next-action",
            "Revalidate",
        )
        == 0
    )
    before = gate.program.load()
    files = {
        path: path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file() and ("evidence" in path.parts or path.name in {"source.md", "plan.json"})
    }
    data = planned_input(gate)
    expected_changes = deepcopy(data["changes"])
    name = runtime_input(gate, data)
    command = [
        sys.executable,
        "-B",
        fp.__file__,
        "--repo",
        str(gate.program.repo),
        "--program-dir",
        "discovery",
        "task-plan",
        "--input",
        name,
    ]
    # When the actual standalone command publishes and the same receipt is retried.
    success = subprocess.run(command, capture_output=True, text=True, check=False)
    assert success.returncode == 0, success.stderr
    current_bytes = gate.program.path("backlog.json").read_bytes()
    retry = subprocess.run(command, capture_output=True, text=True, check=False)
    # Then one immutable patch is committed, accepted phases/membership/history remain exact.
    assert retry.returncode == 1 and "already committed" in retry.stderr
    assert gate.program.path("backlog.json").read_bytes() == current_bytes
    current = gate.program.load()
    assert current["objective"] == before["objective"]
    assert current["phases"] == before["phases"]
    assert (
        gate.program.task(current, "S01")["attempts"]
        == gate.program.task(before, "S01")["attempts"]
    )
    assert all(path.read_bytes() == raw for path, raw in files.items())
    assert current["amendments"][-1]["changes"] == expected_changes
    assert gate.fixture.claim(number=2) == 0
    assert gate.fixture.run("check") == 0


def test_reviewed_planned_batch_renews_exact_scope_without_membership(gate: GateFixture) -> None:
    # Given two explicit consequential metadata patches.
    gate.ready()
    before = gate.program.load()
    data = planned_input(gate)
    data["changes"] = [
        {
            "id": "S01",
            "before": {"candidate_paths": ["src/S01.py"], "planning_note": "Synthetic only"},
            "after": {
                "candidate_paths": ["src/S01.py", "src/new.py"],
                "planning_note": "Inspect expanded paths",
            },
        },
        {
            "id": "S02",
            "before": {"depends_on": [], "phase": 1},
            "after": {"depends_on": ["S01"], "phase": 2},
        },
    ]
    name, _ = reviewed_input(gate, data)
    # When the independently reviewed future contract commits.
    assert gate.fixture.run("task-plan", "--input", name) == 0
    # Then scope is current, old phase history/member/source records are unchanged.
    current = gate.program.load()
    assert current["objective"] == before["objective"]
    assert "source_intakes" not in current
    assert (
        fp.record(current["phases"], "phases")["history"][:-3]
        == fp.record(before["phases"], "phases")["history"]
    )
    assert fp.phases.planning_binding(current) == data["after_binding"]
    assert gate.program.status(current, True)["candidates"][0]["id"] == "S01"
    assert gate.fixture.run("check") == 0


@pytest.mark.parametrize("value", [True, False, 1.5, -(2**31) - 1, 2**31, "1"])
def test_invalid_priority_rejects_without_registry_change(
    gate: GateFixture, value: fp.Json
) -> None:
    gate.ready()
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S02", "before": None, "after": value}]
    name = gate.fixture.write("evidence/_planning/attempt-1/invalid-priority.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-prioritize", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("value", [-(2**31), 0, 2**31 - 1, None])
def test_priority_boundaries_and_claimed_history_remain_valid(
    gate: GateFixture, value: fp.Json
) -> None:
    gate.ready()
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S02", "before": None, "after": value}]
    name = runtime_input(gate, data)
    assert gate.fixture.run("task-prioritize", "--input", name) == 0
    state = gate.program.load()
    assert fp.record(state["execution_priorities"], "priorities").get("S02") == value
    assert gate.fixture.claim("S02") == 0
    assert gate.fixture.run("check") == 0


def test_priority_removal_keeps_activation_and_unspecified_distinct_from_zero(
    gate: GateFixture,
) -> None:
    gate.ready()
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S02", "before": None, "after": 0}]
    assert gate.fixture.run("task-prioritize", "--input", runtime_input(gate, data)) == 0
    explicit = gate.program.load()
    data = planned_input(gate, "task-prioritize")
    data["id"] = "priority-remove"
    data["changes"] = [{"id": "S02", "before": 0, "after": None}]
    assert gate.fixture.run("task-prioritize", "--input", runtime_input(gate, data)) == 0
    current = gate.program.load()
    assert explicit["execution_priorities"] == {"S02": 0}
    assert current["execution_priorities"] == {}
    assert current["amendments"][-1]["priority_before"] == {"S02": 0}
    assert current["amendments"][-1]["priority_after"] == {}
    assert gate.fixture.run("check") == 0


@pytest.mark.parametrize(
    "fault",
    [
        "revision",
        "actor",
        "objective",
        "unknown",
        "before",
        "field",
        "path",
        "private",
        "cycle",
        "related",
        "check",
        "phase",
        "duplicate",
        "membership",
        "workstream",
    ],
)
def test_invalid_planned_amendment_preserves_registry(gate: GateFixture, fault: str) -> None:
    gate.ready()
    data = planned_input(gate)
    change = fp.record(fp.sequence(data["changes"], "changes")[0], "change")
    if fault == "revision":
        data["expected_revision"] = "0" * 64
    elif fault == "actor":
        data["actor_id"] = "reviewer"
    elif fault == "objective":
        data["objective_id"] = "old"
    elif fault == "unknown":
        change["id"] = "unknown"
    elif fault == "before":
        change["before"] = {"next_action": "outdated"}
    elif fault == "field":
        change.update({"before": {"status": "planned"}, "after": {"status": "done"}})
    elif fault in {"path", "private"}:
        change.update(
            {
                "before": {"candidate_paths": ["src/S01.py"]},
                "after": {
                    "candidate_paths": ["../outside.py" if fault == "path" else ".work/private.py"]
                },
            }
        )
    elif fault == "cycle":
        data["changes"] = [
            {"id": "S01", "before": {"depends_on": []}, "after": {"depends_on": ["S02"]}},
            {"id": "S02", "before": {"depends_on": []}, "after": {"depends_on": ["S01"]}},
        ]
    elif fault == "related":
        change.update({"before": {"related_to": []}, "after": {"related_to": ["unknown"]}})
    elif fault == "check":
        change.update(
            {
                "before": {"required_checks": ["acceptance", "scoped_tests", "format_lint"]},
                "after": {"required_checks": ["unknown"]},
            }
        )
    elif fault == "phase":
        change.update({"before": {"phase": 1}, "after": {"phase": True}})
    elif fault == "duplicate":
        data["changes"] = [change, deepcopy(change)]
    elif fault == "membership":
        data["membership"] = {"after": ["S01"]}
    else:
        change.update({"before": {"workstream": "delivery"}, "after": {"workstream": "unknown"}})
    name = gate.fixture.write("evidence/_planning/attempt-1/rejected-plan.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-plan", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("operation", ["task-plan", "task-prioritize"])
@pytest.mark.parametrize("status", ["active", "review", "blocked", "done", "excluded", "disposed"])
def test_ineligible_planning_targets_reject_in_pure_proposal(
    gate: GateFixture, operation: str, status: str
) -> None:
    gate.ready()
    data = planned_input(gate, operation)
    if operation == "task-prioritize":
        data["changes"] = [{"id": "S01", "before": None, "after": -1}]
    state = gate.program.load()
    task = gate.program.task(state, "S01")
    if status == "excluded":
        fp.record(state["objective"], "objective")["task_ids"] = ["S02"]
    elif status == "disposed":
        task["disposition"] = "retired"
    else:
        task["status"] = status
        task["owner"] = "worker" if status in {"active", "review", "blocked"} else None
    data["expected_revision"] = fp.phases.revision(state)
    with pytest.raises((fp.planning.PlanningError, fp.phases.PhaseError)):
        fp.planning.construct(gate.program, state, data)


def test_priority_remains_valid_through_real_completion(gate: GateFixture) -> None:
    gate.ready()
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S01", "before": None, "after": -4}]
    assert gate.fixture.run("task-prioritize", "--input", runtime_input(gate, data)) == 0
    assert gate.fixture.claim() == 0
    assert gate.fixture.submit()
    review = gate.fixture.review()
    assert gate.fixture.run("complete", "S01", "--review", review) == 0
    assert gate.program.load()["execution_priorities"] == {"S01": -4}
    assert gate.fixture.run("check") == 0


def test_priority_cannot_bypass_dependency_readiness(gate: GateFixture) -> None:
    state = gate.program.load()
    gate.program.task(state, "S02")["depends_on"] = ["S01"]
    gate.program.save(state)
    gate.ready()
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S02", "before": None, "after": -(2**31)}]
    assert gate.fixture.run("task-prioritize", "--input", runtime_input(gate, data)) == 0
    current = gate.program.load()
    assert current["amendments"][-1]["order_after"] == ["S02", "S01"]
    assert current["amendments"][-1]["ready_before"] == ["S01"]
    assert current["amendments"][-1]["ready_after"] == ["S01"]
    assert [
        task["id"] for task in gate.program.status(gate.program.load(), True)["candidates"]
    ] == ["S01"]
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.claim("S02") == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_planned_scope_collision_with_owned_peer_rejects(gate: GateFixture) -> None:
    gate.ready()
    assert gate.fixture.claim("S02") == 0
    data = planned_input(gate)
    data["changes"] = [
        {
            "id": "S01",
            "before": {"candidate_paths": ["src/S01.py"]},
            "after": {"candidate_paths": ["src/S02.py"]},
        }
    ]
    before = gate.program.path("backlog.json").read_bytes()
    name = gate.fixture.write("evidence/_planning/attempt-1/colliding.json", data)
    assert gate.fixture.run("task-plan", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_planned_workstream_cannot_rewrite_existing_category(gate: GateFixture) -> None:
    state = gate.program.load()
    fp.sequence(state["workstreams"], "streams").append(
        {"id": "other", "name": "Other existing root"}
    )
    state["categories"] = [
        {"id": "leaf", "parent_id": "delivery", "title": "Authored leaf", "task_ids": ["S01"]}
    ]
    gate.program.save(state)
    gate.ready()
    data = planned_input(gate)
    data["changes"] = [
        {"id": "S01", "before": {"workstream": "delivery"}, "after": {"workstream": "other"}}
    ]
    before = gate.program.path("backlog.json").read_bytes()
    with pytest.raises(fp.coordination.GovernanceError, match="Category/workstream mismatch"):
        fp.planning.construct(gate.program, gate.program.load(), data)
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["metadata", "priority", "history"])
def test_effective_planning_drift_is_detected(gate: GateFixture, fault: str) -> None:
    gate.ready()
    data = planned_input(gate, "task-prioritize" if fault == "priority" else "task-plan")
    if fault == "priority":
        data["changes"] = [{"id": "S02", "before": None, "after": -1}]
    else:
        data["changes"] = [
            {
                "id": "S01",
                "before": {"planning_note": "Synthetic only"},
                "after": {"planning_note": "Explicit updated note"},
            }
        ]
    name = runtime_input(gate, data) if fault == "priority" else reviewed_input(gate, data)[0]
    assert gate.fixture.run(str(data["operation"]), "--input", name) == 0
    state = gate.program.load()
    if fault == "metadata":
        gate.program.task(state, "S01")["planning_note"] = "Unrecorded edit"
    elif fault == "priority":
        state["execution_priorities"] = {"S02": -2}
    else:
        state["amendments"][-1]["changes"][0]["after"] = {"planning_note": "Altered history"}
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    assert gate.fixture.run("check") == 1


@pytest.mark.parametrize("stage", ["atomic", "render", "precommit", "publication", "corrupt"])
def test_planned_publication_failures_preserve_or_report_committed_state(
    gate: GateFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    gate.ready()
    name = runtime_input(gate, planned_input(gate))
    before = gate.program.path("backlog.json").read_bytes()
    original_write = fp.atomic_write
    original_views = fp.Program.views

    def fail_write(path: Path, content: bytes) -> None:
        if path == gate.program.path("backlog.json"):
            raise OSError("Injected planned atomic failure")
        original_write(path, content)

    def fail_render(program: fp.Program, state: fp.Record) -> None:
        raise OSError("Injected planned render failure")

    def changing_views(program: fp.Program, state: fp.Record) -> dict[str, str]:
        views = original_views(program, state)
        if stage == "precommit":
            program.path(name).write_text("{}", encoding="utf-8")
        else:
            fp.planning.PublicationStore(program.repo).path("ledger.json").write_text(
                "{}", encoding="utf-8"
            )
        return views

    if stage == "publication":
        marker: fp.Record = {
            "program_id": "discovery",
            "destination": "pending",
            "reason": "Synthetic pending copy",
        }
        with gate.program.lock(publication_recovery=True):
            fp.planning.publication_register(
                gate.program,
                "relocation",
                marker,
                fp.Program(gate.program.repo, gate.program.repo / "pending"),
            )
    capsys.readouterr()
    with monkeypatch.context() as scoped:
        if stage == "atomic":
            scoped.setattr(fp, "atomic_write", fail_write)
        elif stage == "render":
            scoped.setattr(fp.Program, "render", fail_render)
        elif stage in {"precommit", "corrupt"}:
            scoped.setattr(fp.Program, "views", changing_views)
        assert gate.fixture.run("task-plan", "--input", name) == 1
    if stage == "render":
        assert "Registry committed at revision" in capsys.readouterr().err
        assert gate.program.path("backlog.json").read_bytes() != before
        assert gate.fixture.run("task-plan", "--input", name) == 1
        assert gate.fixture.run("render") == 0
        assert gate.fixture.run("check") == 0
    else:
        assert gate.program.path("backlog.json").read_bytes() == before


def test_planned_metadata_requires_independent_renewal(gate: GateFixture) -> None:
    gate.ready()
    data = planned_input(gate)
    data["changes"] = [
        {
            "id": "S01",
            "before": {"planning_note": "Synthetic only"},
            "after": {"planning_note": "New stable definition"},
        }
    ]
    result = fp.planning.preview(gate.program, gate.program.load(), data)
    assert result["renewal_required"] is True
    name = runtime_input(gate, data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-plan", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_priority_of_conflicting_planned_task_does_not_grant_readiness(gate: GateFixture) -> None:
    state = gate.program.load()
    for value in state["tasks"]:
        fp.record(value, "task")["conflict_groups"] = ["shared"]
    gate.program.save(state)
    gate.ready()
    assert gate.fixture.claim() == 0
    data = planned_input(gate, "task-prioritize")
    data["changes"] = [{"id": "S02", "before": None, "after": -20}]
    assert gate.fixture.run("task-prioritize", "--input", runtime_input(gate, data)) == 0
    assert gate.program.status(gate.program.load(), True)["candidates"] == []
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.claim("S02", owner="other-worker") == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_admitted_task_planning_preserves_supplemental_provenance(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    before = gate.program.load()
    source = gate.program.path("source/admissions/intake/report.md").read_bytes()
    plan = gate.program.path("source/admissions/intake/plan.json").read_bytes()
    data = planned_input(gate)
    data["id"] = "admitted-plan"
    data["changes"] = [
        {
            "id": "S03",
            "before": {"next_action": "Revalidate new implementation"},
            "after": {"next_action": "Inspect admitted source"},
        }
    ]
    assert gate.fixture.run("task-plan", "--input", runtime_input(gate, data)) == 0
    current = gate.program.load()
    assert current["source_intakes"] == before["source_intakes"]
    assert current["objective"] == before["objective"]
    assert gate.program.task(current, "S03")["source"] == gate.program.task(before, "S03")["source"]
    assert gate.program.path("source/admissions/intake/report.md").read_bytes() == source
    assert gate.program.path("source/admissions/intake/plan.json").read_bytes() == plan
    assert gate.fixture.run("check") == 0


def test_planned_scope_rejects_self_review_without_commit(gate: GateFixture) -> None:
    gate.ready()
    data = planned_input(gate)
    data["changes"] = [
        {
            "id": "S01",
            "before": {"planning_note": "Synthetic only"},
            "after": {"planning_note": "Reviewed future definition"},
        }
    ]
    name, data = reviewed_input(gate, data)
    review_name = data["renewal"]["review"]
    review = fp.read_json(gate.program.path(review_name))
    review["actor_id"] = "director"
    gate.program.path(review_name).write_bytes(fp.canonical(review))
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-plan", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_reviewed_admission_preserves_original_records_and_allows_fresh_claim(
    gate: GateFixture,
) -> None:
    # Given real prior attempt history and frozen phase evidence in a temporary program.
    gate.ready()
    assert gate.fixture.claim() == 0
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Keep prior attempt",
            "--next-action",
            "Revalidate",
        )
        == 0
    )
    before = gate.program.load()
    preserved = {
        path: path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file()
        and (path.parts[-1] in {"source.md", "plan.json"} or "evidence" in path.parts)
    }
    name, _ = reviewed_input(gate)
    # When a complete reviewed future contract commits.
    assert gate.fixture.run("task-admit", "--input", name) == 0
    # Then old source/attempt/event/evidence bytes remain intact and new work starts fresh.
    current = gate.program.load()
    assert current["tasks"][:2] == before["tasks"]
    assert current["events"][:-1] == before["events"]
    assert (
        fp.record(current["phases"], "phases")["history"][:-3]
        == fp.record(before["phases"], "phases")["history"]
    )
    assert all(path.read_bytes() == raw for path, raw in preserved.items())
    task = gate.program.task(current, "S03")
    assert (task["status"], task["owner"], task["attempts"]) == ("planned", None, [])
    objective = fp.record(current["objective"], "objective")
    assert objective["membership_revision"] == 1
    assert objective["task_ids"] == ["S01", "S02", "S03"]
    assert objective["criteria"] == fp.record(before["objective"], "objective")["criteria"]
    assert gate.fixture.claim("S03") == 0
    assert gate.fixture.run("check") == 0


def test_standalone_cli_admission_and_duplicate_retry(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    command = [
        sys.executable,
        "-B",
        fp.__file__,
        "--repo",
        str(gate.program.repo),
        "--program-dir",
        "discovery",
        "task-admit",
        "--input",
        name,
    ]
    success = subprocess.run(command, capture_output=True, text=True, check=False)
    assert success.returncode == 0, success.stderr
    before = gate.program.path("backlog.json").read_bytes()
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode == 1 and "already committed" in rejected.stderr
    assert "Traceback" not in rejected.stderr
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "revision",
        "director",
        "objective",
        "before",
        "after",
        "reviewer",
        "units",
        "candidate",
        "planning",
        "source",
        "evidence",
        "authors",
    ],
)
def test_review_or_binding_failure_leaves_no_partial_admission(
    gate: GateFixture,
    fault: Literal[
        "revision",
        "director",
        "objective",
        "before",
        "after",
        "reviewer",
        "units",
        "candidate",
        "planning",
        "source",
        "evidence",
        "authors",
    ],
) -> None:
    gate.ready()
    name, data = reviewed_input(gate)
    renewal = fp.record(data["renewal"], "renewal")
    match fault:
        case "revision":
            data["expected_revision"] = "0" * 64
        case "director":
            data["actor_id"] = "reviewer"
        case "objective":
            data["objective_id"] = "past-goal"
        case "before":
            data["before_binding"] = {}
        case "after":
            data["after_binding"] = {}
        case "reviewer" | "units" | "authors":
            key = "review" if fault == "reviewer" else "inventory"
            receipt = fp.read_json(gate.program.path(str(renewal[key])))
            if fault == "reviewer":
                receipt["actor_id"] = "author"
            elif fault == "units":
                receipt["units"] = receipt["units"][:-1]
            else:
                receipt["inventory_authors"] = ["director"]
            gate.fixture.write(str(renewal[key]), receipt)
        case "candidate":
            gate.program.path("evidence/_phases/attempt-1/intake-candidate.md").write_text(
                "Changed candidate", encoding="utf-8"
            )
        case "planning":
            gate.program.path("source/admissions/intake/plan.json").write_text(
                "{}", encoding="utf-8"
            )
        case "source":
            gate.program.path("source/admissions/intake/report.md").write_text(
                "Changed source", encoding="utf-8"
            )
        case "evidence":
            data["evidence"] = []
        case unreachable:
            assert_never(unreachable)
    gate.fixture.write(name, data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "case",
        "duplicate",
        "runtime",
        "paths",
        "checks",
        "stream",
        "dependency",
        "cycle",
        "plan-only",
        "report-only",
    ],
)
def test_invalid_intake_definitions_reject_before_publication(
    gate: GateFixture, fault: str
) -> None:
    gate.ready()
    data = proposal(gate)
    plan_path = gate.program.path("source/admissions/intake/plan.json")
    plan = fp.read_json(plan_path)
    task = fp.record(plan["tasks"][0], "new task")
    if fault in {"case", "duplicate"}:
        task["id"] = "s01" if fault == "case" else "S01"
        data["source"]["task_ids"] = [task["id"]]
        report_path = gate.program.path("source/admissions/intake/report.md")
        report_path.write_text(
            f"### {task['id']}. Duplicate\nAcceptance: reject duplicates.\n", encoding="utf-8"
        )
        data["source"]["sha256"] = fp.sha(report_path.read_bytes())
    elif fault == "runtime":
        task["attempts"] = []
    elif fault == "paths":
        task["candidate_paths"] = []
    elif fault == "checks":
        task["required_checks"] = []
    elif fault == "stream":
        task["workstream"] = "unknown"
    elif fault == "dependency":
        task["depends_on"] = ["UNKNOWN"]
    elif fault == "cycle":
        task["depends_on"] = ["S03"]
    elif fault == "plan-only":
        task["id"] = "S04"
    else:
        path = gate.program.path("source/admissions/intake/report.md")
        path.write_bytes(
            path.read_bytes() + b"\n### S04. Extra\nAcceptance: reject unspecified work.\n"
        )
        data["source"]["sha256"] = fp.sha(path.read_bytes())
    plan_path.write_bytes(fp.canonical(plan))
    data["planning"]["sha256"] = fp.sha(plan_path.read_bytes())
    before = gate.program.path("backlog.json").read_bytes()
    name = gate.fixture.write("evidence/_planning/attempt-1/invalid.json", data)
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def membership(gate: GateFixture, after: list[str], label: str = "membership") -> fp.Record:
    state = gate.program.load()
    before = fp.strings(fp.record(state["objective"], "objective")["task_ids"], "members")
    return {
        "schema_version": 1,
        "operation": "objective-membership",
        "id": label,
        "program_id": state["program_id"],
        "actor_id": "director",
        "objective_id": "goal",
        "expected_revision": fp.phases.revision(state),
        "reason": "Explicitly amend current membership",
        "membership": {
            "before": before,
            "after": after,
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
        },
    }


def test_membership_exclusion_preserves_history_and_blocks_new_execution(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate, membership(gate, ["S01"]))
    before = gate.program.load()
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    state = gate.program.load()
    assert state["tasks"] == before["tasks"]
    assert fp.record(state["objective"], "objective")["history"] == []
    assert gate.fixture.claim("S02") == 1
    assert gate.fixture.claim() == 0


def test_remove_readd_has_monotonic_binding_and_retains_prior_history(gate: GateFixture) -> None:
    gate.ready()
    original = fp.phases.planning_binding(gate.program.load())
    name, _ = reviewed_input(gate, membership(gate, ["S01"], "exclude"))
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    name, _ = reviewed_input(gate, membership(gate, ["S01", "S02"], "readd"))
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    state = gate.program.load()
    assert fp.record(state["objective"], "objective")["membership_revision"] == 2
    assert fp.phases.planning_binding(state)["objective_digest"] != original["objective_digest"]
    assert gate.fixture.claim("S02") == 0


@pytest.mark.parametrize("fault", ["owned", "dependency", "empty", "unknown"])
def test_unsafe_membership_amendment_preserves_registry(gate: GateFixture, fault: str) -> None:
    gate.ready()
    if fault == "owned":
        assert gate.fixture.claim("S02") == 0
    if fault == "dependency":
        state = gate.program.load()
        gate.program.task(state, "S01")["depends_on"] = ["S02"]
        # Establish a genuinely renewed accepted baseline for the dependency.
        gate.program.save(state)
        assert gate.freeze("dependency-freeze") == 0
        assert gate.review(label="dependency-review") == 0
        assert gate.accept(label="dependency-accept") == 0
    after = [] if fault == "empty" else (["UNKNOWN"] if fault == "unknown" else ["S01"])
    data = membership(gate, after)
    name = gate.fixture.write("evidence/_planning/attempt-1/unsafe.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("objective-membership", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "marker_name", ["SUCCESSION_INCOMPLETE.json", "RELOCATION_INCOMPLETE.json"]
)
@pytest.mark.parametrize("related", [True, False])
def test_remote_publication_marker_blocks_only_related_amendment(
    gate: GateFixture, marker_name: str, related: bool
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    path = gate.program.repo / "other-endpoint" / marker_name
    path.parent.mkdir()
    fields = (
        ("predecessor", "successor")
        if marker_name == "SUCCESSION_INCOMPLETE.json"
        else ("source", "destination")
    )
    path.write_bytes(
        fp.canonical(
            {fields[0]: "other-endpoint", fields[1]: "discovery" if related else "unrelated"}
        )
    )
    source = fp.Program(gate.program.repo, path.parent)
    destination = (
        gate.program if related else fp.Program(gate.program.repo, gate.program.repo / "unrelated")
    )
    marker = {
        "predecessor_id": "other",
        "successor_id": "discovery" if related else "unrelated",
        "program_id": "other",
    }
    with source.lock(publication_recovery=True):
        fp.planning.publication_register(
            source,
            "succession" if marker_name == "SUCCESSION_INCOMPLETE.json" else "relocation",
            marker,
            destination,
            legacy_observed=True,
        )
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-admit", "--input", name) == (1 if related else 0)
    if related:
        assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("stage", ["views", "atomic", "render"])
def test_admission_failures_distinguish_precommit_and_committed_recovery(
    gate: GateFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    before = gate.program.path("backlog.json").read_bytes()
    original = fp.atomic_write

    def fail_write(path: Path, content: bytes) -> None:
        if path.name == "backlog.json":
            raise OSError("Injected canonical publication failure")
        original(path, content)

    def fail_views(_program: fp.Program, _state: fp.Record) -> dict[str, str]:
        raise OSError("Injected view preflight failure")

    def fail_render(_program: fp.Program, _state: fp.Record) -> None:
        raise OSError("Injected postcommit render failure")

    capsys.readouterr()
    with monkeypatch.context() as scoped:
        if stage == "views":
            scoped.setattr(fp.Program, "views", fail_views)
        elif stage == "atomic":
            scoped.setattr(fp, "atomic_write", fail_write)
        else:
            scoped.setattr(fp.Program, "render", fail_render)
        assert gate.fixture.run("task-admit", "--input", name) == 1
    if stage == "render":
        assert "Registry committed at revision" in capsys.readouterr().err
        assert gate.program.task(gate.program.load(), "S03")["status"] == "planned"
        assert gate.fixture.run("task-admit", "--input", name) == 1
        assert gate.fixture.run("render") == 0
        assert gate.fixture.run("check") == 0
    else:
        assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("drift", ["report", "plan", "missing"])
def test_supplemental_snapshot_observes_drift_without_mutation(
    gate: GateFixture, drift: str
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    target = gate.program.path(
        "source/admissions/intake/plan.json"
        if drift == "plan"
        else "source/admissions/intake/report.md"
    )
    if drift == "missing":
        target.unlink()
    else:
        target.write_text("changed", encoding="utf-8")
    before = gate.program.path("backlog.json").read_bytes()
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert snapshot["integrity"]["status"] == "stale"
    assert snapshot["tasks"][2]["source"]["path"] == "discovery/source/admissions/intake/report.md"
    assert gate.program.path("backlog.json").read_bytes() == before
    assert gate.fixture.run("check") == 1


def test_admitted_provenance_relocation_keeps_binding_and_receipt_bytes(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    state = gate.program.load()
    before = fp.phases.planning_binding(state)
    receipt_bytes = gate.program.path(name).read_bytes()
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            "moved",
            "--owner",
            "director",
            "--reason",
            "Verify additive provenance movement",
        )
        == 0
    )
    moved = fp.Program(gate.program.repo, gate.program.repo / "moved")
    current = moved.load()
    assert fp.phases.planning_binding(current) == before
    assert moved.path(name).read_bytes() == receipt_bytes
    assert (
        fp.planning.source_for_task(current, moved.task(current, "S03"))["path"]
        == "moved/source/admissions/intake/report.md"
    )
    moved.check(current)
    fp.planning.require_completed_publication(moved, current)


def test_legacy_amendment_requires_prior_explicit_adoption(gate: GateFixture) -> None:
    data = proposal(gate)
    name = gate.fixture.write("evidence/_planning/attempt-1/unadopted.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before
    assert gate.fixture.claim() == 0  # Untouched ordinary legacy behavior remains available.


@pytest.mark.parametrize("stage", ["early", "cleanup"])
def test_incomplete_succession_blocks_definition_change_until_recovered(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    gate.ready()
    predecessor = create_program(gate.program.repo, "predecessor")
    original_write, original_unlink = fp.atomic_write, Path.unlink

    def fail_write(path: Path, content: bytes) -> None:
        if stage == "early" and path == gate.program.path("SUCCESSION_INCOMPLETE.json"):
            raise OSError("Injected early successor publication failure")
        original_write(path, content)

    def fail_unlink(path: Path, *args, **kwargs) -> None:
        if stage == "cleanup" and path == predecessor.program.path("SUCCESSION_INCOMPLETE.json"):
            raise OSError("Injected final predecessor cleanup failure")
        original_unlink(path, *args, **kwargs)

    old = predecessor.program.path("backlog.json").read_bytes()
    args = [
        "program-succeed",
        "--successor",
        "discovery",
        "--successor-id",
        "discovery",
        "--expected-revision",
        fp.phases.revision(predecessor.program.load()),
        "--successor-revision",
        fp.phases.revision(gate.program.load()),
        "--owner",
        "leader",
        "--reason",
        "Synthetic reviewed successor",
    ]
    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", fail_write)
        scoped.setattr(Path, "unlink", fail_unlink)
        assert predecessor.run(*args) == 1
    assert gate.fixture.claim() == 0  # Existing PHASE ordinary progress remains enabled.
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Preserve progress",
            "--next-action",
            "Revalidate",
        )
        == 0
    )
    name, _ = reviewed_input(gate)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before
    assert (
        predecessor.run(
            "program-succeed-recover",
            "--owner",
            "leader",
            "--reason",
            "Explicitly finish verified publication",
        )
        == 0
    )
    assert predecessor.program.path("backlog.json").read_bytes() == old
    assert gate.fixture.run("task-admit", "--input", name) == 0


def test_completed_succession_allows_relocation_and_later_admission(gate: GateFixture) -> None:
    gate.ready()
    predecessor = create_program(gate.program.repo, "predecessor")
    args = [
        "program-succeed",
        "--successor",
        "discovery",
        "--successor-id",
        "discovery",
        "--expected-revision",
        fp.phases.revision(predecessor.program.load()),
        "--successor-revision",
        fp.phases.revision(gate.program.load()),
        "--owner",
        "leader",
        "--reason",
        "Synthetic reviewed successor",
    ]
    assert predecessor.run(*args) == 0
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            "moved",
            "--owner",
            "director",
            "--reason",
            "Move completed execution program",
        )
        == 0
    )
    moved = fp.Program(gate.program.repo, gate.program.repo / "moved")
    from tools.tests.test_feature_program import Fixture

    adopted = GateFixture(Fixture(moved, gate.fixture.report))
    name, _ = reviewed_input(adopted)
    assert adopted.fixture.run("task-admit", "--input", name) == 0
    assert adopted.fixture.run("check") == 0


@pytest.mark.parametrize("stage", ["copying", "ready"])
def test_outbound_incomplete_relocation_blocks_source_admission(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    # Given reviewed inputs prepared before a real interrupted outbound relocation.
    gate.ready()
    name, _ = reviewed_input(gate)
    before = gate.program.path("backlog.json").read_bytes()
    target = fp.Program(gate.program.repo, gate.program.repo / "moved")
    original_write = fp.atomic_write

    def interrupted(path: Path, content: bytes) -> None:
        failed_path = (
            target.path("source.md") if stage == "copying" else gate.program.path("RELOCATED.json")
        )
        if path == failed_path:
            raise OSError("Injected outbound relocation failure")
        original_write(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", interrupted)
        assert (
            gate.fixture.run(
                "relocate",
                "--destination",
                "moved",
                "--owner",
                "director",
                "--reason",
                "Move fixture",
            )
            == 1
        )
    assert fp.read_json(target.path("RELOCATION_INCOMPLETE.json"))["phase"] == stage
    # When admission is attempted at the still authoritative source endpoint.
    assert gate.fixture.run("task-admit", "--input", name) == 1
    # Then the opposite endpoint guard prevents any source registry replacement.
    assert gate.program.path("backlog.json").read_bytes() == before
    from tools.tests.test_feature_program import Fixture

    recovered = Fixture(target, gate.fixture.report).run(
        "relocate-recover", "--owner", "director", "--reason", "Inspect interrupted publication"
    )
    assert recovered == (0 if stage == "ready" else 1)
    assert gate.program.path("backlog.json").read_bytes() == before
    if stage == "ready":
        assert Fixture(target, gate.fixture.report).run("check") == 0
    else:
        assert gate.program.load()["tasks"]  # Copying is never falsely promoted.


def test_admission_shared_evidence_budget_rejects_without_registry_write(gate: GateFixture) -> None:
    # Given individually valid evidence documents whose aggregate exceeds the commit budget.
    gate.ready()
    name, data = reviewed_input(gate)
    evidence = []
    for index in range(8):
        path = f"evidence/_planning/attempt-1/large-{index}.txt"
        gate.program.path(path).write_bytes(b"x" * (8 * 1024 * 1024))
        evidence.append(path)
    data["evidence"] = evidence
    gate.fixture.write(name, data)
    before = gate.program.path("backlog.json").read_bytes()
    # When the final transaction verifies all evidence together.
    assert gate.fixture.run("task-admit", "--input", name) == 1
    # Then the aggregate read bound rejects before canonical publication.
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "path",
    [
        "Assets/Resources/GameData",
        "node_modules",
        ".git",
        ".agents",
        ".codex",
        ".aws",
        "Library",
        "generated",
    ],
)
def test_publication_discovery_does_not_read_excluded_trees(gate: GateFixture, path: str) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    marker = gate.program.repo / path / "SUCCESSION_INCOMPLETE.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("invalid and excluded; must never be read", encoding="utf-8")
    assert gate.fixture.run("task-admit", "--input", name) == 0


def test_unrelated_corrupt_marker_is_not_opened(
    gate: GateFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    path = gate.program.repo / "unrelated" / "RELOCATION_INCOMPLETE.json"
    path.parent.mkdir()
    path.write_text("broken JSON", encoding="utf-8")
    before = gate.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    assert gate.fixture.run("task-admit", "--input", name) == 0
    assert gate.program.path("backlog.json").read_bytes() != before


def test_publication_ledger_record_budget_rejects_without_registry_change(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    before = gate.program.path("backlog.json").read_bytes()

    store = fp.planning.PublicationStore(gate.program.repo)
    ledger = store.load()
    ledger["active"] = [{}] * 4097
    store.path("ledger.json").write_bytes(fp.canonical(ledger))
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("changed", ["input", "candidate", "source", "plan", "original-plan"])
def test_precommit_rehash_rejects_inputs_changed_during_preflight(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    target = {
        "input": name,
        "candidate": "evidence/_phases/attempt-1/intake-candidate.md",
        "source": "source/admissions/intake/report.md",
        "plan": "source/admissions/intake/plan.json",
        "original-plan": "plan.json",
    }[changed]
    before = gate.program.path("backlog.json").read_bytes()
    original = fp.Program.views

    def changing_views(program: fp.Program, state: fp.Record) -> dict[str, str]:
        result = original(program, state)
        gate.program.path(target).write_bytes(gate.program.path(target).read_bytes() + b" ")
        return result

    monkeypatch.setattr(fp.Program, "views", changing_views)
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_oversized_document_rejects_before_intake(gate: GateFixture) -> None:
    gate.ready()
    data = proposal(gate)
    path = gate.program.path("source/admissions/intake/report.md")
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    data["source"]["sha256"] = fp.sha(path.read_bytes())
    name = gate.fixture.write("evidence/_planning/attempt-1/oversized.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_task_count_limit_rejects_complete_but_oversized_intake(gate: GateFixture) -> None:
    gate.ready()
    data = proposal(gate)
    plan_path = gate.program.path("source/admissions/intake/plan.json")
    base = fp.read_json(plan_path)["tasks"][0]
    tasks = [
        {**deepcopy(base), "id": f"NEW-{index}", "candidate_paths": [f"src/new-{index}.py"]}
        for index in range(1999)
    ]
    plan_path.write_bytes(fp.canonical({"schema_version": 1, "tasks": tasks}))
    report = gate.program.path("source/admissions/intake/report.md")
    report.write_text(
        "\n".join(
            f"### {task['id']}. Added unit\nExpected result: retain unit.\nAcceptance: verify unit.\n"
            for task in tasks
        ),
        encoding="utf-8",
    )
    data["source"].update(
        {"task_ids": [task["id"] for task in tasks], "sha256": fp.sha(report.read_bytes())}
    )
    data["planning"]["sha256"] = fp.sha(plan_path.read_bytes())
    data["membership"].update(
        {
            "after": ["S01", "S02", *[task["id"] for task in tasks]],
            "added": [task["id"] for task in tasks],
        }
    )
    name = gate.fixture.write("evidence/_planning/attempt-1/too-many.json", data)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_amended_objective_completion_rejects_old_membership_revision(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate, membership(gate, ["S01"]))
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    state = gate.program.load()
    with pytest.raises(fp.coordination.GovernanceError, match="membership revision"):
        fp.coordination._objective_complete(
            gate.program,
            state,
            {"actor_id": "director", "objective_id": "goal", "membership_revision": 0},
            "unused.json",
            "0" * 64,
        )


def test_structural_snapshot_rejects_forged_membership_chain(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate, membership(gate, ["S01"]))
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    state = gate.program.load()
    state["objective"]["membership_history"][1]["before"] = ["S02"]
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    with pytest.raises(fp.ProgramError, match="chain"):
        fp.read_snapshot(gate.program.repo, gate.program.directory)


def test_multiple_intakes_resolve_exact_union_and_preserve_first_evidence(
    gate: GateFixture,
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    original = gate.program.load()
    first_report = gate.program.path("source/admissions/intake/report.md").read_bytes()
    first_receipt = gate.program.path(name).read_bytes()
    path = gate.program.path("source/admissions/second/report.md")
    path.parent.mkdir()
    path.write_text(
        "### S04. Fourth unit\nExpected result: fourth unit.\nAcceptance: verify fourth unit.\n\n### S05. Fifth unit\nExpected result: fifth unit.\nAcceptance: verify fifth unit.\n",
        encoding="utf-8",
    )
    template = fp.read_json(gate.program.path("source/admissions/intake/plan.json"))["tasks"][0]
    tasks = [
        {**deepcopy(template), "id": key, "candidate_paths": [f"src/{key}.py"]}
        for key in ("S04", "S05")
    ]
    plan_path = path.with_name("plan.json")
    plan_path.write_bytes(fp.canonical({"schema_version": 1, "tasks": tasks}))
    data: fp.Record = {
        "schema_version": 1,
        "operation": "task-admit",
        "id": "second",
        "program_id": original["program_id"],
        "actor_id": "director",
        "objective_id": "goal",
        "expected_revision": fp.phases.revision(original),
        "reason": "Two more independently reviewed units",
        "intake_authors": ["author"],
        "source": {
            "path": path.relative_to(gate.program.repo).as_posix(),
            "sha256": fp.sha(path.read_bytes()),
            "task_ids": ["S04", "S05"],
            "review_baseline": original["baseline_head"],
            "reference_base": "discovery",
        },
        "planning": {
            "path": plan_path.relative_to(gate.program.repo).as_posix(),
            "sha256": fp.sha(plan_path.read_bytes()),
        },
        "membership": {
            "before": ["S01", "S02", "S03"],
            "after": ["S01", "S02", "S03", "S04", "S05"],
            "added": ["S04", "S05"],
            "removed": [],
        },
    }
    name, _ = reviewed_input(gate, data)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    current = gate.program.load()
    assert current["source"] == original["source"]
    assert current["planning_sha256"] == original["planning_sha256"]
    assert current["source_intakes"][0] == original["source_intakes"][0]
    assert gate.program.path("source/admissions/intake/report.md").read_bytes() == first_report
    assert (
        gate.program.path("evidence/_planning/attempt-1/intake.json").read_bytes() == first_receipt
    )
    assert "discovery/source/admissions/second/report.md" in gate.program.packet(
        current, gate.program.task(current, "S05")
    )
    assert gate.fixture.run("check") == 0
    assert fp.read_snapshot(gate.program.repo, gate.program.directory)["total_tasks"] == 5


@pytest.mark.parametrize("placement", ["valid", "missing", "root", "unknown", "wrong-stream"])
def test_admission_requires_exact_compatible_authored_category_leaf(
    gate: GateFixture, placement: str
) -> None:
    state = gate.program.load()
    state["workstreams"].append({"id": "other", "name": "Other workstream"})
    state["categories"] = [
        {"id": "delivery", "title": "Delivery", "parent_id": None, "task_ids": []},
        {"id": "leaf", "title": "Leaf", "parent_id": "delivery", "task_ids": ["S01", "S02"]},
        {"id": "other", "title": "Other", "parent_id": None, "task_ids": []},
    ]
    gate.program.save(state)
    gate.ready()
    data = proposal(gate)
    if placement != "missing":
        target = {
            "valid": "leaf",
            "root": "delivery",
            "unknown": "absent",
            "wrong-stream": "other",
        }[placement]
        data["category_placements"] = [{"id": "S03", "category_id": target}]
    before = gate.program.path("backlog.json").read_bytes()
    if placement == "valid":
        name, _ = reviewed_input(gate, data)
        assert gate.fixture.run("task-admit", "--input", name) == 0
        current = gate.program.load()
        assert fp.coordination._indexed(current["categories"], "category")["leaf"]["task_ids"] == [
            "S01",
            "S02",
            "S03",
        ]
    else:
        name = gate.fixture.write("evidence/_planning/attempt-1/category.json", data)
        assert gate.fixture.run("task-admit", "--input", name) == 1
        assert gate.program.path("backlog.json").read_bytes() == before


def test_runtime_and_implementation_progress_preserves_new_gate_binding(gate: GateFixture) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    bound = fp.phases.planning_binding(gate.program.load())
    assert gate.fixture.claim("S03") == 0
    (gate.program.repo / "src/S03.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert (
        gate.fixture.run(
            "note",
            "S03",
            "--owner",
            "worker",
            "--reason",
            "Implementation milestone",
            "--next-action",
            "Submit actual current source",
        )
        == 0
    )
    gate.fixture.submit("S03")
    assert fp.phases.planning_binding(gate.program.load()) == bound
    assert gate.fixture.run("check") == 0


def publication_decision(gate: GateFixture, fields: fp.Record) -> str:
    state = gate.program.load()
    store = fp.planning.PublicationStore(gate.program.repo)
    receipt: fp.Record = {
        "schema_version": 1,
        "program_id": state["program_id"],
        "actor_id": "director",
        "expected_revision": fp.phases.revision(state),
        "ledger_revision": fp.phases.revision(store.load()),
        "binding": fp.phases.planning_binding(state),
        "reason": "Explicit synthetic publication reconciliation",
        "inventory_authors": ["author", "director"],
        **fields,
    }
    digest = (
        fp.phases.revision(
            {
                "inventory": receipt["inventory"],
                "uninspected_artifacts": receipt.get("uninspected_artifacts", []),
            }
        )
        if "inventory" in receipt
        else fp.phases.revision(receipt)
    )
    receipt["review"] = gate.fixture.write(
        "evidence/_publications/attempt-1/review.json",
        {
            "program_id": state["program_id"],
            "binding": receipt["binding"],
            "inventory_digest": digest,
            "reviewer": "reviewer",
            "reviewer_role": "independent",
            "verdict": "accepted",
            "unresolved_findings": [],
        },
    )
    evidence = "evidence/_publications/attempt-1/inspection.md"
    gate.program.path(evidence).write_text(
        "Synthetic actual named-endpoint observations and explicit exclusions.\n", encoding="utf-8"
    )
    receipt["evidence"] = [evidence]
    return gate.fixture.write("evidence/_publications/attempt-1/decision.json", receipt)


def test_legacy_tracking_adoption_is_explicit_and_never_recurses(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an existing untracked accepted registry and unavailable unrelated historical artifacts.
    gate.ready()
    gate.program.path(fp.planning.TRACKING).unlink()
    admission, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", admission) == 1
    before = gate.program.path("backlog.json").read_bytes()
    name = publication_decision(
        gate,
        {
            "inventory": fp.planning.inspect_publication_inventory(
                gate.program, gate.program.load(), ["discovery"]
            ),
            "obsolete_writers_quiesced": True,
            "uninspected_artifacts": [
                {
                    "path": ".omo/unreadable-historical",
                    "reason": "Not inspected; known unrelated synthetic artifacts",
                }
            ],
        },
    )

    def no_walk(*args, **kwargs):
        raise PermissionError("Unrelated historical cache cannot be traversed")

    monkeypatch.setattr(fp.planning.os, "walk", no_walk)
    # When the current director explicitly adopts the independently reviewed finite inventory.
    assert gate.fixture.run("publication-adopt", "--input", name) == 0
    # Then no runtime registry bytes are changed, and admission needs no repository walk.
    assert gate.program.path("backlog.json").read_bytes() == before
    assert gate.fixture.run("task-admit", "--input", admission) == 0
    assert fp.read_json(gate.program.path(fp.planning.TRACKING))["mode"] == "legacy-adopted"


@pytest.mark.parametrize(
    "fault", ["revision", "director", "self-review", "inventory", "quiescence", "related-exclusion"]
)
def test_legacy_tracking_adoption_rejects_unverified_scope(gate: GateFixture, fault: str) -> None:
    gate.ready()
    gate.program.path(fp.planning.TRACKING).unlink()
    name = publication_decision(
        gate,
        {
            "inventory": fp.planning.inspect_publication_inventory(
                gate.program, gate.program.load(), ["discovery"]
            ),
            "obsolete_writers_quiesced": True,
        },
    )
    receipt = fp.read_json(gate.program.path(name))
    if fault == "revision":
        receipt["expected_revision"] = "0" * 64
    elif fault == "director":
        receipt["actor_id"] = "worker"
    elif fault == "inventory":
        receipt["inventory"] = {}
    elif fault == "quiescence":
        receipt["obsolete_writers_quiesced"] = False
    elif fault == "related-exclusion":
        receipt["uninspected_artifacts"] = [
            {"path": "discovery", "reason": "Invalid related exclusion"}
        ]
    else:
        review = fp.read_json(gate.program.path(receipt["review"]))
        review["reviewer"] = "director"
        gate.fixture.write(receipt["review"], review)
    gate.fixture.write(name, receipt)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("publication-adopt", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before
    assert not gate.program.path(fp.planning.TRACKING).exists()


@pytest.mark.parametrize(
    "stage", ["registered", "publishing", "receipt", "complete", "completion-receipt"]
)
def test_central_succession_crashes_block_both_endpoints_and_recover(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    gate.ready()
    predecessor = GateFixture(create_program(gate.program.repo, "predecessor"))
    before = predecessor.program.path("backlog.json").read_bytes()
    store = fp.planning.PublicationStore(gate.program.repo)
    original_write = fp.atomic_write

    def interrupted(path: Path, content: bytes) -> None:
        if path == store.path("ledger.json"):
            value = json.loads(content)
            active = value["active"]
            if (
                (stage == "registered" and active and active[-1]["stage"] == "registered")
                or (stage == "publishing" and active and active[-1]["stage"] == "publishing")
                or (stage == "complete" and not active and value["history"])
            ):
                raise OSError("Injected central ledger interruption")
        if stage == "receipt" and path.name == "registered.json":
            raise OSError("Injected pre-ledger receipt interruption")
        if stage == "completion-receipt" and path.name == "completed.json":
            raise OSError("Injected immutable completion interruption")
        original_write(path, content)

    args = [
        "program-succeed",
        "--successor",
        "discovery",
        "--successor-id",
        "discovery",
        "--expected-revision",
        fp.phases.revision(predecessor.program.load()),
        "--successor-revision",
        fp.phases.revision(gate.program.load()),
        "--owner",
        "leader",
        "--reason",
        "Reviewed synthetic succession",
    ]
    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", interrupted)
        assert predecessor.fixture.run(*args) == 1
    ledger = store.load()
    assert predecessor.program.path("backlog.json").read_bytes() == before
    if stage in {"registered", "receipt"}:
        assert not ledger["active"]
        assert not predecessor.program.path("SUCCESSION_INCOMPLETE.json").exists()
        assert predecessor.fixture.run(*args) == 0
    else:
        assert ledger["active"]
        for endpoint in (gate, predecessor):
            state = fp.read_json(endpoint.program.path("backlog.json"))
            with pytest.raises((fp.ProgramError, fp.planning.PlanningError, fp.phases.PhaseError)):
                fp.planning.require_completed_publication(endpoint.program, state)
        assert gate.fixture.claim() == 0
        assert (
            predecessor.fixture.run(
                "program-succeed-recover",
                "--owner",
                "leader",
                "--reason",
                "Finish exact central operation",
            )
            == 0
        )
        assert not store.load()["active"]
        assert predecessor.program.path("backlog.json").read_bytes() == before


def test_registered_orphan_cancellation_preserves_runtime_progress(gate: GateFixture) -> None:
    gate.ready()
    target = fp.Program(gate.program.repo, gate.program.repo / "absent")
    marker = {
        "program_id": "discovery",
        "source": "discovery",
        "destination": "absent",
        "original_registry_sha256": fp.sha(gate.program.path("backlog.json").read_bytes()),
    }
    with gate.program.lock():
        identifier = fp.planning.publication_register(gate.program, "relocation", marker, target)
    assert gate.fixture.claim() == 0
    before = gate.program.path("backlog.json").read_bytes()
    name = publication_decision(gate, {"action": "cancel-registration", "operation_id": identifier})
    assert gate.fixture.run("publication-reconcile", "--input", name) == 0
    assert gate.program.path("backlog.json").read_bytes() == before
    assert (
        fp.planning.PublicationStore(gate.program.repo).load()["history"][-1]["stage"]
        == "cancelled"
    )


def test_started_publication_cannot_use_registration_cancellation(gate: GateFixture) -> None:
    gate.ready()
    target = fp.Program(gate.program.repo, gate.program.repo / "absent")
    with gate.program.lock():
        identifier = fp.planning.publication_register(
            gate.program, "relocation", {"program_id": "discovery"}, target
        )
        fp.planning.publication_begin(gate.program, identifier)
    name = publication_decision(gate, {"action": "cancel-registration", "operation_id": identifier})
    assert gate.fixture.run("publication-reconcile", "--input", name) == 1
    assert fp.planning.PublicationStore(gate.program.repo).load()["active"]


def test_failed_copy_retry_supersession_preserves_markers_and_allows_new_location(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate.ready()
    original_write = fp.atomic_write
    failed = fp.Program(gate.program.repo, gate.program.repo / "failed-copy")

    def interrupted(path: Path, content: bytes) -> None:
        if path == failed.path("source.md"):
            raise OSError("Injected copy failure")
        original_write(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", interrupted)
        assert (
            gate.fixture.run(
                "relocate",
                "--destination",
                "failed-copy",
                "--owner",
                "director",
                "--reason",
                "Move synthetic program",
            )
            == 1
        )
    frozen = failed.path("RELOCATION_INCOMPLETE.json").read_bytes()
    store = fp.planning.PublicationStore(gate.program.repo)
    old_id = store.load()["active"][0]["id"]
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            "moved",
            "--supersedes",
            old_id,
            "--owner",
            "director",
            "--reason",
            "Retry inspected failure to distinct destination",
        )
        == 0
    )
    from tools.tests.test_feature_program import Fixture

    moved = GateFixture(
        Fixture(fp.Program(gate.program.repo, gate.program.repo / "moved"), gate.fixture.report)
    )
    assert failed.path("RELOCATION_INCOMPLETE.json").read_bytes() == frozen
    assert not store.load()["active"]
    admission, _ = reviewed_input(moved)
    assert moved.fixture.run("task-admit", "--input", admission) == 0
    failed.path("RELOCATION_INCOMPLETE.json").write_bytes(frozen + b" ")
    with pytest.raises(fp.planning.PlanningError):
        fp.planning.require_completed_publication(moved.program, moved.program.load())


def fresh_fixture(gate: GateFixture):
    from tools.tests.test_feature_program import Fixture

    program = fp.Program(gate.program.repo, gate.program.repo / "fresh")
    program.directory.mkdir()
    program.path("source.md").write_bytes(gate.fixture.report)
    program.path("plan.json").write_bytes(gate.program.path("plan.json").read_bytes())
    program.path("README.md").write_bytes(b"# Authored recovery guide\n")
    return Fixture(program, gate.fixture.report)


def init_arguments() -> list[str]:
    return [
        "init",
        "--report",
        "fresh/source.md",
        "--planning",
        "fresh/plan.json",
        "--review-baseline",
        "a" * 40,
    ]


@pytest.mark.parametrize("boundary", ["tracking", "backlog"])
def test_fresh_init_exact_partial_retry_preserves_authored_bytes(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    fixture = fresh_fixture(gate)
    before = fixture.program.path("README.md").read_bytes()
    original = fp.atomic_write
    failing_name = fp.planning.TRACKING if boundary == "tracking" else "backlog.json"

    def interrupted(path: Path, content: bytes) -> None:
        if path == fixture.program.path(failing_name):
            raise OSError("Injected partial initialization")
        original(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", interrupted)
        assert fixture.run(*init_arguments()) == 1
    receipt = fixture.program.path("FRESH_INIT.json").read_bytes()
    assert not fixture.program.path("backlog.json").exists()
    monkeypatch.setattr(fp, "utc", lambda: "2030-01-01T01:02:03+00:00")
    assert fixture.run(*init_arguments()) == 0
    assert fixture.program.path("FRESH_INIT.json").read_bytes() == receipt
    assert fixture.program.path("README.md").read_bytes() == before
    assert fixture.program.load()["events"][-1]["timestamp"] == "2030-01-01T01:02:03+00:00"


@pytest.mark.parametrize("changed", ["report", "plan", "guide", "runtime"])
def test_fresh_init_partial_retry_rejects_changed_or_prior_material(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    fixture = fresh_fixture(gate)
    original = fp.atomic_write

    def interrupted(path: Path, content: bytes) -> None:
        if path == fixture.program.path(fp.planning.TRACKING):
            raise OSError("Injected initialization boundary")
        original(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", interrupted)
        assert fixture.run(*init_arguments()) == 1
    receipt = fixture.program.path("FRESH_INIT.json").read_bytes()
    name = {
        "report": "source.md",
        "plan": "plan.json",
        "guide": "README.md",
        "runtime": "CHECKPOINT.md",
    }[changed]
    path = fixture.program.path(name)
    path.write_bytes((path.read_bytes() if path.exists() else b"") + b" ")
    assert fixture.run(*init_arguments()) == 1
    assert not fixture.program.path("backlog.json").exists()
    assert fixture.program.path("FRESH_INIT.json").read_bytes() == receipt


def test_absent_target_reservation_cannot_be_initialized_using_equivalent_casing(
    gate: GateFixture,
) -> None:
    target = fp.Program(gate.program.repo, gate.program.repo / "fresh")
    with gate.program.lock():
        identifier = fp.planning.publication_register(
            gate.program, "relocation", {"program_id": "discovery"}, target
        )
    fixture = fresh_fixture(gate)
    assert (
        fp.main(["--repo", str(gate.program.repo), "--program-dir", "FRESH", *init_arguments()])
        == 1
    )
    assert not fixture.program.path("backlog.json").exists()
    assert fp.planning.PublicationStore(gate.program.repo).load()["active"][0]["id"] == identifier


@pytest.mark.parametrize(
    "endpoint",
    [".work/programs", ".work/programs/.publications", ".work/programs/.publications/nested"],
)
def test_reserved_publication_store_overlap_rejects_endpoint(
    gate: GateFixture, endpoint: str
) -> None:
    assert fp.main(["--repo", str(gate.program.repo), "--program-dir", endpoint, "status"]) == 1
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            endpoint,
            "--owner",
            "leader",
            "--reason",
            "Invalid reserved destination",
        )
        == 1
    )


def test_missing_adopted_ledger_is_not_recreated_by_fresh_init_or_publisher(
    gate: GateFixture,
) -> None:
    store = fp.planning.PublicationStore(gate.program.repo)
    store.path("ledger.json").unlink()
    fixture = fresh_fixture(gate)
    assert fixture.run(*init_arguments()) == 1
    assert not store.path("ledger.json").exists()
    with gate.program.lock(), pytest.raises(fp.planning.PlanningError, match="ledger missing"):
        fp.planning.publication_register(
            gate.program,
            "relocation",
            {"program_id": "discovery"},
            fp.Program(gate.program.repo, gate.program.repo / "absent"),
        )


def test_unrelated_inaccessible_payload_is_not_opened_but_related_read_fails(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate.ready()
    first = create_program(gate.program.repo, "unrelated")
    target = fp.Program(gate.program.repo, gate.program.repo / "elsewhere")
    with first.program.lock():
        identifier = fp.planning.publication_register(
            first.program, "relocation", {"program_id": "unrelated"}, target
        )
    store = fp.planning.PublicationStore(gate.program.repo)
    item = store.active(store.load(), identifier)
    payload_path = store.path(item["receipt"]["path"])
    original = fp.bounded_bytes

    def unreadable(path: Path, limit: int = 8 * 1024 * 1024) -> bytes:
        if path == payload_path:
            raise PermissionError("Synthetic inaccessible historical payload")
        return original(path, limit)

    monkeypatch.setattr(fp, "bounded_bytes", unreadable)
    name, _ = reviewed_input(gate)
    assert gate.fixture.run("task-admit", "--input", name) == 0
    with pytest.raises(PermissionError):
        fp.planning.require_completed_publication(first.program, first.program.load())


def test_partial_legacy_adoption_resumes_only_exact_recorded_decision(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate.ready()
    gate.program.path(fp.planning.TRACKING).unlink()
    name = publication_decision(
        gate,
        {
            "inventory": fp.planning.inspect_publication_inventory(
                gate.program, gate.program.load(), ["discovery"]
            ),
            "obsolete_writers_quiesced": True,
        },
    )
    original = fp.atomic_write

    def interrupted(path: Path, content: bytes) -> None:
        if path == gate.program.path(fp.planning.TRACKING):
            raise OSError("Injected adoption tracking failure")
        original(path, content)

    before = gate.program.path("backlog.json").read_bytes()
    with monkeypatch.context() as scoped:
        scoped.setattr(fp, "atomic_write", interrupted)
        assert gate.fixture.run("publication-adopt", "--input", name) == 1
    observation = gate.program.path("evidence/_publications/attempt-1/adopted.json").read_bytes()
    assert gate.fixture.run("publication-adopt", "--input", name) == 0
    assert gate.program.path("backlog.json").read_bytes() == before
    assert (
        gate.program.path("evidence/_publications/attempt-1/adopted.json").read_bytes()
        == observation
    )


def test_busy_publication_lock_rejects_before_canonical_state_change(gate: GateFixture) -> None:
    gate.ready()
    store = fp.planning.PublicationStore(gate.program.repo)
    before = gate.program.path("backlog.json").read_bytes()
    with store.lock():
        assert (
            gate.fixture.run(
                "relocate",
                "--destination",
                "busy-target",
                "--owner",
                "director",
                "--reason",
                "Synthetic busy ledger",
            )
            == 1
        )
    assert gate.program.path("backlog.json").read_bytes() == before
    assert not store.load()["active"]


def test_oversized_publication_ledger_is_bounded_before_definition_change(
    gate: GateFixture,
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    before = gate.program.path("backlog.json").read_bytes()
    store = fp.planning.PublicationStore(gate.program.repo)
    raw = store.path("ledger.json").read_bytes()
    store.path("ledger.json").write_bytes(raw + b" " * (8 * 1024 * 1024))
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_publication_related_read_budget_rejects_before_mutation(gate: GateFixture) -> None:
    gate.ready()
    paths = []
    for index in range(8):
        name = f"evidence/_publications/attempt-1/large-{index}.txt"
        gate.program.path(name).parent.mkdir(parents=True, exist_ok=True)
        gate.program.path(name).write_bytes(b"x" * (8 * 1024 * 1024))
        paths.append(name)
    receipt_name = "evidence/_publications/attempt-1/budget.json"
    receipt = {
        "mode": "legacy-adopted",
        "program_id": "discovery",
        "ledger_id": fp.planning.PublicationStore(gate.program.repo).load()["ledger_id"],
        "legacy_endpoints": [],
        "evidence": [
            {"path": name, "sha256": fp.sha(gate.program.path(name).read_bytes())} for name in paths
        ],
    }
    gate.fixture.write(receipt_name, receipt)
    tracking = fp.read_json(gate.program.path(fp.planning.TRACKING))
    tracking["receipt"] = {
        "path": receipt_name,
        "sha256": fp.sha(gate.program.path(receipt_name).read_bytes()),
    }
    gate.fixture.write(fp.planning.TRACKING, tracking)
    before = gate.program.path("backlog.json").read_bytes()
    with pytest.raises(fp.ProgramError, match="bounded read"):
        fp.planning.require_completed_publication(gate.program, gate.program.load())
    assert gate.program.path("backlog.json").read_bytes() == before


def test_registered_cancellation_rejects_changed_marker_inventory(gate: GateFixture) -> None:
    gate.ready()
    target = fp.Program(gate.program.repo, gate.program.repo / "other")
    with gate.program.lock():
        identifier = fp.planning.publication_register(
            gate.program, "relocation", {"program_id": "discovery"}, target
        )
    target.directory.mkdir()
    target.path("RELOCATION_INCOMPLETE.json").write_bytes(b"{}")
    name = publication_decision(gate, {"action": "cancel-registration", "operation_id": identifier})
    assert gate.fixture.run("publication-reconcile", "--input", name) == 1
    assert fp.planning.PublicationStore(gate.program.repo).load()["active"]


def test_legacy_failed_copy_is_imported_now_with_exact_original_bytes(gate: GateFixture) -> None:
    gate.ready()
    gate.program.path(fp.planning.TRACKING).unlink()
    failed = fp.Program(gate.program.repo, gate.program.repo / "old-copy")
    failed.directory.mkdir()
    raw = (
        fp.canonical({"phase": "copying", "source": "discovery", "destination": "old-copy"})
        + b" \n"
    )
    failed.path("RELOCATION_INCOMPLETE.json").write_bytes(raw)
    name = publication_decision(
        gate,
        {
            "inventory": fp.planning.inspect_publication_inventory(
                gate.program, gate.program.load(), ["discovery", "old-copy"]
            ),
            "obsolete_writers_quiesced": True,
        },
    )
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("publication-adopt", "--input", name) == 0
    store = fp.planning.PublicationStore(gate.program.repo)
    old = store.load()["active"][0]
    observed = store.payload(old, fp.ReadBudget(64 * 1024 * 1024))
    assert old["stage"] == "legacy-observed"
    assert observed["legacy_observed_at"]
    assert observed["failed_marker_sha256"] == fp.sha(raw)
    assert failed.path("RELOCATION_INCOMPLETE.json").read_bytes() == raw
    assert gate.program.path("backlog.json").read_bytes() == before
    with pytest.raises(fp.planning.PlanningError, match="recovery/reconciliation"):
        fp.planning.require_completed_publication(gate.program, gate.program.load())
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            "replacement",
            "--supersedes",
            old["id"],
            "--owner",
            "director",
            "--reason",
            "Retry explicitly observed legacy failure",
        )
        == 0
    )
    assert failed.path("RELOCATION_INCOMPLETE.json").read_bytes() == raw
    failed.path("RELOCATION_INCOMPLETE.json").unlink()
    with pytest.raises(fp.ProgramError, match="remains guarded"):
        failed.load()
