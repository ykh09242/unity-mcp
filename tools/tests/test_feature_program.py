"""Isolated coordination tests; no real private program or native checks are used."""

from __future__ import annotations

import argparse
from copy import deepcopy
from io import BytesIO
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import feature_program as fp  # noqa: E402

HEAD = "a" * 40
LEGACY_IDS = tuple(
    f"{prefix}{index:02d}"
    for prefix, count in (
        ("S", 15),
        ("E", 17),
        ("V", 17),
        ("Q", 16),
        ("P", 15),
        ("DX", 16),
        ("N", 15),
    )
    for index in range(1, count + 1)
    if (prefix, index) != ("DX", 11)
)


@dataclass(frozen=True, slots=True)
class Fixture:
    program: fp.Program
    report: bytes

    def run(self, *arguments: str) -> int:
        return fp.main(
            [
                "--repo",
                str(self.program.repo),
                "--program-dir",
                self.program.directory.relative_to(self.program.repo).as_posix(),
                *arguments,
            ]
        )

    def write(self, name: str, value: fp.Json) -> str:
        path = self.program.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fp.canonical(value))
        return name

    def evidence(self, task_id: str, number: int = 1, label: str = "inspection") -> str:
        name = f"evidence/{task_id}/attempt-{number}/{label}.txt"
        path = self.program.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "Synthetic fixture evidence only; no real product/native execution.\n", encoding="utf-8"
        )
        return name

    def validation(self, task_id: str, scopes: list[str], number: int = 1) -> str:
        return self.write(
            f"evidence/{task_id}/attempt-{number}/revalidation.json",
            {
                "task_id": task_id,
                "attempt": number,
                "head": HEAD,
                "scope": sorted(scopes),
                "source_digest": fp.digest(self.program.source_snapshot(scopes)),
                "acceptance_reviewed": True,
                "summary": "Fixture acceptance inspected",
                "evidence": [self.evidence(task_id, number)],
            },
        )

    def claim(
        self, task_id: str = "S01", scope: str | None = None, owner: str = "worker", number: int = 1
    ) -> int:
        scope = scope or f"src/{task_id}.py"
        validation = self.validation(task_id, [scope], number)
        return self.run(
            "claim",
            task_id,
            "--owner",
            owner,
            "--scope",
            scope,
            "--next-action",
            "Implement bounded fixture",
            "--revalidation",
            validation,
        )

    def receipt(self, task_id: str = "S01", round_number: int = 1) -> str:
        task = self.program.task(self.program.load(), task_id)
        attempt = self.program.current_attempt(task)
        number = attempt["number"]
        assert isinstance(number, int)
        log = self.evidence(task_id, number, f"verification-{round_number}")
        snapshot = self.program.source_snapshot(fp.strings(attempt["scope"], "scope"))
        return self.write(
            f"evidence/{task_id}/attempt-{number}/execution-{round_number}.json",
            {
                "task_id": task_id,
                "attempt": number,
                "summary": "Synthetic implementation verified",
                "changed_paths": self.program.changed_paths(
                    fp.record(attempt["baseline"], "baseline"), snapshot
                ),
                "checks": [
                    {
                        "id": check,
                        "status": "passed",
                        "command": "fixture assertion",
                        "execution_kind": "test",
                        "evidence": [log],
                    }
                    for check in fp.strings(task["required_checks"], "checks")
                ],
                "acceptance_complete": True,
                "acceptance_evidence": [
                    {
                        "criterion": "Entire fixture acceptance",
                        "status": "passed",
                        "evidence": [log],
                    }
                ],
                "unresolved_acceptance": [],
                "unresolved_blockers": [],
                "limitations": [],
                "next_action": "Review fixture",
            },
        )

    def submit(self, task_id: str = "S01", round_number: int = 1) -> str:
        path = self.receipt(task_id, round_number)
        assert self.run("submit", task_id, "--owner", "worker", "--receipt", path) == 0
        return path

    def review(self, task_id: str = "S01", accepted: bool = True, round_number: int = 1) -> str:
        attempt = self.program.current_attempt(self.program.task(self.program.load(), task_id))
        submission = fp.record(attempt["submission"], "submission")
        number = attempt["number"]
        assert isinstance(number, int)
        return self.write(
            f"evidence/{task_id}/attempt-{number}/review-{round_number}.json",
            {
                "task_id": task_id,
                "attempt": number,
                "receipt_sha256": fp.record(submission["receipt"], "receipt")["sha256"],
                "source_digest": submission["source_digest"],
                "verdict": "accepted" if accepted else "rejected",
                "reviewer": "reviewer",
                "reviewer_role": "independent",
                "acceptance_complete": accepted,
                "unresolved_findings": [] if accepted else ["Fix fixture result"],
                "evidence": [self.evidence(task_id, number, f"review-evidence-{round_number}")],
                "next_action": "Correct fixture" if not accepted else "Retain evidence",
            },
        )


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    repo = tmp_path / "repo"
    repo.mkdir()
    program = fp.Program(repo, repo / "program")
    program.directory.mkdir()
    lines = ["# Synthetic reviewed opportunities\r\n", "\r\n"]
    for task_id in LEGACY_IDS:
        lines += [
            f"### {task_id}. Fixture opportunity\r\n",
            "\r\n",
            "- Existing: synthetic evidence.\r\n",
            "- Acceptance\r\n",
            "  criterion: preserve all wrapped prose.\r\n",
            f'Unique payload {task_id}: `{{"value": 0}}`.\r\n',
            "\r\n",
        ]
    lines += ["## Not a feature\r\n", "Other notes remain in the source report.\r\n"]
    report = "".join(lines).encode("utf-8")
    (repo / "report.md").write_bytes(report)
    plan = {
        "schema_version": 1,
        "program_id": "unity-mcp-110",
        "baseline_head": HEAD,
        "workstreams": [
            {
                "id": "fixture",
                "name": "Fixture",
                "default_agent": "worker",
                "focus": "Isolated tests",
            }
        ],
        "tasks": [
            {
                "id": task_id,
                "workstream": "fixture",
                "phase": 1,
                "depends_on": [],
                "related_to": [],
                "conflict_groups": [],
                "candidate_paths": [f"src/{task_id}.py"],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Revalidate synthetic source",
                "planning_note": "Fixture only",
            }
            for task_id in LEGACY_IDS
        ],
    }
    (repo / "plan.json").write_bytes(fp.canonical(plan))
    (repo / "src").mkdir()
    for task_id in LEGACY_IDS:
        (repo / "src" / f"{task_id}.py").write_text("original = True\n", encoding="utf-8")
    monkeypatch.setattr(
        fp, "git", lambda _root, arguments: "" if arguments[0] == "status" else HEAD
    )
    result = Fixture(program, report)
    assert (
        result.run(
            "init",
            "--report",
            "report.md",
            "--planning",
            "plan.json",
            "--review-baseline",
            "b" * 40,
        )
        == 0
    )
    return result


def test_lossless_110_import_and_idempotent_render(fixture: Fixture) -> None:
    state = fixture.program.load()
    sections = fp.extract_sections(fixture.report)
    assert len(state["tasks"]) == 110
    assert "DX11" not in sections
    for task_id in LEGACY_IDS:
        assert fixture.program.task(state, task_id)["source"] == sections[task_id]
        section = fp.record(sections[task_id], "source")
        assert section["text"].encode() in fixture.report
        assert "Acceptance\r\n  criterion" in section["text"]
    before = {
        name: fixture.program.path(name).read_bytes() for name in fixture.program.views(state)
    }
    assert fixture.run("render") == 0
    assert fixture.run("render") == 0
    assert before == {name: fixture.program.path(name).read_bytes() for name in before}
    assert fixture.run("check") == 0


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_report_requires_exact_unique_ids(fixture: Fixture, change: str) -> None:
    data = fixture.report
    if change == "missing":
        data = data.replace(b"### S01.", b"### Other.", 1)
    elif change == "duplicate":
        data += b"### S01. Duplicate\n"
    else:
        data = data.replace(b"### S01.", b"### S99.", 1)
    with pytest.raises(fp.ProgramError):
        fp.extract_sections(data, list(LEGACY_IDS))


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "cycle", "unknown_dependency", "missing_check", "false_done"]
)
def test_registry_invariants_reject_invalid_progress(fixture: Fixture, fault: str) -> None:
    state = fixture.program.load()
    tasks = state["tasks"]
    if fault == "missing":
        tasks.pop()
    elif fault == "duplicate":
        tasks[-1] = tasks[0]
    elif fault == "cycle":
        tasks[0]["depends_on"] = ["S02"]
        tasks[1]["depends_on"] = ["S01"]
    elif fault == "unknown_dependency":
        tasks[0]["depends_on"] = ["DX11"]
    elif fault == "missing_check":
        tasks[0]["required_checks"] = ["acceptance"]
    else:
        tasks[0]["status"] = "done"
    with pytest.raises(fp.ProgramError):
        fixture.program.validate(state)


def test_init_refuses_to_replace_progress(fixture: Fixture) -> None:
    before = fixture.program.path("backlog.json").read_bytes()
    assert (
        fixture.run(
            "init", "--report", "report.md", "--planning", "plan.json", "--review-baseline", HEAD
        )
        == 1
    )
    assert fixture.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("drift", ["head", "source", "scope", "acceptance", "evidence"])
def test_claim_requires_current_revalidation(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    name = fixture.validation("S01", ["src/S01.py"])
    data = fp.read_json(fixture.program.path(name))
    if drift == "head":
        monkeypatch.setattr(fp, "git", lambda *_args: "c" * 40)
    elif drift == "source":
        (fixture.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
    elif drift == "scope":
        data["scope"] = ["src/S02.py"]
    elif drift == "acceptance":
        data["acceptance_reviewed"] = False
    else:
        data["evidence"] = ["evidence/S01/attempt-1/missing.txt"]
    fixture.write(name, data)
    before = fixture.program.path("backlog.json").read_bytes()
    assert (
        fixture.run(
            "claim",
            "S01",
            "--owner",
            "worker",
            "--scope",
            "src/S01.py",
            "--next-action",
            "Inspect",
            "--revalidation",
            name,
        )
        == 1
    )
    assert before == fixture.program.path("backlog.json").read_bytes()


def test_scope_owner_group_and_dependency_conflicts(fixture: Fixture) -> None:
    state = fixture.program.load()
    fixture.program.task(state, "S03")["depends_on"] = ["S01"]
    fixture.program.task(state, "S01")["conflict_groups"] = ["shared"]
    fixture.program.task(state, "S04")["conflict_groups"] = ["shared"]
    with fixture.program.lock():
        fixture.program.save(state)
    assert fixture.claim() == 0
    assert fixture.claim("S02", scope="src", owner="other") == 1
    assert fixture.claim("S02", owner="worker") == 1
    assert fixture.claim("S03", owner="other") == 1
    assert fixture.claim("S04", owner="other") == 1
    assert (
        fixture.run(
            "block",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Fixture prerequisite",
            "--next-action",
            "Inspect",
        )
        == 0
    )
    assert fixture.claim("S03", owner="other") == 1
    assert fixture.claim("S04", owner="other") == 1


def test_release_reclaim_and_notes_preserve_attempt_history(fixture: Fixture) -> None:
    assert fixture.claim() == 0
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Inspection captured",
            "--next-action",
            "Continue",
        )
        == 0
    )
    assert (
        fixture.run(
            "block",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Agent unavailable",
            "--next-action",
            "Resolve owner",
        )
        == 0
    )
    assert (
        fixture.run(
            "release",
            "S01",
            "--owner",
            "leader",
            "--reason",
            "Explicit replacement",
            "--next-action",
            "Revalidate for replacement",
        )
        == 0
    )
    assert fixture.claim(owner="replacement", number=2) == 0
    task = fixture.program.task(fixture.program.load(), "S01")
    assert task["owner"] == "replacement"
    assert [attempt["number"] for attempt in task["attempts"]] == [1, 2]
    assert task["attempts"][0]["revalidation_stale"] is True
    assert task["attempts"][0]["owner"] == "worker"
    assert fixture.run("check") == 0


@pytest.mark.parametrize(
    "fault", ["missing_check", "missing_evidence", "native_compile", "acceptance", "changed_paths"]
)
def test_submit_rejects_incomplete_verification(fixture: Fixture, fault: str) -> None:
    if fault == "native_compile":
        state = fixture.program.load()
        fixture.program.task(state, "S01")["required_checks"].append("native_editor")
        with fixture.program.lock():
            fixture.program.save(state)
    assert fixture.claim() == 0
    path = fixture.receipt()
    receipt = fp.read_json(fixture.program.path(path))
    if fault == "missing_check":
        receipt["checks"].pop()
    elif fault == "missing_evidence":
        receipt["checks"][0]["evidence"] = ["evidence/S01/attempt-1/missing.txt"]
    elif fault == "native_compile":
        receipt["checks"][-1]["execution_kind"] = "compile"
    elif fault == "acceptance":
        receipt["unresolved_acceptance"] = ["Unverified criterion"]
    else:
        receipt["changed_paths"] = ["src/S02.py"]
    fixture.write(path, receipt)
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", path) == 1
    assert fixture.program.task(fixture.program.load(), "S01")["status"] == "active"


@pytest.mark.parametrize("drift", ["source", "receipt", "evidence", "review_binding"])
def test_frozen_submission_rejects_drift(fixture: Fixture, drift: str) -> None:
    assert fixture.claim() == 0
    receipt = fixture.submit()
    review = fixture.review()
    if drift == "source":
        (fixture.program.repo / "src/S01.py").write_text("stale = True\n", encoding="utf-8")
    elif drift == "receipt":
        fixture.program.path(receipt).write_text("{}", encoding="utf-8")
    elif drift == "evidence":
        fixture.program.path("evidence/S01/attempt-1/verification-1.txt").write_text(
            "tampered", encoding="utf-8"
        )
    else:
        value = fp.read_json(fixture.program.path(review))
        value["source_digest"] = "0" * 64
        fixture.write(review, value)
    assert fixture.run("complete", "S01", "--review", review) == 1
    persisted = fp.read_json(fixture.program.path("backlog.json"))
    assert fixture.program.task(persisted, "S01")["status"] == "review"


def test_missing_directory_scope_counts_only_new_files(fixture: Fixture) -> None:
    assert fixture.claim(scope="newdir") == 0
    target = fixture.program.repo / "newdir/file.py"
    target.parent.mkdir()
    target.write_text("created = True\n", encoding="utf-8")
    receipt = fixture.receipt()
    assert fp.read_json(fixture.program.path(receipt))["changed_paths"] == ["newdir/file.py"]
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", receipt) == 0


def test_review_completion_remains_historical_but_evidence_unlocks_dependencies(
    fixture: Fixture,
) -> None:
    state = fixture.program.load()
    fixture.program.task(state, "S02")["depends_on"] = ["S01"]
    with fixture.program.lock():
        fixture.program.save(state)
    assert fixture.claim() == 0
    fixture.submit()
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    (fixture.program.repo / "src/S01.py").write_text(
        "later_authorized_work = True\n", encoding="utf-8"
    )
    assert fixture.run("check") == 0
    fixture.program.path("evidence/S01/attempt-1/verification-1.txt").unlink()
    assert fixture.claim("S02", owner="other") == 1


