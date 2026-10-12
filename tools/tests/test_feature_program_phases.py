"""Real CLI transitions on temporary programs; synthetic evidence is labeled explicitly."""

# noqa: SIZE_OK - The accepted file owns the full phase/succession acceptance matrix.

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Literal, assert_never

import pytest

from tools.tests.test_feature_program import Fixture, HEAD, fp

phases = fp.phases


@dataclass(frozen=True, slots=True)
class GateFixture:
    fixture: Fixture

    @property
    def program(self) -> fp.Program:
        return self.fixture.program

    def phase(self, command: str, fields: fp.Record, label: str | None = None) -> int:
        state = self.program.load()
        receipt = {
            **phases.binding(self.program, state),
            "actor_id": "director",
            "reason": "Synthetic gate transition for a temporary program",
            **fields,
        }
        name = self.fixture.write(f"evidence/_phases/attempt-1/{label or command}.json", receipt)
        return self.fixture.run(command, "--input", name)

    def adopt(self) -> int:
        return self.phase("phase-adopt", {"acknowledged": True})

    def freeze(self, label: str = "inventory-freeze") -> int:
        name = "evidence/_phases/attempt-1/candidates.md"
        path = self.program.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("Synthetic candidate inventory: S01 and S02.\n", encoding="utf-8")
        return self.phase(
            "inventory-freeze",
            {
                "candidate_path": name,
                "candidate_digest": fp.sha(path.read_bytes()),
                "inventory_authors": ["author", "director"],
                "units": [
                    {"id": identifier, "scope": [f"src/{identifier}.py"]}
                    for identifier in ("S01", "S02")
                ],
            },
            label,
        )

    def review(self, fields: fp.Record | None = None, label: str = "candidate-review") -> int:
        return self.phase(
            "candidate-review",
            {
                "actor_id": "reviewer",
                "reviewer_role": "independent",
                "verdict": "accepted",
                "unresolved_findings": [],
                **(fields or {}),
            },
            label,
        )

    def accept(self, fields: fp.Record | None = None, label: str = "execution-accept") -> int:
        return self.phase(
            "execution-accept", {"acceptance_complete": True, **(fields or {})}, label
        )

    def ready(self) -> None:
        assert self.adopt() == 0
        assert self.freeze() == 0
        assert self.review() == 0
        assert self.accept() == 0


