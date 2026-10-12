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


def test_corrupt_marker_has_actionable_fail_closed_diagnostic(
    gate: GateFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    path = gate.program.repo / "unrelated" / "RELOCATION_INCOMPLETE.json"
    path.parent.mkdir()
    path.write_text("broken JSON", encoding="utf-8")
    before = gate.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    assert gate.fixture.run("task-admit", "--input", name) == 1
    assert "unrelated/RELOCATION_INCOMPLETE.json" in capsys.readouterr().err
    assert gate.program.path("backlog.json").read_bytes() == before


def test_publication_discovery_budget_rejects_without_registry_change(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate.ready()
    name, _ = reviewed_input(gate)
    before = gate.program.path("backlog.json").read_bytes()

    def overflow(_root: Path, **kwargs):
        for _index in range(10001):
            yield str(gate.program.repo), [], []

    monkeypatch.setattr(fp.planning.os, "walk", overflow)
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