def test_rejected_review_rework_and_second_round_preserve_frozen_history(fixture: Fixture) -> None:
    assert fixture.claim() == 0
    receipt = fixture.submit()
    original = fixture.program.path(receipt).read_bytes()
    rejection = fixture.review(accepted=False)
    assert fixture.run("rework", "S01", "--review", rejection) == 0
    assert fixture.program.task(fixture.program.load(), "S01")["status"] == "active"
    (fixture.program.repo / "src/S01.py").write_text("corrected = True\n", encoding="utf-8")
    fixture.submit(round_number=2)
    accepted = fixture.review(round_number=2)
    assert fixture.run("complete", "S01", "--review", accepted) == 0
    task = fixture.program.task(fixture.program.load(), "S01")
    rounds = task["attempts"][0]["rounds"]
    assert len(rounds) == 2
    assert rounds[0]["review"]["path"].endswith("review-1.json")
    assert rounds[1]["review"]["path"].endswith("review-2.json")
    assert fixture.program.path(receipt).read_bytes() == original
    assert fixture.run("check") == 0


def test_context_free_checkpoint_and_packet_recovery(
    fixture: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fixture.claim() == 0
    assert (
        fixture.run(
            "checkpoint",
            "--note",
            "Fixture handoff",
            "--next-action",
            "Resume from packet",
            "--team-state",
            ".omo/teams/fixture/team.json",
        )
        == 0
    )
    checkpoint = fixture.program.path("CHECKPOINT.md").read_text(encoding="utf-8")
    assert (
        HEAD in checkpoint
        and "worker, attempt 1" in checkpoint
        and "Resume from packet" in checkpoint
    )
    capsys.readouterr()
    assert fixture.run("packet", "S01") == 0
    packet = capsys.readouterr().out
    assert "Acceptance\r\n  criterion" in packet
    assert "Original relative evidence reference base" in packet
    assert "source_digest" not in packet or "baseline_digest" in packet
    assert fixture.run("check") == 0


@pytest.mark.parametrize(
    "scope",
    [
        ".",
        "../outside",
        ".env.local",
        ".aws/credentials",
        "Assets/Resources/GameData/private.json",
        "program",
    ],
)
def test_prohibited_scopes_rejected_before_content_reads(fixture: Fixture, scope: str) -> None:
    with pytest.raises(fp.ProgramError):
        fixture.program.source_snapshot([scope])


def test_symlink_scope_and_evidence_rejected(fixture: Fixture) -> None:
    link = fixture.program.repo / "linked.py"
    try:
        link.symlink_to(fixture.program.repo / "src/S01.py")
    except OSError:
        pytest.skip("Host cannot create symlinks")
    with pytest.raises(fp.ProgramError, match="Linked"):
        fixture.program.source_snapshot(["linked.py"])


def test_derived_drift_is_recoverable_without_losing_registry(fixture: Fixture) -> None:
    assert fixture.claim() == 0
    state = fixture.program.path("backlog.json").read_bytes()
    fixture.program.path("tasks/S01.md").write_text("interrupted view", encoding="utf-8")
    assert fixture.run("check") == 1
    assert fixture.run("render") == 0
    assert state == fixture.program.path("backlog.json").read_bytes()
    assert fixture.run("check") == 0


def test_atomic_write_failure_preserves_previous_bytes(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = fixture.program.path("backlog.json")
    before = target.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("Fixture replacement failure")

    monkeypatch.setattr(fp.os, "replace", fail_replace)
    with pytest.raises(OSError):
        fp.atomic_write(target, b"replacement")
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".backlog.json.*"))


def test_save_preflights_derived_document_budget_before_registry_write(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an unchanged canonical registry and oversized generated output.
    before = fixture.program.path("backlog.json").read_bytes()
    state = fixture.program.load()

    def oversized(_program: fp.Program, _state: fp.Record) -> dict[str, str]:
        return {"BOARD.md": "x" * (8 * 1024 * 1024 + 1)}

    monkeypatch.setattr(fp.Program, "views", oversized)
    # When a save tries to publish beyond the document budget.
    with pytest.raises(fp.ProgramError, match="Derived document exceeds"):
        fixture.program.save(state)
    # Then no canonical state was replaced.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_save_rejects_oversized_registry_before_publication(fixture: Fixture) -> None:
    # Given valid existing history and an oversized pending runtime observation.
    before = fixture.program.path("backlog.json").read_bytes()
    state = fixture.program.load()
    fixture.program.event(state, "observation", {"reason": "x" * (8 * 1024 * 1024)})
    # When a save exceeds the canonical registry byte limit.
    with pytest.raises(fp.ProgramError, match="Canonical registry exceeds"):
        fixture.program.save(state)
    # Then the original bounded registry remains intact.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_cross_process_lock_contention_cannot_lose_writes(fixture: Fixture) -> None:
    script = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(TOOLS)!r}); import feature_program as fp; "
        f"p=fp.Program(Path({str(fixture.program.repo)!r}), Path({str(fixture.program.directory)!r})); "
        "\nwith p.lock():\n print('locked', flush=True)\n input()\n"
    )
    before = fixture.program.path("backlog.json").read_bytes()
    with subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout.readline().strip() == "locked"
        assert fixture.run("checkpoint", "--note", "Contended", "--next-action", "Wait") == 1
        assert fixture.program.path("backlog.json").read_bytes() == before
        assert fixture.program.path(".feature-program.lock").exists()
        process.communicate("release\n", timeout=10)
        assert process.returncode == 0
    assert fixture.run("checkpoint", "--note", "Acquired", "--next-action", "Continue") == 0
    assert fixture.run("check") == 0