def create_program(repo: Path, name: str) -> Fixture:
    program = fp.Program(repo, repo / name)
    program.directory.mkdir(parents=True)
    report = b"# Synthetic reviewed report\n\n### S01. First unit\n\nAcceptance: preserve first unit.\n\n### S02. Second unit\n\nAcceptance: preserve second unit.\n"
    program.path("source.md").write_bytes(report)
    plan: fp.Record = {
        "schema_version": 1,
        "program_id": name,
        "baseline_head": HEAD,
        "workstreams": [{"id": "delivery", "name": "Synthetic delivery"}],
        "tasks": [
            {
                "id": identifier,
                "phase": 1,
                "workstream": "delivery",
                "candidate_paths": [f"src/{identifier}.py"],
                "depends_on": [],
                "related_to": [],
                "conflict_groups": [],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Revalidate synthetic implementation",
                "planning_note": "Synthetic only",
            }
            for identifier in ("S01", "S02")
        ],
    }
    program.path("plan.json").write_bytes(fp.canonical(plan))
    fixture = Fixture(program, report)
    assert (
        fixture.run(
            "init",
            "--report",
            f"{name}/source.md",
            "--planning",
            f"{name}/plan.json",
            "--review-baseline",
            HEAD,
        )
        == 0
    )
    return fixture


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GateFixture:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for identifier in ("S01", "S02"):
        (repo / f"src/{identifier}.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(fp, "git", lambda _repo, args: "" if args[0] == "status" else HEAD)
    fixture = create_program(repo, "discovery")
    state = fixture.program.load()
    state["objective"] = {
        "id": "goal",
        "title": "Synthetic objective",
        "criteria": ["Verify agreed units"],
        "task_ids": ["S01", "S02"],
        "status": "active",
        "supplied_by": "user",
        "decision_owner": "director",
        "next_action_owner": "director",
        "history": [],
    }
    state["actors"] = [
        {
            "id": "director",
            "role": "director",
            "parent_id": None,
            "model_id": None,
            "reasoning_effort": None,
            "provenance": "unknown",
            "observed_status": "unknown",
            "staffing_status": "active",
            "history": [],
        }
    ]
    scope = ["src/S01.py", "src/S02.py"]
    appointment: fp.Record = {
        "actor_id": "director",
        "authority": {"authorized_by": "synthetic-human", "basis": "Temporary fixture"},
        "source_head": HEAD,
        "source_digest": fixture.program.source_digest(scope),
        "scope": scope,
        "acknowledged": True,
        "evidence": [],
    }
    with fixture.program.lock():
        fixture.program.save(state)
    receipt = fixture.write("evidence/_coordination/attempt-1/appoint.json", appointment)
    assert fixture.run("director-appoint", "--input", receipt) == 0
    return GateFixture(fixture)


def test_opt_in_sequence_guards_claim_submit_and_keeps_legacy(gate: GateFixture) -> None:
    assert gate.fixture.claim() == 0  # Legacy still permits the source-revalidated claim.
    assert gate.adopt() == 0
    before = gate.program.path("backlog.json").read_bytes()
    assert (
        gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", gate.fixture.receipt())
        == 1
    )
    assert gate.program.path("backlog.json").read_bytes() == before
    assert gate.fixture.claim("S02", owner="another") == 1
    assert gate.accept() == 1
    assert gate.freeze() == 0
    assert gate.accept(label="accept-before-review") == 1
    assert gate.review() == 0
    assert gate.accept() == 0
    assert (
        gate.fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Observed milestone",
            "--next-action",
            "Verify changed implementation",
        )
        == 0
    )
    (gate.program.repo / "src/S01.py").write_text("VALUE = 2\n", encoding="utf-8")
    gate.fixture.submit()
    assert gate.fixture.claim("S02", owner="another") == 0
    assert gate.fixture.run("check") == 0


@pytest.mark.parametrize("actor", ["director", "author"])
def test_inventory_authors_cannot_self_review(gate: GateFixture, actor: str) -> None:
    assert gate.adopt() == 0
    assert gate.freeze() == 0
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.review({"actor_id": actor}) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "revision",
        "candidate",
        "objective",
        "definitions",
        "planning",
        "units",
        "director",
        "verdict",
    ],
)
def test_phase_acceptance_rejects_stale_or_incomplete_contract(
    gate: GateFixture,
    fault: Literal[
        "revision",
        "candidate",
        "objective",
        "definitions",
        "planning",
        "units",
        "director",
        "verdict",
    ],
) -> None:
    assert gate.adopt() == 0
    assert gate.freeze() == 0
    assert gate.review() == 0
    overrides: fp.Record = {}
    state = gate.program.load()
    match fault:
        case "revision":
            overrides["expected_revision"] = "0" * 64
        case "candidate":
            overrides["candidate_digest"] = "0" * 64
        case "objective":
            overrides["objective_digest"] = "0" * 64
        case "definitions":
            overrides["task_definition_digest"] = "0" * 64
        case "planning":
            overrides["planning_digest"] = "0" * 64
        case "units":
            overrides["units"] = [{"id": "S01", "scope": ["src/S01.py"]}]
        case "director":
            overrides["actor_id"] = "author"
        case "verdict":
            assert (
                gate.review(
                    {"verdict": "rejected", "unresolved_findings": ["Missing acceptance"]},
                    "rejected-review",
                )
                == 0
            )
        case unreachable:
            assert_never(unreachable)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.accept(overrides) == 1
    assert gate.program.path("backlog.json").read_bytes() == before
    assert phases.planning_binding(state) == phases.planning_binding(gate.program.load())


