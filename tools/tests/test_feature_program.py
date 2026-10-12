"""Isolated coordination tests; no real private program or native checks are used."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import feature_program as fp  # noqa: E402

HEAD = "a" * 40


@dataclass(frozen=True, slots=True)
class Fixture:
    program: fp.Program
    report: bytes

    def run(self, *arguments: str) -> int:
        return fp.main(["--repo", str(self.program.repo), "--program-dir", "program", *arguments])

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
    for task_id in fp.EXPECTED_IDS:
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
            for task_id in fp.EXPECTED_IDS
        ],
    }
    (repo / "plan.json").write_bytes(fp.canonical(plan))
    (repo / "src").mkdir()
    for task_id in fp.EXPECTED_IDS:
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
    for task_id in fp.EXPECTED_IDS:
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
        fp.extract_sections(data)


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
    assert fixture.program.task(fixture.program.load(), "S01")["status"] == "review"


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