def test_template_helpers_fail_closed_until_evidence_is_completed(
    fixture: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert fixture.run("template", "S01", "--kind", "revalidation", "--scope", "src/S01.py") == 0
    template = json.loads(capsys.readouterr().out)
    assert template["head"] == HEAD and template["acceptance_reviewed"] is False
    assert fixture.claim() == 0
    capsys.readouterr()
    assert fixture.run("template", "S01", "--kind", "execution") == 0
    template = json.loads(capsys.readouterr().out)
    assert all(check["status"] == "not_run" for check in template["checks"])


def test_next_excludes_active_conflicts_and_requires_revalidation(
    fixture: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    state = fixture.program.load()
    fixture.program.task(state, "S02")["candidate_paths"] = ["src"]
    fixture.program.task(state, "S03")["depends_on"] = ["S01"]
    with fixture.program.lock():
        fixture.program.save(state)
    assert fixture.claim() == 0
    capsys.readouterr()
    assert fixture.run("next") == 0
    candidates = json.loads(capsys.readouterr().out)["candidates"]
    assert {item["id"] for item in candidates}.isdisjoint({"S01", "S02", "S03"})
    assert all(item["requires_revalidation"] for item in candidates)


def test_queue_preserves_legacy_array_ties_until_explicit_priority_activation(
    fixture: Fixture,
) -> None:
    # Given equal phases and intentionally reversed task insertion order.
    state = fixture.program.load()
    state["tasks"] = list(reversed(fp.sequence(state["tasks"], "tasks")))
    legacy_ids = [fp.record(task, "task")["id"] for task in state["tasks"]]
    # When legacy and explicitly activated neutral-priority queues are read.
    legacy = fixture.program.status(state, True)["candidates"]
    state["execution_priorities"] = {}
    activated = fixture.program.status(state, True)["candidates"]
    # Then legacy array order is retained and explicit ties use portable IDs.
    assert [task["id"] for task in legacy] == legacy_ids
    assert [task["id"] for task in activated] == sorted(legacy_ids)
    assert all(task["execution_priority"] is None for task in activated)


def test_leader_reopen_preserves_completed_attempt_and_requires_fresh_claim(
    fixture: Fixture,
) -> None:
    assert fixture.claim() == 0
    receipt = fixture.submit()
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    original = fixture.program.path(receipt).read_bytes()
    assert (
        fixture.run(
            "reopen",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Regression",
            "--next-action",
            "Revalidate",
        )
        == 1
    )
    assert (
        fixture.run(
            "reopen",
            "S01",
            "--owner",
            "leader",
            "--reason",
            "CI found regression",
            "--next-action",
            "Revalidate regression",
        )
        == 0
    )
    assert fixture.claim(number=2) == 0
    task = fixture.program.task(fixture.program.load(), "S01")
    assert [attempt["status"] for attempt in task["attempts"]] == ["done", "active"]
    assert task["attempts"][0]["review"]["path"] == review
    assert fixture.program.path(receipt).read_bytes() == original
    assert fixture.run("check") == 0


@pytest.mark.parametrize(
    "conclusion,expected",
    [
        ("pending", "ci_pending"),
        ("failure", "ci_failed"),
        ("skipped", "ci_failed"),
        ("success", "ci_recorded_success"),
    ],
)
def test_delivery_remains_separate_caller_recorded_ci_metadata(
    fixture: Fixture, conclusion: str, expected: str
) -> None:
    assert fixture.claim() == 0
    fixture.submit()
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    attempt = fixture.program.current_attempt(fixture.program.task(fixture.program.load(), "S01"))
    receipt = fixture.write(
        "evidence/S01/attempt-1/delivery.json",
        {
            "task_id": "S01",
            "attempt": 1,
            "branch": "work/fixture",
            "base": "base",
            "commits": [HEAD],
            "pr_url": "https://example.invalid/pr/1",
            "tested_commit": HEAD,
            "reviewed_source_digest": fp.record(attempt["submission"], "submission")[
                "source_digest"
            ],
            "required_ci": ["tests"],
            "ci": [
                {
                    "name": "tests",
                    "url": "https://example.invalid/jobs/1",
                    "commit": HEAD,
                    "conclusion": conclusion,
                }
            ],
            "merge_status": "open",
            "evidence": [fixture.evidence("S01", label="ci-output")],
        },
    )
    assert fixture.run("delivery", "S01", "--receipt", receipt) == 0
    task = fixture.program.task(fixture.program.load(), "S01")
    assert task["status"] == "done"
    assert task["deliveries"][-1]["status"] == expected
    assert task["deliveries"][-1]["remote_verified_by_tool"] is False
    assert fixture.run("check") == 0


@pytest.mark.parametrize("fault", ["wrong_commit", "wrong_reviewed_source", "missing_ci_check"])
def test_delivery_rejects_commit_source_or_ci_coverage_mismatches(
    fixture: Fixture, fault: str
) -> None:
    assert fixture.claim() == 0
    fixture.submit()
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    args = argparse.Namespace(id="S01", kind="delivery")
    template = fixture.program.template(fixture.program.load(), args)
    template["evidence"] = [fixture.evidence("S01", label="ci-output")]
    if fault == "wrong_commit":
        template["ci"][0]["commit"] = "f" * 40
    elif fault == "wrong_reviewed_source":
        template["reviewed_source_digest"] = "0" * 64
    else:
        template["required_ci"].append("missing-required-job")
    receipt = fixture.write("evidence/S01/attempt-1/delivery.json", template)
    assert fixture.run("delivery", "S01", "--receipt", receipt) == 1
    assert fixture.program.task(fixture.program.load(), "S01").get("deliveries") is None


def test_source_and_section_drift_report_without_resetting_progress(fixture: Fixture) -> None:
    assert fixture.claim() == 0
    before = fixture.program.path("backlog.json").read_bytes()
    (fixture.program.repo / "report.md").write_bytes(fixture.report + b"changed\n")
    assert fixture.run("check") == 1
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_duplicate_json_keys_rejected(fixture: Fixture) -> None:
    path = fixture.program.path("duplicate.json")
    path.write_text('{"key": 1, "key": 2}', encoding="utf-8")
    with pytest.raises(fp.ProgramError, match="Duplicate"):
        fp.read_json(path)


def test_reopened_new_completion_cannot_inherit_old_green_delivery(fixture: Fixture) -> None:
    assert fixture.claim() == 0
    fixture.submit()
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    receipt = fixture.program.template(
        fixture.program.load(), argparse.Namespace(id="S01", kind="delivery")
    )
    receipt["ci"][0]["conclusion"] = "success"
    receipt["evidence"] = [fixture.evidence("S01", label="ci-output")]
    path = fixture.write("evidence/S01/attempt-1/delivery.json", receipt)
    assert fixture.run("delivery", "S01", "--receipt", path) == 0
    assert (
        fixture.run(
            "reopen",
            "S01",
            "--owner",
            "leader",
            "--reason",
            "Follow-up acceptance",
            "--next-action",
            "Revalidate",
        )
        == 0
    )
    assert fixture.claim(number=2) == 0
    fixture.submit()
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    state = fixture.program.load()
    task = fixture.program.task(state, "S01")
    assert task["deliveries"][0]["metadata"]["attempt"] == 1
    delivery = fixture.program.status(state)["delivery"][0]["latest"]
    assert delivery["status"] == "unrecorded"


def test_canonical_publication_survives_interrupted_render(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert fixture.claim() == 0

    def fail_render(_program: fp.Program, _state: fp.Record) -> None:
        raise OSError("Fixture render interruption after registry publication")

    with monkeypatch.context() as scoped:
        scoped.setattr(fp.Program, "render", fail_render)
        assert (
            fixture.run(
                "note",
                "S01",
                "--owner",
                "worker",
                "--reason",
                "Durable progress",
                "--next-action",
                "Recover views",
            )
            == 1
        )
    state = fixture.program.load()
    task = fixture.program.task(state, "S01")
    assert task["notes"][-1]["reason"] == "Durable progress"
    assert len(task["attempts"]) == 1
    assert fixture.run("render") == 0
    assert fixture.run("check") == 0


def test_checkpoint_preserves_first_dirty_path_and_rename_names(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fp,
        "git",
        lambda _root, args: (
            " M src/S01.py\0R  new name.py\0old name.py\0" if args[0] == "status" else HEAD
        ),
    )
    assert fixture.run("checkpoint", "--note", "Dirty names", "--next-action", "Inspect paths") == 0
    checkpoint = fp.record(fixture.program.load()["checkpoint"], "checkpoint")
    assert checkpoint["dirty_paths"] == ["new name.py", "old name.py", "src/S01.py"]


def make_small_program(repo: Path, name: str, ids: tuple[str, ...]) -> Fixture:
    repo.mkdir(parents=True, exist_ok=True)
    program = fp.Program(repo, repo / name)
    program.directory.mkdir()
    report = "# Synthetic non-Unity work\n\n" + "".join(
        f"### {task_id}. Synthetic {task_id}\n\nGoal: verify a bounded non-Unity change.\nAcceptance\n  criteria: preserve exact source text.\n\n"
        for task_id in ids
    )
    path = program.path("source/feature-opportunities.md")
    path.parent.mkdir()
    path.write_bytes(report.encode())
    plan = {
        "schema_version": 1,
        "program_id": name,
        "baseline_head": HEAD,
        "workstreams": [
            {
                "id": "operations",
                "name": "Operations",
                "default_agent": "worker",
                "focus": "Synthetic workflow",
            }
        ],
        "tasks": [
            {
                "id": task_id,
                "workstream": "operations",
                "phase": index + 7,
                "depends_on": [],
                "related_to": [],
                "conflict_groups": [],
                "candidate_paths": [f"src/{task_id}.py"],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Inspect synthetic source",
                "planning_note": "Not a completed feature",
            }
            for index, task_id in enumerate(ids)
        ],
    }
    program.path("planning.json").write_bytes(fp.canonical(plan))
    (repo / "src").mkdir(exist_ok=True)
    for task_id in ids:
        (repo / "src" / f"{task_id}.py").write_text("original = True\n", encoding="utf-8")
    result = Fixture(program, report.encode())
    assert result.run("init", "--review-baseline", HEAD) == 0
    return result


@pytest.fixture
def small_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    monkeypatch.setattr(
        fp, "git", lambda _root, arguments: "" if arguments[0] == "status" else HEAD
    )
    return make_small_program(
        tmp_path / "repository", "small-program", ("compile-api", "docs.2", "test_9")
    )


def test_generic_small_program_arbitrary_identity_phases_and_commands(
    small_program: Fixture,
) -> None:
    state = small_program.program.load()
    assert state["program_id"] == "small-program"
    assert [task["id"] for task in state["tasks"]] == ["compile-api", "docs.2", "test_9"]
    assert [task["phase"] for task in state["tasks"]] == [7, 8, 9]
    assert small_program.claim("compile-api") == 0
    assert small_program.run("packet", "compile-api") == 0
    assert small_program.run("packet", "not-defined") == 1
    assert small_program.run("check") == 0


def test_independent_programs_in_one_repository_preserve_ownership(small_program: Fixture) -> None:
    other = make_small_program(small_program.program.repo, "second-program", ("compile-api",))
    assert small_program.claim("compile-api", owner="first-worker") == 0
    assert other.program.task(other.program.load(), "compile-api")["status"] == "planned"
    assert other.claim("compile-api", owner="second-worker") == 0
    assert (
        small_program.program.task(small_program.program.load(), "compile-api")["owner"]
        == "first-worker"
    )
    assert other.program.task(other.program.load(), "compile-api")["owner"] == "second-worker"


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "case_collision", "empty"])
def test_generic_source_plan_exact_set_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    repo = tmp_path / "repository"
    program = fp.Program(repo, repo / "program")
    program.directory.mkdir(parents=True)
    report = b"### TASK-1. Synthetic task\n\nEntire original acceptance.\n"
    program.path("source.md").write_bytes(report)
    ids = ["TASK-1"]
    if fault == "missing":
        ids = ["TASK-2"]
    elif fault == "extra":
        ids.append("TASK-2")
    elif fault == "duplicate":
        ids.append("TASK-1")
    elif fault == "case_collision":
        program.path("source.md").write_bytes(report + b"### task-1. Case collision\n")
        ids.append("task-1")
    else:
        ids = []
    plan = {
        "schema_version": 1,
        "program_id": "arbitrary",
        "tasks": [{"id": task_id} for task_id in ids],
    }
    program.path("planning.json").write_bytes(fp.canonical(plan))
    fixture = Fixture(program, report)
    assert (
        fixture.run(
            "init",
            "--report",
            "program/source.md",
            "--planning",
            "program/planning.json",
            "--review-baseline",
            HEAD,
        )
        == 1
    )
    assert not program.path("backlog.json").exists()


@pytest.mark.parametrize("state_kind", ["active", "completed", "reworked", "delivery"])
def test_legacy_v1_history_load_is_non_destructive(fixture: Fixture, state_kind: str) -> None:
    assert fixture.claim() == 0
    if state_kind != "active":
        fixture.submit()
        if state_kind == "reworked":
            assert fixture.run("rework", "S01", "--review", fixture.review(accepted=False)) == 0
        else:
            assert fixture.run("complete", "S01", "--review", fixture.review()) == 0
    if state_kind == "delivery":
        receipt = fixture.program.template(
            fixture.program.load(), argparse.Namespace(id="S01", kind="delivery")
        )
        receipt["evidence"] = [fixture.evidence("S01", label="ci-output")]
        path = fixture.write("evidence/S01/attempt-1/delivery.json", receipt)
        assert fixture.run("delivery", "S01", "--receipt", path) == 0
    state = fixture.program.load()
    state["source"].pop("task_ids")
    state.pop("verification_policy_sha256")
    with fixture.program.lock():
        fixture.program.save(state)
    before = fixture.program.path("backlog.json").read_bytes()
    loaded = fixture.program.load()
    assert loaded == state and loaded["schema_version"] == 1
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert fixture.run("check") == 0
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_snapshot_read_only_no_locks_git_or_writes(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {
        path.relative_to(small_program.program.directory).as_posix(): path.read_bytes()
        for path in small_program.program.directory.rglob("*")
        if path.is_file()
    }

    def forbidden(*_args: fp.Json) -> None:
        raise AssertionError("Snapshot attempted mutation or Git access")

    monkeypatch.setattr(fp, "git", forbidden)
    monkeypatch.setattr(fp, "atomic_write", forbidden)
    snapshot = fp.read_snapshot(small_program.program.repo, small_program.program.directory)
    assert snapshot["snapshot_version"] == 1 and snapshot["total_tasks"] == 3
    assert snapshot["integrity"]["status"] == "fresh"
    assert snapshot["counts"]["planned"] == 3 and snapshot["counts"]["done"] == 0
    assert snapshot["tasks"][0]["source"]["text"].encode() in small_program.report
    assert snapshot["tasks"][0]["delivery"]["status"] == "unrecorded"
    assert not small_program.program.path(".feature-program.lock").exists()
    after = {
        path.relative_to(small_program.program.directory).as_posix(): path.read_bytes()
        for path in small_program.program.directory.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("fault", ["report", "view", "evidence", "pending_source"])
def test_snapshot_distinguishes_recorded_context_from_stale_artifacts(
    small_program: Fixture, fault: str
) -> None:
    if fault in {"evidence", "pending_source"}:
        assert small_program.claim("compile-api") == 0
        small_program.submit("compile-api")
    if fault == "report":
        small_program.program.path("source/feature-opportunities.md").write_bytes(b"Changed source")
    elif fault == "view":
        small_program.program.path("BOARD.md").write_bytes(b"Interrupted render")
    elif fault == "evidence":
        small_program.program.path("evidence/compile-api/attempt-1/verification-1.txt").unlink()
    else:
        (small_program.program.repo / "src/compile-api.py").write_bytes(b"changed source\n")
    snapshot = fp.read_snapshot(small_program.program.repo, small_program.program.directory)
    assert snapshot["integrity"]["status"] == "stale"
    assert snapshot["integrity"]["warnings"]
    assert snapshot["tasks"][0]["title"] == "Synthetic compile-api"


def test_snapshot_missing_or_unsafe_program_never_creates_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repository"
    repo.mkdir()
    missing = repo / "not-created"
    with pytest.raises(fp.ProgramError):
        fp.read_snapshot(repo, missing)
    assert not missing.exists()
    with pytest.raises(fp.ProgramError):
        fp.read_snapshot(repo, tmp_path / "outside")


def test_starter_creates_portable_pending_material_without_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repository"
    repo.mkdir()
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    args = [
        "--repo",
        str(repo),
        "--program-dir",
        ".omo/example",
        "starter",
        "--program-id",
        "sample",
        "--language",
        "en-GB",
        "--objective",
        "Synthetic human objective",
        "--criterion",
        "Synthetic verified criterion",
        "--task",
        "build-api",
        "--task",
        "review-api",
    ]
    assert fp.main(args) == 0
    program = fp.Program(repo, repo / ".omo/example")
    assert not program.path("backlog.json").exists()
    assert not program.path("evidence").exists()
    for name in (
        "README.md",
        "DIRECTOR.md",
        "SUBDIRECTOR.md",
        "WORKER.md",
        "REVIEW.md",
        "EVIDENCE.md",
        "DELIVERY.md",
        "DECISIONS.md",
        "DESIGN.md",
    ):
        assert program.path(name).is_file()
    plan = fp.read_json(program.path("planning.json"))
    assert plan["objective"]["title"] == "Synthetic human objective"
    assert plan["communication_language"] == "en-GB"
    assert plan["objective"]["status"] == "active"
    assert plan["objective"]["next_action_owner"] == "director"
    assert fp.main(args) == 1
    assert (
        fp.main(
            [
                "--repo",
                str(repo),
                "--program-dir",
                ".omo/example",
                "init",
                "--review-baseline",
                HEAD,
            ]
        )
        == 0
    )
    assert program.load()["program_id"] == "sample"
    assert program.load()["communication_language"] == "en-GB"


def test_generic_custom_verification_catalog_is_explicit_and_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repository"
    repo.mkdir()
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    args = [
        "--repo",
        str(repo),
        "--program-dir",
        ".omo/editorial",
        "starter",
        "--program-id",
        "editorial",
        "--objective",
        "Synthetic editorial goal",
        "--criterion",
        "Reviewed document",
        "--task",
        "review-document",
    ]
    assert fp.main(args) == 0
    program = fp.Program(repo, repo / ".omo/editorial")
    plan = fp.read_json(program.path("planning.json"))
    plan["check_catalog"] = ["acceptance", "editorial-review"]
    plan["minimum_checks"] = ["acceptance"]
    plan["tasks"][0]["required_checks"] = ["acceptance", "editorial-review"]
    program.path("planning.json").write_bytes(fp.canonical(plan))
    fixture = Fixture(program, program.path("source/feature-opportunities.md").read_bytes())
    assert fixture.run("init", "--review-baseline", HEAD) == 0
    state = program.load()
    assert state["check_catalog"] == ["acceptance", "editorial-review"]
    assert state["verification_policy_sha256"]
    (repo / "article.md").write_text("Synthetic article.\n", encoding="utf-8")
    assert fixture.claim("review-document", scope="article.md") == 0
    receipt = fixture.receipt("review-document")
    assert fixture.run("submit", "review-document", "--owner", "worker", "--receipt", receipt) == 0
    changed = program.load()
    changed["minimum_checks"] = []
    with pytest.raises(fp.ProgramError):
        program.validate(changed)


def test_snapshot_budget_rejects_oversized_state_without_truncation(small_program: Fixture) -> None:
    path = small_program.program.path("backlog.json")
    oversized = b" " * (8 * 1024 * 1024 + 1)
    path.write_bytes(oversized)
    with pytest.raises(fp.ProgramError, match="registry_size"):
        fp.read_snapshot(small_program.program.repo, small_program.program.directory)
    assert path.read_bytes() == oversized


def test_assignment_settings_are_frozen_at_claim_and_legacy_is_unknown(
    small_program: Fixture,
) -> None:
    state = small_program.program.load()
    state["actors"] = [
        {
            "id": "worker",
            "role": "worker",
            "parent_id": None,
            "model_id": "fixture-model-v1",
            "reasoning_effort": "high",
            "provenance": "verified_launch",
            "observed_status": "unknown",
            "observed_at": None,
            "staffing_status": "active",
            "history": [],
        }
    ]
    with small_program.program.lock():
        small_program.program.save(state)
    assert small_program.claim("compile-api") == 0
    state = small_program.program.load()
    attempt = small_program.program.current_attempt(
        small_program.program.task(state, "compile-api")
    )
    assert attempt["assignment"]["model_id"] == "fixture-model-v1"
    state["actors"][0]["model_id"] = "fixture-model-v2"
    with small_program.program.lock():
        small_program.program.save(state)
    snapshot = fp.read_snapshot(small_program.program.repo, small_program.program.directory)
    assert snapshot["tasks"][0]["attempts"][0]["assignment"]["model_id"] == "fixture-model-v1"
    state = small_program.program.load()
    small_program.program.current_attempt(small_program.program.task(state, "compile-api")).pop(
        "assignment"
    )
    with small_program.program.lock():
        small_program.program.save(state)
    legacy = fp.read_snapshot(small_program.program.repo, small_program.program.directory)
    assert legacy["tasks"][0]["attempts"][0]["assignment"]["model_id"] is None
    assert legacy["tasks"][0]["attempts"][0]["assignment"]["recorded_at"] is None
    assert legacy["tasks"][0]["attempts"][0]["assignment"]["provenance"] == "unknown"


def test_explicit_absolute_program_root_stays_within_repository(small_program: Fixture) -> None:
    assert (
        fp.main(
            [
                "--repo",
                str(small_program.program.repo),
                "--program-dir",
                str(small_program.program.directory),
                "check",
            ]
        )
        == 0
    )
    outside = small_program.program.repo.parent / "outside"
    assert (
        fp.main(
            ["--repo", str(small_program.program.repo), "--program-dir", str(outside), "status"]
        )
        == 1
    )
    assert not outside.exists()


def test_snapshot_rejects_tampered_frozen_prose_even_with_updated_section_hash(
    small_program: Fixture,
) -> None:
    state = small_program.program.load()
    source = state["tasks"][0]["source"]
    source["text"] += "Injected acceptance prose.\n"
    source["sha256"] = fp.sha(source["text"].encode())
    small_program.program.path("backlog.json").write_bytes(fp.canonical(state))
    with pytest.raises(fp.ProgramError, match="Frozen source sections"):
        fp.read_snapshot(small_program.program.repo, small_program.program.directory)


@pytest.mark.parametrize("artifact", ["original_source", "frozen_evidence"])
def test_load_bounds_actual_reads_without_read_bytes(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    if artifact == "frozen_evidence":
        assert small_program.claim("compile-api") == 0
        small_program.submit("compile-api")
        assert (
            small_program.run(
                "complete", "compile-api", "--review", small_program.review("compile-api")
            )
            == 0
        )
        target = small_program.program.path("evidence/compile-api/attempt-1/verification-1.txt")
    else:
        target = small_program.program.path("source/feature-opportunities.md")
    target.write_bytes(b"x" * (8 * 1024 * 1024 + 1))

    def unbounded_read(_path: Path) -> bytes:
        raise AssertionError("An unbounded read_bytes call was attempted")

    monkeypatch.setattr(Path, "read_bytes", unbounded_read)
    with pytest.raises(fp.ProgramError, match="bounded read"):
        small_program.program.load()


def test_evidence_growth_after_stat_is_still_bounded(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = small_program.evidence("compile-api")
    target = small_program.program.path(name)
    original_open = Path.open
    grew = False

    def grow_then_open(path: Path, *args, **kwargs):
        nonlocal grew
        if path == target and args and args[0] == "rb" and not grew:
            grew = True
            with original_open(path, "wb") as stream:
                stream.write(b"x" * (8 * 1024 * 1024 + 1))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", grow_then_open)
    with pytest.raises(fp.ProgramError, match="bounded read"):
        small_program.program.evidence("compile-api", 1, [name])


@pytest.mark.parametrize("limit", [0, 7, 65536, 65537])
def test_bounded_reader_preserves_short_reads_and_exact_limit(tmp_path, monkeypatch, limit):
    # Given a stream that may return fewer bytes than requested without reaching EOF.
    payload = b"x" * limit

    class ShortReader(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(min(size, 3))

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: ShortReader(payload))
    # When the file is captured with its exact byte limit.
    content = fp.bounded_bytes(tmp_path / "short.bin", limit)
    # Then all bytes survive, including after short reads.
    assert content == payload


@pytest.mark.parametrize("limit", [0, 7, 65536, 65537])
def test_bounded_reader_detects_growth_after_a_short_read(tmp_path, monkeypatch, limit):
    # Given a stream that exposes an additional byte after the original bounded contents.
    payload = b"x" * limit

    class GrowingReader(BytesIO):
        grown = False

        def read(self, size=-1):
            assert 0 < size <= 65536
            if not self.grown and self.tell() >= min(limit, 3):
                position = self.tell()
                self.seek(0, 2)
                self.write(b"x")
                self.seek(position)
                self.grown = True
            return super().read(min(size, 3))

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: GrowingReader(payload))
    # When the capture reaches its byte limit and probes the overflow sentinel.
    # Then it rejects growth rather than returning an incomplete successful capture.
    with pytest.raises(fp.ProgramError, match="bounded read"):
        fp.bounded_bytes(tmp_path / "growing.bin", limit)


def test_read_budget_tracks_actual_bytes_and_rejects_aggregate_overflow(tmp_path):
    # Given two individually valid files whose combined contents exceed the ledger.
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"abc")
    second.write_bytes(b"def")
    budget = fp.ReadBudget(remaining=5)
    # When the first capture is reused and the second file is read.
    assert budget.read(first, 3) == budget.read(first, 3) == b"abc"
    # Then cached reads do not consume the ledger twice and excess bytes are rejected.
    assert budget.remaining == 2
    with pytest.raises(fp.ProgramError, match="bounded read"):
        budget.read(second, 3)


@pytest.mark.parametrize("owned_status", ["active", "blocked", "review"])
def test_request_local_conflicts_match_original_directional_semantics(owned_status):
    # Given exact, ancestor, near-prefix, Unicode, and unnormalized lexical scope variants.
    paths = [
        "src",
        "SRC/a",
        "src/a/",
        "src//a",
        "src/ab",
        "src/a.py",
        "other",
        "Straße/a",
        "STRASSE",
        "src/",
        "src///a",
    ]
    tasks = []
    for position, path in enumerate(paths):
        for status in ("planned", owned_status, "done"):
            tasks.append(
                {
                    "id": f"task-{len(tasks)}",
                    "status": status,
                    "conflict_groups": ["shared" if position % 2 else "SHARED"],
                    "candidate_paths": [path, "planned-only"],
                    "attempts": [{"scope": [path]}],
                }
            )
    original = deepcopy(tasks)
    # When one request-local index projects all owned conflicts.
    index = fp._TaskIndex.capture(tasks)
    actual = {task["id"]: index.conflicts(task["id"]) for task in tasks}
    # Then it preserves original case/group/path semantics, orientation and task order.
    for task in tasks:
        expected = []
        for other in tasks:
            if other["status"] in fp.OWNED_STATES and other["id"] != task["id"]:
                reasons = fp.Program.collisions(task, other)
                if reasons:
                    expected.append({"id": other["id"], "reasons": reasons})
        assert actual[task["id"]] == expected
    assert tasks == original


def test_status_rebuilds_scopes_and_membership_for_each_captured_state(small_program):
    # Given an owned task and a planned task with a case-insensitive scope collision.
    assert small_program.claim("compile-api") == 0
    state = small_program.program.load()
    planned = small_program.program.task(state, "docs.2")
    planned["candidate_paths"] = ["SRC/COMPILE-API.py"]
    status = small_program.program.status(state)
    row = next(item for item in status["candidates"] if item["id"] == "docs.2")
    assert row["scope_conflicts"][0]["id"] == "compile-api"
    # When the next call sees a different scope and then different objective membership.
    planned["candidate_paths"] = ["src/docs.2.py"]
    refreshed = small_program.program.status(state)
    state["objective"] = {"membership_revision": 1, "task_ids": ["test_9"]}
    selected = small_program.program.status(state)
    # Then no index survives across calls and the existing public task error is preserved.
    assert not next(item for item in refreshed["candidates"] if item["id"] == "docs.2")[
        "scope_conflicts"
    ]
    assert [item["id"] for item in selected["candidates"]] == ["test_9"]
    with pytest.raises(fp.ProgramError, match="Unknown task: absent"):
        small_program.program.task(state, "absent")


def test_oversized_canonical_save_preserves_previous_registry(small_program: Fixture) -> None:
    path = small_program.program.path("backlog.json")
    before = path.read_bytes()
    state = small_program.program.load()
    state["events"].append({"action": "synthetic-large-event", "reason": "x" * (8 * 1024 * 1024)})
    with (
        small_program.program.lock(),
        pytest.raises(fp.ProgramError, match="Canonical registry exceeds"),
    ):
        small_program.program.save(state)
    assert path.read_bytes() == before


@pytest.mark.parametrize("reverse,cycle", [(False, False), (True, False), (True, True)])
def test_deep_generic_dependency_graph_has_no_recursion_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool, cycle: bool
) -> None:
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    repo = tmp_path / "repository"
    program = fp.Program(repo, repo / "program")
    program.directory.mkdir(parents=True)
    numbers = list(range(1100))
    if reverse:
        numbers.reverse()
    program.path("source.md").write_text(
        "".join(
            f"### TASK-{number}. Synthetic task\n\nAcceptance remains explicit.\n\n"
            for number in numbers
        ),
        encoding="utf-8",
    )
    plan = {
        "schema_version": 1,
        "program_id": "deep-chain",
        "baseline_head": HEAD,
        "workstreams": [
            {
                "id": "chain",
                "name": "Chain",
                "default_agent": "worker",
                "focus": "Synthetic dependency order",
            }
        ],
        "tasks": [
            {
                "id": f"TASK-{number}",
                "workstream": "chain",
                "phase": 1,
                "depends_on": [f"TASK-{number - 1}"]
                if number
                else (["TASK-1099"] if cycle else []),
                "related_to": [],
                "conflict_groups": [],
                "candidate_paths": [],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Revalidate synthetic task",
            }
            for number in numbers
        ],
    }
    program.path("planning.json").write_bytes(fp.canonical(plan))
    args = argparse.Namespace(
        report="program/source.md",
        planning="program/planning.json",
        review_baseline=HEAD,
        reference_base="docs",
    )
    if cycle:
        with pytest.raises(fp.ProgramError, match="cycle"):
            program.initialize(args)
    else:
        assert len(program.initialize(args)["tasks"]) == 1100
    assert not program.path("backlog.json").exists()


def test_revalidation_metadata_must_match_frozen_bytes_before_claim_publication(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = small_program.validation("compile-api", ["src/compile-api.py"])
    payload = fp.read_json(small_program.program.path(name))
    payload["acceptance_reviewed"] = False
    small_program.write(name, payload)
    original_evidence = fp.Program.evidence
    switched = False

    def switch_after_hash(program: fp.Program, task_id: str, attempt: int, names: list[str]):
        nonlocal switched
        refs = original_evidence(program, task_id, attempt, names)
        if name in names and not switched:
            switched = True
            payload["acceptance_reviewed"] = True
            small_program.write(name, payload)
        return refs

    monkeypatch.setattr(fp.Program, "evidence", switch_after_hash)
    before = small_program.program.path("backlog.json").read_bytes()
    assert (
        small_program.run(
            "claim",
            "compile-api",
            "--owner",
            "worker",
            "--scope",
            "src/compile-api.py",
            "--next-action",
            "Inspect bounded change",
            "--revalidation",
            name,
        )
        == 1
    )
    assert small_program.program.path("backlog.json").read_bytes() == before


def test_revalidation_aba_cannot_admit_different_parsed_metadata(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = small_program.validation("compile-api", ["src/compile-api.py"])
    original = fp.read_json(small_program.program.path(name))
    original["acceptance_reviewed"] = False
    small_program.write(name, original)
    original_evidence = fp.Program.evidence

    def change_and_restore(program: fp.Program, task_id: str, attempt: int, names: list[str]):
        refs = original_evidence(program, task_id, attempt, names)
        if name in names:
            small_program.write(name, {**original, "acceptance_reviewed": True})
        else:
            small_program.write(name, original)
        return refs

    monkeypatch.setattr(fp.Program, "evidence", change_and_restore)
    before = small_program.program.path("backlog.json").read_bytes()
    assert (
        small_program.run(
            "claim",
            "compile-api",
            "--owner",
            "worker",
            "--scope",
            "src/compile-api.py",
            "--next-action",
            "Inspect bounded change",
            "--revalidation",
            name,
        )
        == 1
    )
    assert small_program.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_shared_json_reader_rejects_nonfinite_numbers(tmp_path: Path, value: str) -> None:
    path = tmp_path / "receipt.json"
    path.write_text('{"value":' + value + "}", encoding="utf-8")
    with pytest.raises(fp.ProgramError, match="Nonfinite"):
        fp.read_json_frozen(path)


def test_frozen_json_digest_and_metadata_use_one_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "receipt.json"
    original = b'{"accepted":false}'
    path.write_bytes(original)
    original_reader = fp.read_json

    def rewrite_then_parse(receipt: Path, *args, **kwargs):
        receipt.write_bytes(b'{"accepted":true}')
        return original_reader(receipt, *args, **kwargs)

    monkeypatch.setattr(fp, "read_json", rewrite_then_parse)
    metadata, parsed_sha = fp.read_json_frozen(path)
    assert metadata == {"accepted": False}
    assert parsed_sha == fp.sha(original)


def test_language_roundtrip_unknown_and_attempt_history(small_program: Fixture) -> None:
    program = small_program.program
    assert fp.read_snapshot(program.repo, program.directory)["communication_language"] is None
    original_source = program.path("source/feature-opportunities.md").read_bytes()
    assert (
        small_program.run(
            "language",
            "--value",
            "ko",
            "--owner",
            "leader",
            "--reason",
            "Explicit human session language",
        )
        == 0
    )
    assert small_program.claim("compile-api") == 0
    assert (
        small_program.run(
            "language",
            "--value",
            "en-GB",
            "--owner",
            "leader",
            "--reason",
            "Explicit human language change",
        )
        == 0
    )
    snapshot = fp.read_snapshot(program.repo, program.directory)
    assert snapshot["communication_language"] == "en-GB"
    assert snapshot["tasks"][0]["attempts"][0]["communication_language"] == "ko"
    assert program.path("source/feature-opportunities.md").read_bytes() == original_source
    packet = program.packet(program.load(), program.task(program.load(), "compile-api"))
    assert "communication_language" in packet
    assert "explicit human language change" in packet
    assert [event["action"] for event in program.load()["events"]].count("language") == 2


@pytest.mark.parametrize("tag", ["", "en_US", "../ko", "x" * 64, "12", "en-", "en\nUS"])
def test_language_invalid_tag_preserves_registry(small_program: Fixture, tag: str) -> None:
    path = small_program.program.path("backlog.json")
    before = path.read_bytes()
    assert (
        small_program.run(
            "language", "--value", tag, "--owner", "leader", "--reason", "Explicit fixture choice"
        )
        == 1
    )
    assert path.read_bytes() == before


def test_language_setter_requires_current_recorded_director(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fp.coordination, "current_director_id", lambda _state: "final-director")
    path = small_program.program.path("backlog.json")
    before = path.read_bytes()
    assert (
        small_program.run(
            "language",
            "--value",
            "ko",
            "--owner",
            "leader",
            "--reason",
            "Fixture unauthorized owner",
        )
        == 1
    )
    assert path.read_bytes() == before
    assert (
        small_program.run(
            "language",
            "--value",
            "ko",
            "--owner",
            "final-director",
            "--reason",
            "Explicit fixture language",
        )
        == 0
    )


@pytest.mark.parametrize(
    "task_id", ["CON", "con.txt", "PrN", "AUX.log", "NUL", "COM1", "LPT9.txt", "task."]
)
def test_reserved_task_filenames_reject_before_any_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_id: str
) -> None:
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    with pytest.raises(fp.ProgramError, match="not portable"):
        fp.extract_sections(f"### {task_id}. Synthetic task\n\nAcceptance.\n".encode())
    repo = tmp_path / "repo"
    repo.mkdir()
    assert (
        fp.main(
            [
                "--repo",
                str(repo),
                "--program-dir",
                "program",
                "starter",
                "--program-id",
                "synthetic",
                "--objective",
                "Synthetic goal",
                "--criterion",
                "Synthetic acceptance",
                "--task",
                task_id,
            ]
        )
        == 1
    )
    assert not (repo / "program/backlog.json").exists()
    assert not (repo / "program/planning.json").exists()


def test_snapshot_keeps_frozen_context_when_receipt_drifts_to_invalid_json(
    small_program: Fixture,
) -> None:
    assert small_program.claim("compile-api") == 0
    receipt = small_program.submit("compile-api")
    small_program.program.path(receipt).write_bytes(b"invalid JSON")
    snapshot = fp.read_snapshot(small_program.program.repo, small_program.program.directory)
    assert snapshot["integrity"]["status"] == "stale"
    assert "receipt_record" not in snapshot["tasks"][0]["attempts"][0]["rounds"][0]


def test_neutral_default_and_explicit_legacy_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    assert fp.parser().parse_args(["status"]).program_dir == ".work/programs/default"
    assert (
        fp.parser().parse_args(["--program-dir", ".omo/legacy", "status"]).program_dir
        == ".omo/legacy"
    )
    assert (
        fp.main(
            [
                "--repo",
                str(tmp_path),
                "starter",
                "--program-id",
                "neutral",
                "--objective",
                "Synthetic goal",
                "--criterion",
                "Synthetic acceptance",
            ]
        )
        == 0
    )
    assert (tmp_path / ".work/programs/default/planning.json").is_file()
    assert not (tmp_path / ".omo").exists()


@pytest.mark.parametrize("rebind_reference", [False, True])
def test_relocate_preserves_attempts_source_and_frozen_evidence(
    small_program: Fixture, rebind_reference: bool
) -> None:
    old = small_program.program
    assert small_program.claim("docs.2") == 0
    small_program.submit("docs.2")
    assert small_program.run("complete", "docs.2", "--review", small_program.review("docs.2")) == 0
    assert small_program.claim("compile-api") == 0
    state = old.load()
    if rebind_reference:
        state["source"]["reference_base"] = old.directory.relative_to(old.repo).as_posix()
        with old.lock():
            old.save(state)
    before = old.load()
    original_registry = old.path("backlog.json").read_bytes()
    evidence = {
        path.relative_to(old.directory).as_posix(): path.read_bytes()
        for path in old.path("evidence").rglob("*")
        if path.is_file()
    }
    assert (
        small_program.run(
            "relocate",
            "--destination",
            ".work/programs/moved",
            "--owner",
            "leader",
            "--reason",
            "Explicit neutral workspace relocation",
        )
        == 0
    )
    target = fp.Program(old.repo, old.repo / ".work/programs/moved")
    after = target.load()
    assert after["tasks"] == before["tasks"]
    assert after["events"][:-1] == before["events"]
    assert after["source"]["sha256"] == before["source"]["sha256"]
    assert after["source"]["path"] == ".work/programs/moved/source/feature-opportunities.md"
    assert after["source"]["reference_base"] == (
        ".work/programs/moved" if rebind_reference else "docs/development"
    )
    assert old.path("backlog.json").read_bytes() == original_registry
    assert target.path("source/feature-opportunities.md").read_bytes() == small_program.report
    for name, content in evidence.items():
        assert target.path(name).read_bytes() == content
    with pytest.raises(fp.ProgramError, match="archived"):
        old.load()
    assert (
        small_program.run(
            "note",
            "compile-api",
            "--owner",
            "worker",
            "--reason",
            "Rejected archive write",
            "--next-action",
            "Inspect",
        )
        == 1
    )
    assert fp.read_snapshot(target.repo, target.directory)["integrity"]["status"] == "fresh"
    # A subsequent relocation preserves earlier recovery records as well.
    assert (
        Fixture(target, small_program.report).run(
            "relocate",
            "--destination",
            ".work/programs/second",
            "--owner",
            "leader",
            "--reason",
            "Second explicit relocation",
        )
        == 0
    )
    assert (
        fp.Program(old.repo, old.repo / ".work/programs/second").load()["tasks"] == before["tasks"]
    )


@pytest.mark.parametrize("failure", ["copy", "archive", "activate"])
def test_relocation_failure_is_recoverable_without_reset(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    old = small_program.program
    before = old.path("backlog.json").read_bytes()
    target = fp.Program(old.repo, old.repo / ".work/programs/moved")
    original_write = fp.atomic_write
    original_unlink = Path.unlink

    def fail_write(path: Path, data: bytes):
        if (failure == "copy" and path == target.path("source/feature-opportunities.md")) or (
            failure == "archive" and path == old.path("RELOCATED.json")
        ):
            raise OSError("Synthetic interrupted relocation")
        original_write(path, data)

    def fail_unlink(path: Path, *args, **kwargs):
        if failure == "activate" and path == target.path("RELOCATION_INCOMPLETE.json"):
            raise OSError("Synthetic interrupted activation")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(fp, "atomic_write", fail_write)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    assert (
        small_program.run(
            "relocate",
            "--destination",
            ".work/programs/moved",
            "--owner",
            "leader",
            "--reason",
            "Explicit fixture relocation",
        )
        == 1
    )
    assert old.path("backlog.json").read_bytes() == before
    with pytest.raises(fp.ProgramError, match="incomplete"):
        target.load()
    if failure != "activate":
        assert old.load()["tasks"]
    monkeypatch.setattr(fp, "atomic_write", original_write)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    result = Fixture(target, small_program.report).run(
        "relocate-recover", "--owner", "leader", "--reason", "Inspect verified recovery receipt"
    )
    assert result == (1 if failure == "copy" else 0)
    if failure != "copy":
        assert target.load()["tasks"]


@pytest.mark.parametrize(
    "destination", ["small-program/child", ".", "../outside", ".work/programs/occupied"]
)
def test_relocation_rejects_unsafe_or_occupied_target(
    small_program: Fixture, destination: str
) -> None:
    occupied = small_program.program.repo / ".work/programs/occupied"
    occupied.mkdir(parents=True)
    (occupied / "sentinel.txt").write_bytes(b"preserve")
    before = small_program.program.path("backlog.json").read_bytes()
    assert (
        small_program.run(
            "relocate",
            "--destination",
            destination,
            "--owner",
            "leader",
            "--reason",
            "Rejected fixture target",
        )
        == 1
    )
    assert small_program.program.path("backlog.json").read_bytes() == before
    assert (occupied / "sentinel.txt").read_bytes() == b"preserve"


def test_private_work_fingerprints_skip_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "src/.WoRk/programs/private.txt"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"private")
    (tmp_path / "src/public.py").write_bytes(b"public")
    program = fp.Program(tmp_path, tmp_path / ".work/programs/default")
    original_reader = fp.bounded_bytes

    def reject_private(path: Path, limit: int) -> bytes:
        assert not any(part.casefold() == ".work" for part in path.relative_to(tmp_path).parts)
        return original_reader(path, limit)

    monkeypatch.setattr(fp, "bounded_bytes", reject_private)
    assert set(program.source_snapshot(["src"])) == {"src/", "src/public.py"}
    with pytest.raises(fp.ProgramError, match="Private .work"):
        program.source_snapshot(["src/.WoRk/programs/private.txt"])


@pytest.mark.parametrize("kind", ["reworked", "delivery"])
def test_relocation_preserves_review_rounds_and_delivery(small_program: Fixture, kind: str) -> None:
    assert small_program.claim("compile-api") == 0
    small_program.submit("compile-api")
    review = small_program.review("compile-api", accepted=kind == "delivery")
    assert (
        small_program.run(
            "rework" if kind == "reworked" else "complete", "compile-api", "--review", review
        )
        == 0
    )
    if kind == "delivery":
        receipt = small_program.program.template(
            small_program.program.load(), argparse.Namespace(id="compile-api", kind="delivery")
        )
        receipt["evidence"] = [small_program.evidence("compile-api", label="synthetic-ci")]
        name = small_program.write("evidence/compile-api/attempt-1/delivery.json", receipt)
        assert small_program.run("delivery", "compile-api", "--receipt", name) == 0
    before = small_program.program.load()
    assert (
        small_program.run(
            "relocate",
            "--destination",
            ".work/programs/moved",
            "--owner",
            "leader",
            "--reason",
            "Explicit synthetic migration",
        )
        == 0
    )
    after = fp.Program(
        small_program.program.repo, small_program.program.repo / ".work/programs/moved"
    ).load()
    assert after["tasks"] == before["tasks"]


def test_relocation_rejects_busy_and_nested_active_programs(small_program: Fixture) -> None:
    args = (
        "relocate",
        "--destination",
        ".work/programs/moved",
        "--owner",
        "leader",
        "--reason",
        "Explicit synthetic migration",
    )
    with small_program.program.lock():
        assert small_program.run(*args) == 1
    assert not (small_program.program.repo / ".work/programs/moved").exists()
    nested = small_program.program.path("nested/backlog.json")
    nested.parent.mkdir()
    nested.write_bytes(b"{}")
    assert small_program.run(*args) == 1
    assert small_program.program.load()["tasks"]
    assert not (small_program.program.repo / ".work/programs/moved").exists()


@pytest.mark.parametrize("customize", [False, True])
def test_initialization_preserves_custom_readme_and_starter_is_uninitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, customize: bool
) -> None:
    repo = tmp_path / "repository"
    repo.mkdir()
    monkeypatch.setattr(fp, "git", lambda *_args: HEAD)
    program = fp.Program(repo, repo / "program")
    sample = Fixture(program, b"")
    assert (
        sample.run(
            "starter",
            "--program-id",
            "example",
            "--objective",
            "Agreed objective",
            "--criterion",
            "Verify behavior",
            "--task",
            "TASK-1",
        )
        == 0
    )
    assert not program.path("backlog.json").exists()
    assert "uninitialized starter" in program.path("README.md").read_text(encoding="utf-8")
    authored = program.path("README.md").read_bytes()
    if customize:
        authored = b"# Authored recovery\r\n\r\nKeep these exact bytes and my links.\r\n"
        program.path("README.md").write_bytes(authored)
    assert (
        sample.run(
            "init",
            "--report",
            "program/source/feature-opportunities.md",
            "--planning",
            "program/planning.json",
            "--review-baseline",
            HEAD,
        )
        == 0
    )
    assert program.path("README.md").read_bytes() == authored
    if not customize:
        assert b"If backlog.json is present" in authored
    assert sample.run("render") == 0
    assert program.path("README.md").read_bytes() == authored
    assert (
        sample.run(
            "starter",
            "--program-id",
            "example",
            "--objective",
            "Agreed objective",
            "--criterion",
            "Verify behavior",
        )
        == 1
    )
    assert program.path("README.md").read_bytes() == authored


@pytest.mark.parametrize("change", ["rewrite", "add"])
def test_source_tree_drift_during_copy_never_archives_original(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    original = small_program.program.path("operator-note.txt")
    original.write_bytes(b"captured note")
    target = small_program.program.repo / ".work/programs/moved/operator-note.txt"
    original_write = fp.atomic_write

    def mutate_after_copy(path: Path, data: bytes):
        original_write(path, data)
        if path == target:
            if change == "rewrite":
                original.write_bytes(b"latest note")
            else:
                original.with_name("new-note.txt").write_bytes(b"new note")

    monkeypatch.setattr(fp, "atomic_write", mutate_after_copy)
    assert (
        small_program.run(
            "relocate",
            "--destination",
            ".work/programs/moved",
            "--owner",
            "leader",
            "--reason",
            "Synthetic drift probe",
        )
        == 1
    )
    assert not small_program.program.path("RELOCATED.json").exists()
    assert small_program.program.load()["tasks"]


def test_relocation_reservation_rejects_competing_starter_without_overwrite(
    small_program: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = small_program.program.repo / ".work/programs/moved"
    original_mkdir = Path.mkdir
    inserted = False

    def insert_starter(path: Path, *args, **kwargs):
        nonlocal inserted
        original_mkdir(path, *args, **kwargs)
        if path == destination and not inserted:
            inserted = True
            assert (
                fp.main(
                    [
                        "--repo",
                        str(small_program.program.repo),
                        "--program-dir",
                        str(destination),
                        "starter",
                        "--program-id",
                        "competing",
                        "--objective",
                        "Different explicit goal",
                        "--criterion",
                        "Different acceptance",
                    ]
                )
                == 0
            )
        return None

    monkeypatch.setattr(Path, "mkdir", insert_starter)
    assert (
        small_program.run(
            "relocate",
            "--destination",
            str(destination),
            "--owner",
            "leader",
            "--reason",
            "Synthetic reservation race",
        )
        == 1
    )
    assert fp.read_json(destination / "planning.json")["program_id"] == "competing"
    assert not (destination / "RELOCATION_INCOMPLETE.json").exists()
    assert small_program.program.load()["tasks"]


def test_captured_bytes_reuses_only_same_logical_admission(tmp_path, monkeypatch):
    # Given one byte image addressable through different logical roots and aliases.
    folder = tmp_path / "records"
    folder.mkdir()
    path = folder / "receipt.json"
    raw = b'{"items":[1]}'
    path.write_bytes(raw)
    budget = fp.ReadBudget(remaining=100)
    admissions = []
    original = fp.safe_path

    def admitted(root, name):
        admissions.append((root, name))
        return original(root, name)

    monkeypatch.setattr(fp, "safe_path", admitted)
    # When the same logical read repeats, aliases and distinct roots are admitted separately.
    captured = budget.capture(tmp_path, "records/receipt.json", 100)
    assert budget.capture(tmp_path, "records/receipt.json", 100) is captured
    budget.capture(tmp_path, "records//receipt.json", 100)
    budget.capture(folder, "receipt.json", 100)
    # Then I/O is shared only inside the byte ledger, never logical admission authority.
    assert len(admissions) == 3
    assert budget.remaining == 100 - len(raw)
    assert captured.content == raw and captured.sha256 == fp.sha(raw)
    with pytest.raises(fp.ProgramError, match="bounded read"):
        budget.capture(tmp_path, "records/receipt.json", len(raw) - 1)


def test_captured_json_is_strict_and_consumer_values_are_isolated(tmp_path):
    # Given one checked object and malformed strict JSON in the same observation.
    path = tmp_path / "receipt.json"
    path.write_bytes(b'{"nested":{"values":[1]}}')
    budget = fp.ReadBudget()
    captured = budget.capture(tmp_path, "receipt.json", 100)
    first = budget.json(captured)
    first["nested"]["values"].append(2)
    # When another consumer decodes the same immutable byte image.
    assert budget.json(captured) == {"nested": {"values": [1]}}
    # Then each malformed image is rejected without contaminating the successful object.
    for number, raw in enumerate((b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]", b"\xff")):
        name = f"bad-{number}.json"
        (tmp_path / name).write_bytes(raw)
        bad = budget.capture(tmp_path, name, 100)
        with pytest.raises(fp.ProgramError):
            budget.json(bad)
    assert budget.json(captured) == {"nested": {"values": [1]}}


def test_reference_consumers_recheck_expected_hash_on_capture_hit(tmp_path):
    # Given correct captured bytes already admitted by a prior reference consumer.
    program = fp.Program(tmp_path, tmp_path)
    raw = b'{"value":1}'
    (tmp_path / "receipt.json").write_bytes(raw)
    budget = fp.ReadBudget()
    good = {"path": "receipt.json", "sha256": fp.sha(raw)}
    program.verify_refs([good], budget)
    # When a second consumer supplies a conflicting expected hash.
    with pytest.raises(fp.ProgramError, match="Frozen evidence changed"):
        program.verify_refs([{**good, "sha256": "0" * 64}], budget)
    # Then the valid reference remains valid in the same observation.
    program.verify_refs([good], budget)


@pytest.mark.parametrize("kind", ["bytes", "is_symlink", "is_junction", "root"])
def test_capture_final_recheck_rejects_midcapture_change(tmp_path, monkeypatch, kind):
    # Given one successful captured observation, including a reused consumer hit.
    path = tmp_path / "receipt.json"
    path.write_bytes(b'{"ok":true}')
    budget = fp.ReadBudget()
    captured = budget.capture(tmp_path, path.name, 100)
    assert budget.capture(tmp_path, path.name, 100) is captured
    if kind == "bytes":
        path.write_bytes(b'{"ok":false}')
    elif kind == "root":
        resolve = Path.resolve
        monkeypatch.setattr(
            Path,
            "resolve",
            lambda p, *a, **kw: (
                tmp_path.parent / p.relative_to(tmp_path)
                if p.is_relative_to(tmp_path)
                else resolve(p, *a, **kw)
            ),
        )
    else:
        _mock_link_lstat(monkeypatch, path, kind)
    # When final independent observation re-admits the path and reads current bytes.
    with pytest.raises(fp.ProgramError):
        budget.recheck(tmp_path)


def test_source_overlap_reuses_admitted_bytes_but_discovers_each_scope(tmp_path, monkeypatch):
    # Given overlapping directory scopes inside one explicit source observation.
    source = tmp_path / "src"
    source.mkdir()
    (source / "first.py").write_bytes(b"first")
    program = fp.Program(tmp_path, tmp_path / "program")
    budget = fp.ReadBudget()
    reads = []
    original = fp.bounded_bytes

    def read(path, limit):
        reads.append(path)
        return original(path, limit)

    monkeypatch.setattr(fp, "bounded_bytes", read)
    first = program.source_snapshot(["src"], budget)
    (source / "second.py").write_bytes(b"second")
    # When the directory is discovered again in that same reader.
    second = program.source_snapshot(["src"], budget)
    # Then existing bytes are shared, while new directory membership remains visible.
    assert reads == [source / "first.py", source / "second.py"]
    assert set(second) == set(first) | {"src/second.py"}
    assert second["src/first.py"] == first["src/first.py"]


def test_source_final_epoch_rejects_mutation_after_cached_source(tmp_path):
    # Given two overlapping captures in a retained source observation.
    (tmp_path / "src").mkdir()
    path = tmp_path / "src/file.py"
    path.write_bytes(b"before")
    program = fp.Program(tmp_path, tmp_path / "program")
    observations = fp._SourceObservations()
    token = fp._source_observations.set(observations)
    try:
        reader = fp.ReadBudget()
        program.source_snapshot(["src"], reader)
        program.source_snapshot(["src/file.py"], reader)
    finally:
        fp._source_observations.reset(token)
    path.write_bytes(b"after")
    # When the independent final source epoch captures the actual current tree.
    with pytest.raises(fp.ProgramError, match="changed during"):
        observations.verify()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows root spelling aliases")
def test_capture_admits_case_aliased_root_independently(tmp_path, monkeypatch):
    # Given two Windows spellings of the same existing logical root.
    root = tmp_path / "CaseRoot"
    root.mkdir()
    (root / "proof.json").write_bytes(b"{}")
    alias = Path(str(root).swapcase())
    budget = fp.ReadBudget()
    admissions = []
    original = fp.safe_path

    def admit(root, name):
        admissions.append(str(root))
        return original(root, name)

    monkeypatch.setattr(fp, "safe_path", admit)
    # When both root spellings capture the same underlying bytes.
    budget.capture(root, "proof.json", 100)
    budget.capture(alias, "proof.json", 100)
    # Then path equality on Windows cannot silently share admission authority.
    assert admissions == [str(root), str(alias)]


def test_explicit_file_scope_overlap_admits_once_per_reader(tmp_path, monkeypatch):
    # Given overlapping exact file scopes inside one existing observation reader.
    source = tmp_path / "src"
    source.mkdir()
    (source / "first.py").write_bytes(b"first")
    (source / "second.py").write_bytes(b"second")
    program = fp.Program(tmp_path, tmp_path / "program")
    reader = fp.ReadBudget()
    admissions = []
    original = fp.safe_path

    def admit(root, name):
        admissions.append((root, name))
        return original(root, name)

    monkeypatch.setattr(fp, "safe_path", admit)
    first = program.source_snapshot(["src/first.py", "src/second.py"], reader)
    # When a later overlapping scope observes an already captured exact file.
    second = program.source_snapshot(["src/first.py"], reader)
    # Then it shares byte-image admission without another filesystem traversal.
    assert second == {"src/first.py": first["src/first.py"]}
    assert admissions == [(tmp_path, "src/first.py"), (tmp_path, "src/second.py")]
    assert reader.remaining == 72 * 1024 * 1024 - len(b"firstsecond")


@pytest.mark.parametrize("change", ["bytes", "link"])
def test_explicit_file_scope_new_epoch_checks_mutation(tmp_path, monkeypatch, change):
    # Given one immutable file image already shared within an observation epoch.
    (tmp_path / "src").mkdir()
    path = tmp_path / "src/file.py"
    path.write_bytes(b"before")
    program = fp.Program(tmp_path, tmp_path / "program")
    reader = fp.ReadBudget()
    first = program.source_snapshot(["src/file.py"], reader)
    if change == "bytes":
        path.write_bytes(b"after")
    else:
        _mock_link_lstat(monkeypatch, path, "is_symlink")
    assert program.source_snapshot(["src/file.py"], reader) == first
    # When a separate reader admits the same exact scope again.
    if change == "bytes":
        assert program.source_snapshot(["src/file.py"], fp.ReadBudget()) != first
    else:
        with pytest.raises(fp.ProgramError, match="Linked path rejected"):
            program.source_snapshot(["src/file.py"], fp.ReadBudget())


def test_storage_registry_budget_keeps_raw_container_and_detached_body(tmp_path: Path) -> None:
    # Given: a registry is explicitly encoded, while an artifact contains identical bytes.
    repo = tmp_path / "repo"
    program = fp.Program(repo, repo / "program")
    program.directory.mkdir(parents=True)
    body = b'{ "program_id": "synthetic", "values": [1] }\n'
    raw = fp.flow_module("storage").encode_registry(body, "a" * 64)
    program.path("backlog.json").write_bytes(raw)
    reader = fp.ReadBudget()
    # When: the typed registry boundary and strict logical parser read through a budget.
    observation = program.read_registry_observation(budget=reader)
    state = fp.parse_registry_state(observation, path=program.path("backlog.json"))
    state["values"].append(2)
    # Then: raw storage never masquerades as BODY in the ordinary byte ledger.
    assert reader.cached[program.path("backlog.json")] == raw
    assert program.read_state_bytes(budget=reader) == body
    assert fp.parse_registry_state(observation, path=program.path("backlog.json"))["values"] == [1]
    with pytest.raises(fp.ProgramError):
        fp.read_json(program.path("backlog.json"))


@pytest.mark.parametrize("body", [b'{"a":1,"a":2}', b'{"a":NaN}', b"[]"])
def test_storage_explicit_body_parser_retains_strict_json_boundary(
    tmp_path: Path, body: bytes
) -> None:
    # Given: framing is correct but logical JSON is invalid.
    repo = tmp_path / "repo"
    program = fp.Program(repo, repo / "program")
    program.directory.mkdir(parents=True)
    program.path("backlog.json").write_bytes(
        fp.flow_module("storage").encode_registry(body, "b" * 64)
    )
    # When/Then: the explicit strict BODY parser rejects the malformed state.
    with pytest.raises(fp.ProgramError):
        fp.parse_registry_state(
            program.read_registry_observation(), path=program.path("backlog.json")
        )


def test_storage_closing_image_retains_each_alias_but_reuses_one_file_image(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: two independently admitted root/name pairs identify one bounded file.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    normal = fp._checked_safe_path
    admissions = []

    def counted(root, name, components=None):
        admissions.append((root, name))
        return normal(root, name, components)

    monkeypatch.setattr(fp, "_checked_safe_path", counted)
    # When: repeated consumers use different limits and logical roots.
    first = observer.file(program.repo, "src/compile-api.py", limit=100, budget_tag="first")
    second = observer.file(program.repo / "src", "compile-api.py", limit=100, budget_tag="second")
    observer.admit(program.repo, "src/compile-api.py")
    # Then: alias admission remains fresh but bytes are shared only in this closing epoch.
    assert first is second
    assert admissions.count((program.repo, "src/compile-api.py")) == 1
    assert (program.repo / "src", "compile-api.py") in admissions
    with pytest.raises(fp.ProgramError):
        observer.file(program.repo, "src/compile-api.py", limit=0, budget_tag="small")


def test_storage_source_scope_replays_excluded_boundaries_without_reading_them(
    small_program: Fixture,
) -> None:
    # Given: an ordinary source directory contains a skipped private boundary.
    program = small_program.program
    skipped = program.repo / "src" / ".git"
    skipped.mkdir()
    (skipped / "private").write_text("Never read", encoding="utf-8")
    boundaries = []
    expected = program.source_snapshot(["src"], _boundaries=boundaries)
    # When: the closing source observer independently discovers the same scope.
    actual, excluded = fp._ClosingObserver(program).scope(
        program.repo, ["src"], byte_budget=128 * 1024 * 1024, entry_limit=10000
    )
    # Then: regular membership is unchanged, and skipped-boundary identity is explicit.
    assert actual == expected
    assert excluded == boundaries == [{"path": "src/.git", "kind": "excluded_directory"}]
    assert "src/.git/private" not in actual


def test_storage_new_closing_epoch_rejects_changed_source_image(small_program: Fixture) -> None:
    # Given: one completed closing image captured an exact regular file.
    program = small_program.program
    path = program.repo / "src" / "compile-api.py"
    old = fp._ClosingObserver(program).file(
        program.repo, "src/compile-api.py", limit=100, budget_tag=None
    )
    # When: actual bytes change before a new independent closing epoch.
    path.write_bytes(b"same_length = 1\n")
    new = fp._ClosingObserver(program).file(
        program.repo, "src/compile-api.py", limit=100, budget_tag=None
    )
    # Then: no prior observer image crosses the fresh boundary.
    assert new.sha256 != old.sha256


def test_storage_recheck_preserves_actual_typed_archive_bound(tmp_path: Path) -> None:
    # Given: a typed archive has the maximum logical BODY plus its fixed frame.
    root = tmp_path / "repo"
    root.mkdir()
    body = b'{"program_id":"p","padding":"' + b"x" * (8 * 1024 * 1024 - 31) + b'"}'
    # Keep BODY exactly at the existing limit without relying on JSON whitespace normalization.
    body = body + b" " * (8 * 1024 * 1024 - len(body))
    raw = fp.flow_module("storage").encode_registry(body, "c" * 64)
    path = root / "original-backlog.json"
    path.write_bytes(raw)
    reader = fp.ReadBudget()
    reader.capture(root, path.name, 8 * 1024 * 1024 + 80)
    # When: final raw closure uses the original successful path bound.
    reader.recheck(root)
    # Then: the valid frame is retained, while an explicit narrower limit still rejects.
    with pytest.raises(fp.ProgramError):
        reader.recheck(root, limit=8 * 1024 * 1024)


def test_closing_observer_admits_narrow_root_once_and_every_logical_name(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: several original names share one explicitly narrower source root.
    program = small_program.program
    root = program.repo / "src"
    observer = fp._ClosingObserver(program)
    normal = fp._checked_safe_path
    admissions = []

    def counted(base, name, components=None):
        admissions.append((base, name))
        return normal(base, name, components)

    monkeypatch.setattr(fp, "_checked_safe_path", counted)
    # When: the complete image sees each name and repeats its admission/probe.
    names = ("compile-api.py", "docs.2.py", "test_9.py")
    for name in names:
        observer.file(root, name, limit=100, budget_tag="fixture")
        assert observer.probe(root, name, operation="kind") == "file"
        observer.admit(root, name)
    # Then: each logical name is admitted independently and root ancestry is observed once.
    assert admissions.count((program.repo, "src")) == 1
    assert all(admissions.count((root, name)) == 1 for name in names)


def test_closing_observer_new_name_rechecks_root_after_normalization(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: a root's first logical name was safely admitted in this fresh image.
    program = small_program.program
    root = program.repo / "src"
    observer = fp._ClosingObserver(program)
    observer.admit(root, "compile-api.py")
    normal = Path.resolve

    def linked(path, *args, **kwargs):
        if path.is_relative_to(root):
            return program.repo / "outside" / path.relative_to(root)
        return normal(path, *args, **kwargs)

    # When: the root resolves elsewhere before another original name is admitted.
    monkeypatch.setattr(Path, "resolve", linked)
    # Then: pure root normalization never authorizes a later linked path.
    with pytest.raises(fp.ProgramError):
        observer.admit(root, "docs.2.py")


def test_closing_observer_exact_allowed_root_needs_no_repeated_ancestor_prefix(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: the program root is already a host-bound root and an alias of repo-relative names.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    normal = fp._checked_safe_path
    admissions = []

    def counted(root, name, components=None):
        admissions.append((root, name))
        return normal(root, name, components)

    monkeypatch.setattr(fp, "_checked_safe_path", counted)
    # When: the canonical source is accessed through both original logical aliases.
    relative = program.directory.relative_to(program.repo).as_posix()
    first = observer.file(program.directory, "planning.json", limit=100000, budget_tag="one")
    second = observer.file(
        program.repo, relative + "/planning.json", limit=100000, budget_tag="two"
    )
    # Then: both aliases receive fresh admission without an extra synthetic prefix admission.
    assert first is second
    assert (program.directory, "planning.json") in admissions
    assert (program.repo, relative + "/planning.json") in admissions
    assert (program.repo, relative) not in admissions


def _path_collector(program: fp.Program):
    module = fp.flow_module("read_model")
    return module.Collector(
        program.repo,
        program.directory,
        program_id="synthetic",
        body_sha256="a" * 64,
        producer=module.producer_identity(program.repo),
        query=module.QUERY,
    )


def test_collector_dependency_mapping_memo_preserves_exact_lexical_aliases(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: real absolute file paths and a count of pure absolute normalization.
    program = small_program.program
    collector = _path_collector(program)
    target = program.repo / "src/compile-api.py"
    normal = Path.absolute
    calls = []

    def counted(path):
        calls.append(str(path))
        return normal(path)

    monkeypatch.setattr(Path, "absolute", counted)
    # When: the same exact spelling is repeatedly mapped in one activation.
    with fp._collect_dependencies(collector):
        first = fp._dependency_name(target)
        for _ in range(5):
            assert fp._dependency_name(target) == first
        alias = target.with_name(target.name.upper())
        other = fp._dependency_name(alias)
    # Then: normalization occurs once per original spelling and neither alias is erased.
    assert calls.count(str(target)) == 1
    assert first == (program.repo, "src/compile-api.py")
    assert other == (program.repo, "src/COMPILE-API.PY")


def test_collector_dependency_mapping_is_scoped_to_owner_activation_and_relative_cwd(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: the same file may be a task-program path or a repository path for two owners.
    program = small_program.program
    first = _path_collector(program)
    other_program = fp.Program(program.repo, program.repo / "other-program")
    other_program.directory.mkdir()
    second = _path_collector(other_program)
    module = fp.flow_module("read_model")
    target = program.path("planning.json")
    # When: nested collection suspends or switches owner, and relative paths change cwd.
    with fp._collect_dependencies(first):
        assert fp._dependency_name(target) == (program.directory, "planning.json")
        with module.collect(None):
            assert fp._dependency_name(target) is None
        with module.collect(second):
            assert fp._dependency_name(target) == (program.repo, "small-program/planning.json")
        assert fp._dependency_name(target) == (program.directory, "planning.json")
        monkeypatch.chdir(program.repo)
        before = fp._dependency_name(Path("relative.txt"))
        monkeypatch.chdir(program.directory)
        after = fp._dependency_name(Path("relative.txt"))
    # Then: no owner/relative-cwd mapping crosses an activation boundary.
    assert before == (program.repo, "relative.txt")
    assert after == (program.directory, "relative.txt")
    assert fp._dependency_name(target) is None


def test_closing_source_scope_reuses_same_repo_normalized_root_file(
    small_program: Fixture, monkeypatch
) -> None:
    # Given: a stored model uses the platform-normalized spelling of the exact repository root.
    import os

    program = small_program.program
    root = Path(os.path.normcase(str(program.repo)))
    observer = fp._ClosingObserver(program)
    observer.file(root, "src/compile-api.py", limit=100, budget_tag="model")
    normal = fp.safe_path
    admissions = []

    def counted(base, name):
        admissions.append((base, name))
        return normal(base, name)

    monkeypatch.setattr(fp, "safe_path", counted)
    # When: two declared source scopes replay using the actual host Program.repo spelling.
    first, _ = observer.scope(
        program.repo, ["src/compile-api.py"], byte_budget=128 * 1024 * 1024, entry_limit=10000
    )
    second, _ = observer.scope(
        program.repo, ["src/compile-api.py"], byte_budget=128 * 1024 * 1024, entry_limit=10000
    )
    # Then: the already fresh same-root file admission is reused and limits still apply.
    assert first == second
    assert (program.repo, "src/compile-api.py") not in admissions
    assert (str(program.repo.absolute()), "src/compile-api.py") in observer.reader._admissions


def test_closing_source_scope_keeps_different_roots_and_directory_discovery(
    small_program: Fixture,
) -> None:
    # Given: an independently admitted narrower root points to one file under a directory scope.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    observer.file(program.repo / "src", "compile-api.py", limit=100, budget_tag="narrow")
    assert (str(program.repo.absolute()), "src/compile-api.py") not in observer.reader._admissions
    # When: complete directory membership is replayed before and after a new source appears.
    before, _ = observer.scope(
        program.repo, ["src"], byte_budget=128 * 1024 * 1024, entry_limit=10000
    )
    (program.repo / "src/new.py").write_bytes(b"new source")
    after, _ = observer.scope(
        program.repo, ["src"], byte_budget=128 * 1024 * 1024, entry_limit=10000
    )
    # Then: a different logical root is not silently preadmitted and new membership remains visible.
    assert "src/new.py" not in before and "src/new.py" in after
    assert (str(program.repo.absolute()), "src/compile-api.py") in observer.reader._admissions


def test_collector_mapping_negative_cache_never_crosses_nested_activation(
    small_program: Fixture,
) -> None:
    # Given: one path is outside the first owner's roots and inside a second owner.
    program = small_program.program
    first = _path_collector(program)
    outer_program = fp.Program(program.repo.parent, program.repo)
    second = _path_collector(outer_program)
    path = program.repo.parent / "outside.txt"
    # When: collection is nested and then unwinds through an actual exception.
    with pytest.raises(RuntimeError):
        with fp._collect_dependencies(first):
            assert fp._dependency_name(path) is None
            with fp._collect_dependencies(second):
                assert fp._dependency_name(path) == (outer_program.repo, "outside.txt")
            assert fp._dependency_name(path) is None
            raise RuntimeError("Synthetic caller failure")
    # Then: neither an admitted mapping nor a negative result survives that activation.
    assert fp._dependency_names.get() is None
    with fp._collect_dependencies(second):
        assert fp._dependency_name(path) == (outer_program.repo, "outside.txt")


def _directory_alias(link: Path, target: Path) -> None:
    """Exercise real aliases, using an unprivileged junction if Windows denies symlinks."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.parametrize("relative_root", [False, True])
@pytest.mark.parametrize("name", ["existing.txt", "absent.txt", "missing/child.txt"])
def test_safe_path_single_resolution_preserves_regular_and_absent_paths(
    tmp_path, monkeypatch, relative_root, name
):
    # Given: an ordinary absolute or relative root and present/absent canonical candidates.
    root = tmp_path / "root"
    root.mkdir()
    (root / "existing.txt").write_bytes(b"original")
    monkeypatch.chdir(tmp_path)
    selected = Path("root") if relative_root else root
    normal = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return normal(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    # When: normal admission checks that exact candidate's canonical filesystem image.
    admitted = fp.safe_path(selected, name)
    # Then: the original path is preserved with one current resolution, without an FS cache.
    assert admitted == selected / name
    assert calls == [selected / name]


@pytest.mark.parametrize("location", ["root", "ancestor", "inward", "outward"])
def test_safe_path_single_resolution_rejects_real_directory_alias(tmp_path, location):
    # Given: real directory links redirect the root, an ancestor, or a scoped component.
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "child").mkdir()
    selected = root
    name = "proof.txt"
    if location == "root":
        selected = tmp_path / "root-alias"
        _directory_alias(selected, root)
    elif location == "ancestor":
        alias = tmp_path / "ancestor-alias"
        _directory_alias(alias, outside)
        selected = alias / "child"
    else:
        inside = root / "inside"
        inside.mkdir()
        _directory_alias(root / "entry", inside if location == "inward" else outside)
        name = "entry/proof.txt"
    # When: admission observes the real redirected candidate, including absent leaf paths.
    # Then: neither inward/outward links nor linked roots/ancestors authorize access.
    with pytest.raises(fp.ProgramError):
        fp.safe_path(selected, name)


@pytest.mark.parametrize("kind", ["is_symlink", "is_junction"])
def test_safe_path_single_resolution_retains_each_component_link_probe(tmp_path, monkeypatch, kind):
    # Given: a linked intermediate component is detected before candidate resolution.
    root = tmp_path / "root"
    root.mkdir()
    linked = root / "nested"
    linked.mkdir()
    normal = Path.lstat
    probes = []
    _mount_metadata_support(monkeypatch)

    def probe(path):
        probes.append(path)
        metadata = normal(path)
        return _link_metadata(metadata, kind) if path == linked else metadata

    monkeypatch.setattr(Path, "lstat", probe)
    # When: admission checks a descendant of that component.
    # Then: both symlink/junction guards remain live and reject before following it.
    with pytest.raises(fp.ProgramError, match="Linked path rejected"):
        fp.safe_path(root, "nested/absent.txt")
    assert linked in probes


def test_safe_path_single_resolution_rejects_late_inward_directory_redirect(tmp_path, monkeypatch):
    # Given: all component probes saw an ordinary directory before final resolution.
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "entry"
    candidate.mkdir()
    target = root / "inside"
    target.mkdir()
    normal = Path.resolve
    redirected = []

    def resolve(path, *args, **kwargs):
        if path == candidate and not redirected:
            candidate.rmdir()
            _directory_alias(candidate, target)
            redirected.append(path)
        return normal(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    # When: a real link appears after component probes and redirects within the same root.
    # Then: final canonical equality rejects this window instead of accepting mere containment.
    with pytest.raises(fp.ProgramError):
        fp.safe_path(root, "entry")
    assert redirected == [candidate]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path casing contract")
def test_safe_path_single_resolution_preserves_windows_case_alias(tmp_path):
    # Given: Windows treats an alternate-case root as the same directory.
    root = tmp_path / "MixedCase"
    root.mkdir()
    (root / "Proof.txt").write_bytes(b"original")
    alias = Path(str(root).swapcase())
    # When: the exact original alias is admitted with normal Windows case semantics.
    # Then: canonical equality retains the platform's case behavior and original spelling.
    assert str(fp.safe_path(alias, "pROOF.TXT")) == str(alias / "pROOF.TXT")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction filesystem contract")
@pytest.mark.parametrize("location", ["root", "ancestor", "component"])
def test_safe_path_single_resolution_rejects_real_windows_junction(tmp_path, location):
    # Given: a real Windows junction redirects an admitted root/ancestor/component.
    target = tmp_path / "target"
    target.mkdir()
    (target / "child").mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    root, name = (
        (junction, "absent.txt")
        if location == "root"
        else (junction / "child", "absent.txt")
        if location == "ancestor"
        else (tmp_path, "junction/absent.txt")
    )
    # When: final resolution observes the actual junction, including an absent descendant.
    # Then: alias handling cannot authorize a linked root/ancestor/component.
    with pytest.raises(fp.ProgramError):
        fp.safe_path(root, name)


def test_safe_path_single_resolution_rejects_noncanonical_relative_root(tmp_path, monkeypatch):
    # Given: an existing root is expressed through a parent traversal from another directory.
    (tmp_path / "root").mkdir()
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    # When: a relative root's lexical absolute spelling differs from its canonical path.
    # Then: the previous noncanonical-root rejection remains, even for an absent leaf.
    with pytest.raises(fp.ProgramError):
        fp.safe_path(Path("../root"), "absent.txt")


def _mount_metadata_support(monkeypatch):
    # Expose Windows metadata/API for deterministic portable junction fault images.
    monkeypatch.setattr(fp.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    monkeypatch.setattr(fp.stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003, raising=False)
    if not hasattr(Path, "is_junction"):
        monkeypatch.setattr(Path, "is_junction", lambda _path: False, raising=False)


def _link_metadata(metadata, kind):
    return SimpleNamespace(
        st_mode=fp.stat.S_IFLNK if kind == "is_symlink" else metadata.st_mode,
        st_file_attributes=getattr(metadata, "st_file_attributes", 0) | 0x400,
        st_reparse_tag=0xA0000003 if kind == "is_junction" else 0xA000000C,
    )


def _mock_link_lstat(monkeypatch, target, kind):
    normal = Path.lstat
    _mount_metadata_support(monkeypatch)

    def linked(path):
        metadata = normal(path)
        return _link_metadata(metadata, kind) if path == target else metadata

    monkeypatch.setattr(Path, "lstat", linked)


def test_component_metadata_rejects_mount_reparse_directory(tmp_path, monkeypatch):
    # Given: one fresh directory metadata image identifies a Windows mount-point junction.
    directory = tmp_path / "linked"
    directory.mkdir()
    _mock_link_lstat(monkeypatch, directory, "is_junction")
    # When: public admission evaluates the path component without following it.
    # Then: directory mode cannot hide its junction reparse tag.
    with pytest.raises(fp.ProgramError, match="Linked path rejected"):
        fp.safe_path(tmp_path, "linked")


def test_lexical_cache_retains_actual_graph_name_population_without_filesystem_facts():
    # Given: the measured current graph contains more distinct names than the old2048bound.
    names = [f"evidence/task-{index}/attempt-1/frozen.json" for index in range(2597)]
    fp._cached_safe_relative.cache_clear()
    first = [fp.safe_relative(name) for name in names]
    misses = fp._cached_safe_relative.cache_info().misses
    # When: another consumer validates the same graph's names.
    second = [fp.safe_relative(name) for name in names]
    # Then: pure results remain exact without repeating lexical parsing or storing OS facts.
    assert second == first == names
    assert fp._cached_safe_relative.cache_info().misses == misses


@pytest.mark.parametrize("error", [FileNotFoundError(), PermissionError(), ValueError("invalid")])
@pytest.mark.parametrize("legacy", [False, True, "raises"])
def test_component_metadata_error_delegates_original_version_semantics(
    tmp_path, monkeypatch, error, legacy
):
    # Given: lstat fails, and the runtime's original predicates define its error behavior.
    path = tmp_path / "proof"

    def failed(_path):
        raise error

    def old_symlink(_path):
        if legacy == "raises":
            raise PermissionError("Original predicate error")
        return legacy

    monkeypatch.setattr(Path, "lstat", failed)
    monkeypatch.setattr(Path, "is_symlink", old_symlink)
    monkeypatch.setattr(Path, "is_junction", lambda _path: False, raising=False)
    # When: the combined component check cannot obtain a valid metadata image.
    # Then: missing/error suppression and propagated errors match the original runtime path.
    if legacy == "raises":
        with pytest.raises(PermissionError):
            fp._component_linked(path)
    else:
        assert fp._component_linked(path) is legacy


@pytest.mark.parametrize("attributes,tag", [(0, 0xA0000003), (0x400, 0), (0x400, 0x80000000)])
def test_component_metadata_does_not_invent_junctions_from_other_metadata(
    tmp_path, monkeypatch, attributes, tag
):
    # Given: normal directory mode has no mount-point reparse identity.
    _mount_metadata_support(monkeypatch)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: SimpleNamespace(
            st_mode=fp.stat.S_IFDIR, st_file_attributes=attributes, st_reparse_tag=tag
        ),
    )
    # When: the combined component guard interprets this exact image.
    # Then: unrelated tags or absent reparse attributes are not relabeled as junctions.
    assert fp._component_linked(tmp_path / "ordinary") is False


def test_component_metadata_keeps_junction_api_absence_compatible(tmp_path, monkeypatch):
    # Given: a Python3.11-style runtime exposes no Path.is_junction predicate.
    class LegacyPath(type(tmp_path)):
        @property
        def is_junction(self):
            raise AttributeError("is_junction")

    _mount_metadata_support(monkeypatch)
    legacy_path = LegacyPath(tmp_path / "ordinary")
    assert not hasattr(legacy_path, "is_junction")
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: SimpleNamespace(
            st_mode=fp.stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003
        ),
    )
    # When: the component check runs on that runtime capability surface.
    # Then: it preserves the previous missing-API semantics; final canonical resolution stays separate.
    assert fp._component_linked(legacy_path) is False


def test_closing_components_model_read_finishes_after_all_dependencies(small_program, monkeypatch):
    from tools.tests.test_feature_program_review_throughput_integration import _enable_storage

    # Given: the complete model's actual source parent changes after all dependencies were read.
    program = small_program.program
    _enable_storage(small_program)
    module = fp.flow_module("read_model")
    normal = module.validate
    source = program.directory / "source"
    moved = program.directory / "previous-source"
    changed = []

    def late(*args, **kwargs):
        value = normal(*args, **kwargs)
        if value is not None:
            source.rename(moved)
            _directory_alias(source, moved)
            changed.append(source)
        return value

    monkeypatch.setattr(module, "validate", late)
    # When: the ordinary cached reader reaches its final component barrier.
    result = fp._read_materialized_projection(program.repo, program.directory)
    # Then: a late real ancestor alias cannot become a served successful representation.
    assert changed == [source]
    assert result is None


def test_closing_components_publication_finishes_before_registry_swap(small_program, monkeypatch):
    from tools.tests.test_feature_program_review_throughput_integration import _enable_storage

    # Given: exact proposed model inputs pass before an ancestor becomes linked.
    program = small_program.program
    _enable_storage(small_program)
    original = program.read_registry_observation()
    state = program.load()
    state["communication_language"] = "ko"
    module = fp.flow_module("read_model")
    normal = module.validate
    source = program.directory / "source"
    moved = program.directory / "previous-source"

    def late(*args, **kwargs):
        value = normal(*args, **kwargs)
        if value is not None:
            source.rename(moved)
            _directory_alias(source, moved)
        return value

    monkeypatch.setattr(module, "validate", late)
    # When: the actual proposed writer closes dependencies immediately before publication.
    with program.lock(), pytest.raises(fp.ProgramError):
        program.save(state)
    # Then: mandatory fresh component closure rejects the swap without changing canonical bytes.
    assert program.read_registry_observation().raw_bytes == original.raw_bytes


def test_closing_components_share_classification_but_resolve_every_alias(
    small_program, monkeypatch
):
    # Given: distinct logical aliases share one ancestor within one closing image.
    program = small_program.program
    source = program.repo / "src"
    observer = fp._ClosingObserver(program)
    normal_stat, normal_resolve = Path.lstat, Path.resolve
    stats, resolves = [], []

    def metadata(path):
        stats.append(str(path))
        return normal_stat(path)

    def resolve(path, *args, **kwargs):
        resolves.append(str(path))
        return normal_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(Path, "resolve", resolve)
    # When: original aliases are admitted and then every seen component closes freshly.
    observer.admit(program.repo, "src/compile-api.py")
    observer.admit(program.repo, "src/docs.2.py")
    observer.admit(source, "compile-api.py")
    observer.finish()
    # Then: shared metadata is read initially+finally while every alias still resolves itself.
    assert stats.count(str(source)) == 2
    assert resolves.count(str(source / "compile-api.py")) == 2
    assert resolves.count(str(source / "docs.2.py")) == 1


@pytest.mark.parametrize("kind", ["is_symlink", "is_junction"])
def test_closing_components_finish_rejects_late_seen_missing_path(small_program, monkeypatch, kind):
    # Given: an admitted missing leaf has no successful metadata classification to cache.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    missing = program.repo / "src" / "absent.py"
    observer.admit(program.repo, "src/absent.py")
    normal = Path.lstat
    _mount_metadata_support(monkeypatch)

    def changed(path):
        if path == missing:
            return _link_metadata(SimpleNamespace(st_mode=fp.stat.S_IFDIR), kind)
        return normal(path)

    monkeypatch.setattr(Path, "lstat", changed)
    # When: a previously missing pathname is now a linked component at final closure.
    # Then: all seen names close, including paths whose first metadata read failed.
    with pytest.raises(fp.ProgramError):
        observer.finish()


@pytest.mark.parametrize("legacy_lstat_fallback", [False, True], ids=["native", "legacy-lstat"])
def test_closing_components_do_not_cache_failed_metadata_or_cross_observers(
    small_program, monkeypatch, legacy_lstat_fallback
):
    # Given: runtime fallback predicates may perform extra lstat calls for missing paths.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    normal = fp._component_check
    missing = program.repo / "src" / "missing"
    calls = []

    def metadata(path):
        calls.append(str(path))
        return normal(path)

    monkeypatch.setattr(fp, "_component_check", metadata)
    if legacy_lstat_fallback:

        def legacy_symlink(path):
            try:
                return fp.stat.S_ISLNK(path.lstat().st_mode)
            except FileNotFoundError:
                return False

        monkeypatch.setattr(Path, "is_symlink", legacy_symlink)
    # When: distinct admissions revisit missing ancestors and then a new observer starts.
    observer.admit(program.repo, "src/missing/one")
    observer.admit(program.repo, "src/missing/two")
    observer.finish()
    fp._ClosingObserver(program).admit(program.repo, "src/missing/three")
    # Then: four fresh classifications retry the failure independently of fallback internals.
    assert calls.count(str(missing)) == 4
    # The successful ancestor is checked first+finish, then freshly in the new observer.
    assert calls.count(str(program.repo / "src")) == 3


def test_closing_components_new_alias_still_rejects_immediate_resolution_drift(
    small_program, monkeypatch
):
    # Given: one successful alias warmed the common ancestor classification.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    observer.admit(program.repo, "src/compile-api.py")
    new = program.repo / "src/docs.2.py"
    normal = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            program.repo.parent / "outside.py" if path == new else normal(path, *args, **kwargs)
        ),
    )
    # When: the next alias resolves outside the original lexical namespace.
    # Then: cached ancestor metadata cannot bypass that candidate's fresh resolution.
    with pytest.raises(fp.ProgramError):
        observer.admit(program.repo, "src/docs.2.py")


def test_closing_components_do_not_change_generic_source_or_path_checks(small_program, monkeypatch):
    # Given: an observer has memoized an ordinary source ancestor.
    program = small_program.program
    observer = fp._ClosingObserver(program)
    observer.admit(program.repo, "src/compile-api.py")
    _mock_link_lstat(monkeypatch, program.repo / "src", "is_symlink")
    # When: unrelated generic admission and a fresh source observation run normally.
    # Then: neither sees the observer-only memo as permission to skip fresh component checks.
    with pytest.raises(fp.ProgramError):
        fp.safe_path(program.repo, "src/docs.2.py")
    with pytest.raises(fp.ProgramError):
        program.source_snapshot(["src/docs.2.py"], fp.ReadBudget())


@pytest.mark.parametrize("admitted", [False, True])
def test_recheck_growth_preserves_snapshot_context_and_original_bound(tmp_path, admitted):
    # Given: an exact bounded observation later grows beyond its original closing budget.
    path = tmp_path / "proof.txt"
    path.write_bytes(b"original")
    budget = fp.ReadBudget()
    if admitted:
        budget.capture(tmp_path, path.name, 100)
    else:
        budget.read(path, 100)
    changed = b"original with more bytes"
    path.write_bytes(changed)
    # When: the independent final capture cannot fit the original aggregate bound.
    with pytest.raises(fp.ProgramError, match="changed during snapshot") as caught:
        budget.recheck(tmp_path)
    # Then: contextual diagnostics preserve the original bound failure and actual bytes.
    assert isinstance(caught.value.__cause__, fp.ProgramError)
    assert "bounded read" in str(caught.value.__cause__)
    assert str(caught.value.__cause__) in str(caught.value)
    assert path.read_bytes() == changed


@pytest.mark.parametrize("kind", ["is_symlink", "is_junction", "missing"])
def test_recheck_path_failure_preserves_snapshot_context_and_guard_cause(
    tmp_path, monkeypatch, kind
):
    # Given: one successful logical admission later becomes linked or unavailable.
    path = tmp_path / "proof.txt"
    path.write_bytes(b"original")
    budget = fp.ReadBudget()
    budget.capture(tmp_path, path.name, 100)
    if kind == "missing":
        path.unlink()
    else:
        _mock_link_lstat(monkeypatch, path, kind)
    # When: independent path admission enforces its original safety and regular-file rules.
    with pytest.raises(fp.ProgramError, match="changed during snapshot") as caught:
        budget.recheck(tmp_path)
    # Then: original guard detail and exception cause survive the contextual failure.
    assert isinstance(caught.value.__cause__, fp.ProgramError)
    assert str(caught.value.__cause__) in str(caught.value)


def test_optional_mutation_outcome_records_commit_without_snapshot(fixture, monkeypatch, capsys):
    assert fixture.claim() == 0
    monkeypatch.setattr(
        fp, "read_snapshot", lambda *args, **kwargs: pytest.fail("Outcome requested a snapshot")
    )
    capsys.readouterr()
    name = "evidence/_coordination/attempt-1/note-outcome.json"
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Actual progress",
            "--next-action",
            "Continue",
            "--outcome-path",
            name,
        )
        == 0
    )
    assert capsys.readouterr().out == "note: OK\n"
    result = fp.read_json(fixture.program.path(name))
    assert result["publication"]["status"] == "committed"
    assert result["publication"]["revision"] == fp.phases.revision(fixture.program.load())
    assert result["changed_task_ids"] == ["S01"]
    assert result["acceptance_complete"] is False


def test_optional_mutation_outcome_retains_committed_render_failure(fixture, monkeypatch):
    assert fixture.claim() == 0
    before = fixture.program.load()
    monkeypatch.setattr(
        fp.Program, "render", lambda *args: (_ for _ in ()).throw(OSError("actual render failure"))
    )
    name = "evidence/_coordination/attempt-1/render-outcome.json"
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Actual progress",
            "--next-action",
            "Continue",
            "--outcome-path",
            name,
        )
        == 1
    )
    result = fp.read_json(fixture.program.path(name))
    assert result["publication"]["status"] == "committed_view_failed"
    assert result["publication"]["view_recovery_required"] is True
    assert len(fixture.program.load()["events"]) == len(before["events"]) + 1
    assert result["next_action"] == "recover_views"


def test_optional_outcome_retention_failure_does_not_replay_committed_mutation(
    fixture, monkeypatch, capsys
):
    assert fixture.claim() == 0
    before = fixture.program.load()

    def fail_retention(*args, **kwargs):
        raise OSError("Actual outcome destination failure")

    monkeypatch.setattr(fp.authoring, "freeze_document", fail_retention)
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Actual progress",
            "--next-action",
            "Continue",
            "--outcome-path",
            "evidence/_coordination/attempt-1/failed-outcome.json",
        )
        == 1
    )
    assert len(fixture.program.load()["events"]) == len(before["events"]) + 1
    assert "publication=committed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "path,parent",
    [
        (fp.PureWindowsPath("C:/Repo/A"), fp.PureWindowsPath("c:/repo")),
        (fp.PureWindowsPath("C:/Repo2/A"), fp.PureWindowsPath("C:/Repo")),
        (fp.PureWindowsPath("D:/Repo/A"), fp.PureWindowsPath("C:/Repo")),
        (fp.PureWindowsPath("//server/share/Repo/A"), fp.PureWindowsPath("//SERVER/share/repo")),
        (fp.PureWindowsPath("C:/Repo/../outside"), fp.PureWindowsPath("C:/Repo")),
        (fp.PurePosixPath("/Repo/A"), fp.PurePosixPath("/Repo")),
        (fp.PurePosixPath("/repo/A"), fp.PurePosixPath("/Repo")),
        (fp.PurePosixPath("/Repo2/A"), fp.PurePosixPath("/Repo")),
        (fp.PurePosixPath("relative/A"), fp.PurePosixPath("relative")),
        (fp.PureWindowsPath("C:relative/A"), fp.PureWindowsPath("C:relative")),
        (fp.PurePosixPath("/" + "a/" * 65), fp.PurePosixPath("/a")),
        (fp.PurePosixPath("/" + "a" * 2050), fp.PurePosixPath("/")),
    ],
)
def test_bounded_pure_containment_matches_native_public_parser(path, parent):
    assert fp._is_path_within(path, parent) == path.is_relative_to(parent)


def test_pure_containment_preserves_custom_parser_fallback_and_bounded_cache():
    calls = []

    class CustomPath(fp.PurePosixPath):
        def is_relative_to(self, other):
            calls.append(other)
            return False

    assert fp._is_path_within(CustomPath("/repo/a"), fp.PurePosixPath("/repo")) is False
    assert len(calls) == 1
    for number in range(4100):
        fp._is_path_within(fp.PurePosixPath(f"/bounded/{number}"), fp.PurePosixPath("/bounded"))
    # Pylint follows the wrapped parser signature for this lru_cache method.
    assert fp._absolute_path_components.cache_info().currsize <= 4096  # pylint: disable=no-value-for-parameter


@pytest.mark.parametrize("after_replace", [False, True], ids=["precommit", "after-replace"])
def test_optional_mutation_outcome_distinguishes_atomic_failure_boundary(
    fixture, monkeypatch, after_replace
):
    assert fixture.claim() == 0
    before = fixture.program.read_registry_observation()
    normal = fp.atomic_write

    def fail_registry(path, content):
        if path == fixture.program.path("backlog.json"):
            if after_replace:
                normal(path, content)
            raise OSError("Actual registry replacement boundary failure")
        return normal(path, content)

    monkeypatch.setattr(fp, "atomic_write", fail_registry)
    name = "evidence/_coordination/attempt-1/atomic-outcome.json"
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Actual progress",
            "--next-action",
            "Inspect publication",
            "--outcome-path",
            name,
        )
        == 1
    )
    result = fp.read_json(fixture.program.path(name))
    actual = fixture.program.read_registry_observation()
    assert result["publication"]["status"] == (
        "committed_view_failed" if after_replace else "not_committed"
    )
    assert result["publication"]["view_recovery_required"] is after_replace
    assert (actual.raw_bytes == before.raw_bytes) is (not after_replace)
    assert result["publication"]["revision"] == actual.body_sha256
    assert result["acceptance_complete"] is False


@pytest.mark.parametrize("changed", [False, True])
def test_byte_observation_memo_preserves_complete_collector_descriptors(
    small_program, monkeypatch, changed
):
    program = small_program.program
    target = program.repo / "src/compile-api.py"
    original = bytes(bytearray(b"original exact image"))
    other = bytes(bytearray(b"different exact image"))
    baseline, candidate = _path_collector(program), _path_collector(program)
    events = [(target, original, 100, "reader-one")] * 1000
    events += [(target, original, 99, "reader-one"), (target, original, 100, "reader-two")]
    events += [(target.with_name(target.name.upper()), original, 100, "reader-one")]
    if changed:
        events += [(target, other, 100, "reader-one"), (target, original, 100, "reader-one")]
    for path, content, limit, tag in events:
        baseline.observe_file(
            program.repo,
            path.relative_to(program.repo).as_posix(),
            kind="file",
            sha256=fp.sha(content),
            size=len(content),
            limit=limit,
            budget_tag=tag,
        )
    normal_sha, normal_emit = fp.sha, candidate.observe_file
    hashes, emissions = [], []

    def counted_sha(content):
        hashes.append(content)
        return normal_sha(content)

    def counted_emit(*args, **kwargs):
        emissions.append((args, kwargs))
        return normal_emit(*args, **kwargs)

    monkeypatch.setattr(fp, "sha", counted_sha)
    monkeypatch.setattr(candidate, "observe_file", counted_emit)
    with fp._collect_dependencies(candidate):
        for path, content, limit, tag in events:
            fp._observe_bytes(path, content, limit, tag)
    assert len(hashes) == (2 if changed else 1)
    assert len(emissions) == (5 if changed else 4)
    assert candidate.finish({"actual": "same detached snapshot"}) == baseline.finish(
        {"actual": "same detached snapshot"}
    )
    assert ("conflicting_observation" in candidate._reasons) is changed


def test_byte_observation_memo_is_bounded_and_pins_exact_objects(small_program, monkeypatch):
    program = small_program.program
    collector = _path_collector(program)
    monkeypatch.setattr(fp, "_OBSERVED_IMAGE_LIMIT", 2)
    monkeypatch.setattr(fp, "_OBSERVED_BYTE_LIMIT", 8)
    images = [bytes(bytearray([number] * 4)) for number in range(6)]
    with fp._collect_dependencies(collector):
        for number, image in enumerate(images):
            fp._observe_bytes(program.repo / f"image-{number}", image, 4, "reader")
        memo = fp._dependency_names.get().byte_observations
        assert len(memo.images) == len(memo.descriptors) == 2
        assert memo.retained_bytes == 8
        assert all(memo.images[id(image)][0] is image for image in images[:2])
        assert len(collector._facts["files"]) == 6
    assert fp._dependency_names.get() is None
    with fp._collect_dependencies(collector):
        assert not fp._dependency_names.get().byte_observations.images


@pytest.mark.parametrize("limit,tag", [(True, "reader"), (1, True), (0, "reader")])
def test_byte_observation_memo_keeps_consumer_validation(small_program, limit, tag):
    program = small_program.program
    collector = _path_collector(program)
    target, image = program.repo / "proof", b"x"
    module = fp.flow_module("read_model")
    with fp._collect_dependencies(collector):
        fp._observe_bytes(target, image, 1, "reader")
        with pytest.raises(module.ModelError):
            fp._observe_bytes(target, image, limit, tag)


def test_byte_observation_memo_keeps_owner_and_fresh_filesystem_epochs(small_program):
    program = small_program.program
    first, second = _path_collector(program), _path_collector(program)
    path = program.repo / "src/compile-api.py"
    path.write_bytes(b"original")
    reader = fp.ReadBudget()
    module = fp.flow_module("read_model")
    with fp._collect_dependencies(first):
        captured = reader.capture(program.repo, "src/compile-api.py", 100)
        with pytest.raises(fp.ProgramError, match="bounded read"):
            reader.capture(program.repo, "src/compile-api.py", 1)
        with module.collect(second):
            fp._observe_bytes(path, captured.content, 100, "other-reader")
        assert len(second._facts["files"]) == 1
        path.write_bytes(b"changed!")
        # A new reader must still read fresh bytes and record the conflicting image.
        fp.ReadBudget().capture(program.repo, "src/compile-api.py", 100)
        assert "conflicting_observation" in first._reasons
        with pytest.raises(fp.ProgramError, match="changed during snapshot"):
            reader.recheck(program.repo)
    assert fp._dependency_names.get() is None


def test_byte_observation_memo_uses_object_identity_not_equal_bytes(small_program, monkeypatch):
    program = small_program.program
    collector = _path_collector(program)
    first = bytes(bytearray(b"equal byte values"))
    second = bytes(bytearray(first))
    assert first == second and first is not second
    normal = fp.sha
    calls = []

    def counted(content):
        calls.append(content)
        return normal(content)

    monkeypatch.setattr(fp, "sha", counted)
    with fp._collect_dependencies(collector):
        for image in (first, first, second, second):
            fp._observe_bytes(program.repo / "proof", image, 100, "reader")
        memo = fp._dependency_names.get().byte_observations
        assert len(memo.images) == 2
        assert memo.images[id(first)][0] is first
        assert memo.images[id(second)][0] is second
    assert len(calls) == 2
    assert len(collector._facts["files"]) == 1


def test_byte_observation_memo_preserves_each_expected_sha_consumer(small_program):
    program = small_program.program
    path = program.directory / "evidence/proof.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"original exact evidence")
    collector = _path_collector(program)
    reader = fp.ReadBudget()
    with fp._collect_dependencies(collector):
        program.verify_refs(
            [{"path": "evidence/proof.txt", "sha256": fp.sha(path.read_bytes())}], reader
        )
        with pytest.raises(fp.ProgramError, match="Frozen evidence changed"):
            program.verify_refs([{"path": "evidence/proof.txt", "sha256": "0" * 64}], reader)