@pytest.mark.parametrize(
    "drift", ["candidate", "planning", "objective", "definition", "authored-metadata"]
)
def test_direct_execution_methods_reject_changed_planning_provenance(
    gate: GateFixture,
    drift: Literal["candidate", "planning", "objective", "definition", "authored-metadata"],
) -> None:
    gate.ready()
    assert gate.fixture.claim() == 0
    state = gate.program.load()
    match drift:
        case "candidate":
            gate.program.path("evidence/_phases/attempt-1/candidates.md").write_text(
                "changed inventory", encoding="utf-8"
            )
        case "planning":
            gate.program.path("plan.json").write_text("{}", encoding="utf-8")
        case "objective":
            fp.record(state["objective"], "objective")["title"] = "Another objective"
        case "definition":
            gate.program.task(state, "S01")["required_checks"] = ["acceptance"]
        case "authored-metadata":
            gate.program.task(state, "S01")["authored_constraints"] = [
                "Consequential scope restriction"
            ]
        case unreachable:
            assert_never(unreachable)
    with pytest.raises((phases.PhaseError, fp.ProgramError)):
        gate.program.submit(
            state, argparse.Namespace(id="S01", owner="worker", receipt="missing.json")
        )
    with pytest.raises((phases.PhaseError, fp.ProgramError)):
        gate.program.claim(
            state, argparse.Namespace(id="S02", owner="another", scope=["src/S02.py"])
        )


def test_exact_scope_and_current_director_are_required(gate: GateFixture) -> None:
    gate.ready()
    assert gate.fixture.claim(scope="src") == 1
    assert gate.fixture.claim(scope="src/S02.py") == 1
    state = gate.program.load()
    fp.record(state["director"], "director")["actor_id"] = "replacement"
    with pytest.raises(phases.PhaseError, match="current director"):
        phases.guard_execution(gate.program, state, "S01", ["src/S01.py"])


def test_progress_and_actor_records_do_not_invalidate_planning(gate: GateFixture) -> None:
    gate.ready()
    state = gate.program.load()
    baseline = phases.planning_binding(state)
    state["checkpoint"] = {"note": "Synthetic progress"}
    fp.record(state["objective"], "objective")["next_action_owner"] = "user"
    gate.program.task(state, "S01")["next_action"] = "New implementation step"
    fp.record(fp.sequence(state["actors"], "actors")[0], "actor")["observed_status"] = "idle"
    assert phases.planning_binding(state) == baseline
    phases.guard_execution(gate.program, state, "S01", ["src/S01.py"])
    assert phases.revision(state) != phases.revision(gate.program.load())


def succession_arguments(gate: GateFixture, target: Fixture) -> list[str]:
    return [
        "program-succeed",
        "--successor",
        target.program.directory.relative_to(gate.program.repo).as_posix(),
        "--successor-id",
        fp.string(target.program.load()["program_id"], "program ID"),
        "--expected-revision",
        phases.revision(gate.program.load()),
        "--successor-revision",
        phases.revision(target.program.load()),
        "--owner",
        "director",
        "--reason",
        "Use initialized execution program",
    ]


def test_succession_preserves_all_original_bytes_and_allows_successor_progress(
    gate: GateFixture,
) -> None:
    assert gate.fixture.claim() == 0
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "leader",
            "--reason",
            "Return discovery to planned state",
            "--next-action",
            "Use execution successor",
        )
        == 0
    )
    target = create_program(gate.program.repo, "execution")
    files = {
        path.relative_to(gate.program.directory).as_posix(): path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    assert gate.fixture.run(*succession_arguments(gate, target)) == 0
    for name, raw in files.items():
        assert gate.program.path(name).read_bytes() == raw
    assert gate.fixture.run("status") == 1
    assert (
        gate.fixture.run(
            "claim",
            "S02",
            "--owner",
            "worker",
            "--scope",
            "src/S02.py",
            "--next-action",
            "Implement",
            "--revalidation",
            "missing.json",
        )
        == 1
    )
    assert target.claim() == 0
    assert target.run("check") == 0


@pytest.mark.parametrize(
    "stage",
    [
        "predecessor-guard",
        "successor-guard",
        "successor-receipt",
        "predecessor-final",
        "successor-activation",
        "predecessor-final-removal",
    ],
)
def test_interrupted_succession_is_explicit_and_recoverable(
    gate: GateFixture,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = create_program(gate.program.repo, "execution")
    predecessor_bytes = gate.program.path("backlog.json").read_bytes()
    successor_bytes = target.program.path("backlog.json").read_bytes()
    original = fp.atomic_write
    names = {
        "predecessor-guard": ("discovery", "SUCCESSION_INCOMPLETE.json"),
        "successor-guard": ("execution", "SUCCESSION_INCOMPLETE.json"),
        "successor-receipt": ("execution", "PREDECESSOR.json"),
        "predecessor-final": ("discovery", "SUCCEEDED.json"),
    }

    def fail_write(path: Path, content: bytes) -> None:
        if stage in names and (path.parent.name, path.name) == names[stage]:
            raise OSError("Injected synthetic publication failure")
        original(path, content)

    original_unlink = Path.unlink

    def fail_unlink(path: Path, *args, **kwargs) -> None:
        if path.name == "SUCCESSION_INCOMPLETE.json" and (
            (stage == "successor-activation" and path.parent.name == "execution")
            or (stage == "predecessor-final-removal" and path.parent.name == "discovery")
        ):
            raise OSError("Injected synthetic activation failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(fp, "atomic_write", fail_write)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    assert gate.fixture.run(*succession_arguments(gate, target)) == 1
    assert gate.program.path("backlog.json").read_bytes() == predecessor_bytes
    assert target.program.path("backlog.json").read_bytes() == successor_bytes
    monkeypatch.setattr(fp, "atomic_write", original)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    if stage in {"successor-guard", "predecessor-final-removal"}:
        assert target.claim() == 0
        successor_bytes = target.program.path("backlog.json").read_bytes()
    if stage == "predecessor-guard":
        assert gate.fixture.run("status") == 0  # Nothing was published; no false completion.
        assert gate.fixture.run(*succession_arguments(gate, target)) == 0
    else:
        assert gate.fixture.run("status") == 1
        assert "Succession publication incomplete" in capsys.readouterr().err
        assert (
            gate.fixture.run(
                "program-succeed-recover",
                "--owner",
                "director",
                "--reason",
                "Recover verified unchanged temporary programs",
            )
            == 0
        )
    assert gate.fixture.run("status") == 1
    assert target.run("status") == 0
    assert target.program.path("backlog.json").read_bytes() == successor_bytes


@pytest.mark.parametrize(
    "fault", ["active", "identity", "revision", "nested", "same", "overlap", "missing"]
)
def test_succession_rejects_unsafe_targets_without_publication(
    gate: GateFixture,
    fault: Literal["active", "identity", "revision", "nested", "same", "overlap", "missing"],
) -> None:
    target = create_program(gate.program.repo, "execution")
    args = succession_arguments(gate, target)
    match fault:
        case "active":
            assert gate.fixture.claim() == 0
        case "identity":
            args[args.index("--successor-id") + 1] = "unknown"
        case "revision":
            args[args.index("--successor-revision") + 1] = "0" * 64
        case "nested":
            target.program.path("nested/backlog.json").parent.mkdir()
            target.program.path("nested/backlog.json").write_text("{}", encoding="utf-8")
        case "same":
            args[args.index("--successor") + 1] = "discovery"
        case "overlap":
            args[args.index("--successor") + 1] = "discovery/nested"
        case "missing":
            args[args.index("--successor") + 1] = "absent"
        case unreachable:
            assert_never(unreachable)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run(*args) == 1
    assert gate.program.path("backlog.json").read_bytes() == before
    assert not gate.program.path("SUCCEEDED.json").exists()
    assert not gate.program.path("SUCCESSION_INCOMPLETE.json").exists()


def test_succession_recovery_refuses_changed_frozen_provenance(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = create_program(gate.program.repo, "execution")
    original = fp.atomic_write

    def interrupt(path: Path, content: bytes) -> None:
        if path.parent.name == "execution" and path.name == "PREDECESSOR.json":
            raise OSError("Injected synthetic interruption")
        original(path, content)

    monkeypatch.setattr(fp, "atomic_write", interrupt)
    assert gate.fixture.run(*succession_arguments(gate, target)) == 1
    monkeypatch.setattr(fp, "atomic_write", original)
    state = fp.read_json(target.program.path("backlog.json"))
    target.program.task(state, "S01")["candidate_paths"] = ["src/S02.py"]
    target.program.path("backlog.json").write_bytes(fp.canonical(state))
    changed = target.program.path("backlog.json").read_bytes()
    assert (
        gate.fixture.run(
            "program-succeed-recover",
            "--owner",
            "director",
            "--reason",
            "Investigate changed successor",
        )
        == 1
    )
    assert target.program.path("backlog.json").read_bytes() == changed
    assert gate.fixture.run("status") == 1
    assert target.run("status") == 1


def test_succession_rejects_linked_successor_before_publication(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = create_program(gate.program.repo, "execution")
    args = succession_arguments(gate, target)
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path == target.program.directory or original(path)
    )
    assert gate.fixture.run(*args) == 1
    assert not gate.program.path("SUCCESSION_INCOMPLETE.json").exists()


def test_adopted_program_relocation_keeps_gate_provenance_and_snapshot_reads(
    gate: GateFixture,
) -> None:
    gate.ready()
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert snapshot["program_id"] == "discovery"
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            "moved",
            "--owner",
            "director",
            "--reason",
            "Relocate adopted synthetic program",
        )
        == 0
    )
    target = Fixture(
        fp.Program(gate.program.repo, gate.program.repo / "moved"), gate.fixture.report
    )
    assert target.claim() == 0
    assert target.run("check") == 0


def test_invalid_phase_metadata_normalizes_snapshot_error(gate: GateFixture) -> None:
    gate.ready()
    state = gate.program.load()
    fp.record(state["phases"], "phases")["schema_version"] = 2
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    with pytest.raises(fp.ProgramError, match="Unsupported phases"):
        fp.read_snapshot(gate.program.repo, gate.program.directory)


@pytest.mark.parametrize("fault", ["unsafe-path", "successor-source", "malformed-recovery"])
def test_standalone_cli_rejections_have_contextual_errors_without_traceback(
    gate: GateFixture, fault: Literal["unsafe-path", "successor-source", "malformed-recovery"]
) -> None:
    target = create_program(gate.program.repo, "execution")
    args = succession_arguments(gate, target)
    match fault:
        case "unsafe-path":
            args[args.index("--successor") + 1] = "../outside"
        case "successor-source":
            target.program.path("source.md").write_text("changed", encoding="utf-8")
        case "malformed-recovery":
            gate.program.path("SUCCESSION_INCOMPLETE.json").write_text("{invalid", encoding="utf-8")
            args = [
                "program-succeed-recover",
                "--owner",
                "director",
                "--reason",
                "Investigate malformed marker",
            ]
        case unreachable:
            assert_never(unreachable)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(fp.ROOT / "tools/feature_program.py"),
            "--repo",
            str(gate.program.repo),
            "--program-dir",
            "discovery",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "feature-program:" in result.stderr
    assert "Traceback" not in result.stderr
