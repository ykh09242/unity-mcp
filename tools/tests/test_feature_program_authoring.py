"""Authoring uses real transitions on isolated synthetic programs; no external provider calls."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import pytest

from tools.tests.test_feature_program import Fixture, HEAD, fp
from tools.tests.test_feature_program_phases import GateFixture, gate  # noqa: F401
from tools.tests.test_feature_program_planning import (
    decomposition_input,
    membership,
    planned_input,
    proposal,
    publication_decision,
    reviewed_input,
    runtime_input,
)

author = fp.authoring


@pytest.fixture(autouse=True)
def observation_clock(monkeypatch):
    """Compare actual and proposed semantic state with the same explicit observation time."""

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 13, 1, 2, 3, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(fp.phases, "datetime", FixedDateTime)
    for module, name in ((fp, "utc"), (fp.coordination, "_utc"), (fp.questions, "_utc")):
        monkeypatch.setattr(module, name, lambda: "2026-10-13T01:02:03+00:00")


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def forbidden(*args, **kwargs):
    raise AssertionError("Read-only authoring reached a publication capability")


def preview(fixture: Fixture, argv: list[str], capsys, monkeypatch) -> dict:
    name = fixture.write("author-request.json", {"argv": argv})
    before = tree(fixture.program.repo)
    capsys.readouterr()
    with monkeypatch.context() as patch:
        for method in ("lock", "save", "render", "checkpoint"):
            patch.setattr(fp.Program, method, forbidden)
        for method in ("lock", "save", "freeze"):
            patch.setattr(fp.planning.PublicationStore, method, forbidden)
        patch.setattr(fp, "atomic_write", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        assert fixture.run("preview", "--request", name) == 0
    output = capsys.readouterr()
    assert not output.err
    result = json.loads(output.out)
    assert tree(fixture.program.repo) == before
    return result


def normalized(value):
    if isinstance(value, dict):
        return {
            key: normalized(item)
            for key, item in value.items()
            if not key.endswith("_at") and key != "timestamp"
        }
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def test_support_lists_every_mutation_and_exact_parser_arguments():
    result = author.support()
    assert len(result["commands"]) == 41
    assert len(result["templates"]) == 5
    assert {row["command"] for row in result["commands"]} == author.COMMANDS
    assert (
        len([row for row in result["commands"] if row["assessment"] == "partial_publication"]) == 6
    )
    assert {"recovery-readiness", "delivery-inputs"} <= result["read_operations"].keys()


def test_preview_request_preserves_repeatable_flags_and_repeated_values(gate, capsys, monkeypatch):
    gate.ready()
    scopes = ["src/S01.py"]
    validation = gate.fixture.validation("S01", scopes)
    argv = [
        "claim",
        "S01",
        "--owner",
        "worker",
        "--scope",
        scopes[0],
        "--next-action",
        "worker",
        "--revalidation",
        validation,
    ]
    result = preview(gate.fixture, argv, capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert gate.fixture.run(*argv) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])
    first = gate.fixture.evidence("S01", label="first")
    second = gate.fixture.evidence("S01", label="second")
    argv = [
        "note",
        "S01",
        "--owner",
        "worker",
        "--reason",
        "worker",
        "--next-action",
        "worker",
        "--evidence",
        first,
        "--evidence",
        second,
    ]
    result = preview(gate.fixture, argv, capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert gate.fixture.run(*argv) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])


@pytest.mark.parametrize("command", sorted(author.COMMANDS))
def test_every_family_has_readonly_actual_cli_failure_or_state_parity(
    gate, command, capsys, monkeypatch
):
    fixture = gate.fixture
    receipt = fixture.write("evidence/_coordination/attempt-1/input.json", {})
    values = {
        "id": "S01",
        "owner": "director",
        "scope": "src/S01.py",
        "next_action": "Inspect synthetic source",
        "reason": "Synthetic observation",
        "note": "Synthetic checkpoint",
        "value": "en",
        "destination": "moved-program",
        "successor": "missing-successor",
        "successor_id": "missing",
        "expected_revision": fp.phases.revision(gate.program.load()),
        "successor_revision": "a" * 64,
    }
    argv = [command]
    row = next(row for row in author.support()["commands"] if row["command"] == command)
    for field in row["arguments"]:
        if field["required"]:
            if field["flags"]:
                argv.append(field["flags"][0])
            argv.append(values.get(field["name"], receipt))
    result = preview(fixture, argv, capsys, monkeypatch)
    capsys.readouterr()
    code = fixture.run(*argv)
    actual = capsys.readouterr()
    if result["status"] == "failed":
        assert code == 1
        assert actual.err.strip() == "feature-program: " + result["reasons"][0]
    elif result["status"] == "validated":
        assert code == 0, actual.err
        assert normalized(gate.program.load()) == normalized(result["predicted_state"])
    else:
        assert command in author.PUBLICATION_COMMANDS
        assert result["assessment"] == "partial_publication"
        assert result["preparation"]["not_assessed"]
        assert result["predicted_state"] is None


def test_claim_submit_review_delivery_prediction_and_failed_ci_facts(gate, capsys, monkeypatch):
    gate.ready()
    fixture = gate.fixture
    validation = fixture.validation("S01", ["src/S01.py"])
    argv = [
        "claim",
        "S01",
        "--owner",
        "worker",
        "--scope",
        "src/S01.py",
        "--next-action",
        "Implement fixture",
        "--revalidation",
        validation,
    ]
    result = preview(fixture, argv, capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert fixture.run(*argv) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])
    receipt = fixture.receipt()
    argv = ["submit", "S01", "--owner", "worker", "--receipt", receipt]
    result = preview(fixture, argv, capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert fixture.run(*argv) == 0
    review = fixture.review()
    argv = ["complete", "S01", "--review", review]
    result = preview(fixture, argv, capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert fixture.run(*argv) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])
    state = gate.program.load()
    task = gate.program.task(state, "S01")
    attempt = gate.program.current_attempt(task)
    delivery = {
        "task_id": "S01",
        "attempt": 1,
        "branch": "fixture",
        "base": "beta",
        "pr_url": "https://example.invalid/pr/1",
        "tested_commit": HEAD,
        "commits": [HEAD],
        "reviewed_source_digest": attempt["submission"]["source_digest"],
        "required_ci": ["unit"],
        "ci": [
            {
                "name": "unit",
                "url": "https://example.invalid/run/1",
                "commit": HEAD,
                "conclusion": "failure",
            }
        ],
        "merge_status": "open",
        "evidence": [fixture.evidence("S01", label="provider")],
    }
    name = fixture.write("evidence/S01/attempt-1/delivery.json", delivery)
    result = preview(fixture, ["delivery", "S01", "--receipt", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert result["delivery"]["status"] == "ci_failed"
    assert result["delivery"]["remote_verified_by_tool"] is False
    assert fixture.run("delivery", "S01", "--receipt", name) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])


@pytest.mark.parametrize("change_scope", [False, True])
def test_full_planning_preview_includes_reviewed_renewal_and_save(
    gate, capsys, monkeypatch, change_scope
):
    gate.ready()
    data = planned_input(gate)
    if change_scope:
        data["changes"] = [{"id": "S01", "before": {"phase": 1}, "after": {"phase": 2}}]
        name, _ = reviewed_input(gate, data)
    else:
        name = runtime_input(gate, data)
    result = preview(gate.fixture, ["task-plan", "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert result["predicted_state"]["amendments"]
    assert gate.fixture.run("task-plan", "--input", name) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])


def test_category_full_replacement_movement_and_rollups(gate, capsys, monkeypatch):
    state = gate.program.load()
    root = state["tasks"][0]["workstream"]
    name = gate.fixture.write(
        "category.json",
        {
            "reason": "Group actual fixture task",
            "categories": [
                {"id": "child", "title": "Child", "parent_id": root, "task_ids": ["S01"]}
            ],
        },
    )
    result = preview(gate.fixture, ["category-set", "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert result["categories"]["moved_tasks"] == ["S01"]
    child = next(row for row in result["categories"]["after"]["categories"] if row["id"] == "child")
    assert child["direct_count"] == child["descendant_count"] == 1
    assert gate.fixture.run("category-set", "--input", name) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])


@pytest.mark.parametrize(
    "categories",
    [
        [{"id": "c", "title": "C", "parent_id": "missing", "task_ids": []}],
        [{"id": "c", "title": "C", "parent_id": "c", "task_ids": []}],
        [{"id": "c", "title": "C", "parent_id": None, "task_ids": ["missing"]}],
    ],
)
def test_category_invalid_replacement_retains_canonical_reason(
    gate, capsys, monkeypatch, categories
):
    name = gate.fixture.write("category.json", {"reason": "Fixture", "categories": categories})
    result = preview(gate.fixture, ["category-set", "--input", name], capsys, monkeypatch)
    assert result["status"] == "failed"
    assert result["predicted_state"] is None
    assert gate.fixture.run("category-set", "--input", name) == 1
    assert capsys.readouterr().err.strip() == "feature-program: " + result["reasons"][0]


def test_original_failure_precedes_unsafe_projection_and_missing_input(gate, capsys, monkeypatch):
    state = gate.program.load()
    del state["objective"]["criteria"]
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    result = preview(
        gate.fixture,
        ["submit", "S01", "--owner", "worker", "--receipt", "missing.json"],
        capsys,
        monkeypatch,
    )
    assert result["status"] == "failed"
    assert result["failure_type"] == "GovernanceError"
    assert result["predicted_state"] is None
    assert gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", "missing.json") == 1
    assert capsys.readouterr().err.strip() == "feature-program: " + result["reasons"][0]


def test_preview_runs_final_save_preflight(gate, capsys, monkeypatch):
    original = fp.Program.views
    monkeypatch.setattr(
        fp.Program,
        "views",
        lambda self, state: {**original(self, state), "huge.md": "x" * (author.LIMIT + 1)},
    )
    result = preview(
        gate.fixture,
        ["language", "--owner", "director", "--value", "en", "--reason", "Explicit fixture change"],
        capsys,
        monkeypatch,
    )
    assert result["status"] == "failed"
    assert "Derived document exceeds" in result["reasons"][0]


def test_preview_final_registry_drift_rejected(gate, capsys, monkeypatch):
    original = author.preview

    def drifting(*args, **kwargs):
        result = original(*args, **kwargs)
        gate.program.path("backlog.json").write_bytes(b"{}\n")
        return result

    monkeypatch.setattr(author, "preview", drifting)
    name = gate.fixture.write(
        "request.json",
        {"argv": ["language", "--value", "en", "--owner", "director", "--reason", "Fixture"]},
    )
    assert gate.fixture.run("preview", "--request", name) == 1
    assert "Registry changed during authoring" in capsys.readouterr().err


def test_preview_rejects_changed_source_between_complete_preparations(gate, capsys, monkeypatch):
    gate.ready()
    fixture = gate.fixture
    assert fixture.claim() == 0
    source = gate.program.repo / "src/S01.py"
    source.write_text("version = 'B'\n", encoding="utf-8")
    receipt = fixture.receipt()
    request = fixture.write(
        "request.json", {"argv": ["submit", "S01", "--owner", "worker", "--receipt", receipt]}
    )
    registry = gate.program.path("backlog.json").read_bytes()
    original = author._preflight
    calls = 0

    def drift(program, prepared):
        nonlocal calls
        original(program, prepared)
        calls += 1
        if calls == 1:
            source.write_text("version = 'C'\n", encoding="utf-8")

    monkeypatch.setattr(author, "_preflight", drift)
    capsys.readouterr()
    assert fixture.run("preview", "--request", request) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed", result
    assert "changed during preview" in result["reasons"][0]
    assert result["predicted_state"] is None
    assert registry == gate.program.path("backlog.json").read_bytes()


def test_checkpoint_preview_observes_exact_actual_status_format(gate, capsys, monkeypatch):
    observed = " M src/S01.py\0?? src/new file.py\0?? src/new-dir/nested.py\0"
    expected = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
    calls = []

    def git(repo, args):
        if args[0] == "status":
            calls.append(args)
            assert args == expected
            return observed
        return HEAD

    monkeypatch.setattr(fp, "git", git)
    args = [
        "checkpoint",
        "--note",
        "Observed fixture",
        "--next-action",
        "Continue",
        "--team-state",
        "Recorded",
    ]
    result = preview(gate.fixture, args, capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert gate.fixture.run(*args) == 0
    assert result["predicted_state"]["checkpoint"] == gate.program.load()["checkpoint"]
    assert calls and all(call == expected for call in calls)


def test_resume_preparation_and_template_use_explicit_head_without_git(gate, monkeypatch):
    from tools.tests.test_feature_program_resume import block, resolution

    gate.ready()
    assert gate.fixture.claim() == 0
    block(gate.fixture)
    receipt = resolution(gate.fixture)
    state = gate.program.load()
    args = argparse.Namespace(command="resume", id="S01", owner="worker", receipt=receipt)
    before = tree(gate.program.repo)
    monkeypatch.setattr(fp.Program, "current_head", forbidden)
    monkeypatch.setattr(fp, "git", forbidden)
    prepared = author.prepare(gate.program, state, args, observed_head=HEAD)
    author._preflight(gate.program, prepared)
    template = gate.program.prepare_template(
        state, argparse.Namespace(kind="resume", id="S01"), observed_head=HEAD
    )
    assert template["head"] == HEAD
    assert prepared.state["tasks"][0]["status"] == "active"
    assert tree(gate.program.repo) == before


def test_recovery_reports_review_submission_source_drift_without_writes(gate):
    gate.ready()
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    (gate.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
    state = gate.program.load()
    before = tree(gate.program.repo)
    result = author.recovery_readiness(gate.program, state)
    assert result["status"] == "attention", result
    assert any(
        item.get("kind") == "stale_submission" and item.get("task_id") == "S01"
        for item in result["findings"]
    )
    assert result["lock_stealable"] == "not_assessed"
    assert tree(gate.program.repo) == before


def test_observation_clock_defaults_resets_and_isolates_concurrent_contexts(monkeypatch):
    monkeypatch.undo()  # This test exercises real clock defaults, not the parity fixture clock.
    clocks = (fp.utc, fp.coordination._utc, fp.questions._utc)
    first = "2001-01-01T00:00:00+00:00"
    second = "2002-02-02T00:00:00+00:00"
    assert all(clock() not in {first, second} for clock in clocks)
    with pytest.raises(RuntimeError, match="clock reset"):
        with fp.coordination.preparation_clock(first):
            assert all(clock() == first for clock in clocks)
            with fp.coordination.preparation_clock(second):
                assert all(clock() == second for clock in clocks)
            assert all(clock() == first for clock in clocks)
            raise RuntimeError("clock reset")
    assert all(clock() not in {first, second} for clock in clocks)
    barrier = Barrier(2)

    def observe(value):
        with fp.coordination.preparation_clock(value):
            barrier.wait(timeout=5)
            return tuple(clock() for clock in clocks)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(observe, (first, second)))
    assert results == [(first,) * 3, (second,) * 3]
    assert all(clock() not in {first, second} for clock in clocks)


def test_preview_compares_complete_state_beyond_source_digests(gate, capsys, monkeypatch):
    original = author.prepare
    calls = 0

    def alter_semantic_fact(*args, **kwargs):
        nonlocal calls
        prepared = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            prepared.state["communication_language"] = "ko"
        return prepared

    monkeypatch.setattr(author, "prepare", alter_semantic_fact)
    result = preview(
        gate.fixture,
        ["language", "--owner", "director", "--value", "en", "--reason", "Fixture"],
        capsys,
        monkeypatch,
    )
    assert result["status"] == "failed"
    assert result["reasons"] == ["Prepared state changed during preview; refresh"]


def test_preparations_share_declared_clock_and_preserve_derived_hashes(gate, capsys, monkeypatch):
    gate.ready()
    data = planned_input(gate)
    data["changes"] = [{"id": "S01", "before": {"phase": 1}, "after": {"phase": 2}}]
    name, _ = reviewed_input(gate, data)
    ticks = iter(range(1000))

    def ticking():
        return fp.coordination.observed_timestamp(
            f"2026-10-13T01:{next(ticks) // 60:02d}:{next(ticks) % 60:02d}+00:00"
        )

    for module, key in ((fp, "utc"), (fp.coordination, "_utc"), (fp.questions, "_utc")):
        monkeypatch.setattr(module, key, ticking)
    result = preview(gate.fixture, ["task-plan", "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    phases = result["predicted_state"]["phases"]
    assert phases["review"]["recorded_at"] == result["observed_at"]
    assert phases["acceptance"]["review_digest"] == fp.phases.revision(phases["review"])


@pytest.mark.parametrize(
    "operation",
    sorted(author.COMMANDS - {"claim", "submit", "complete", "rework", "resume", "delivery"}),
)
def test_scaffold_every_nonexecution_family_is_nonpassing_and_readonly(
    gate, operation, capsys, monkeypatch
):
    before = tree(gate.program.repo)
    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(fp.Program, "lock", forbidden)
        patch.setattr(fp, "atomic_write", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        assert gate.fixture.run("scaffold", "--operation", operation) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "not_assessed"
    assert result["acceptance_complete"] is False
    assert tree(gate.program.repo) == before


def test_recovery_busy_lock_is_observation_only(gate, capsys, monkeypatch):
    lock = gate.program.path(".feature-program.lock")
    lock.write_bytes(fp.canonical({"pid": 999999, "created_at": "2020-01-01T00:00:00+00:00"}))
    before = tree(gate.program.repo)
    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(fp.Program, "lock", forbidden)
        assert gate.fixture.run("recovery-readiness") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["lock"]["pid"] == 999999
    assert result["liveness"] == "unknown"
    assert result["lock_stealable"] == "not_assessed"
    assert tree(gate.program.repo) == before


@pytest.mark.parametrize(
    "operation",
    ["task-admit", "task-split", "task-retire", "objective-membership", "task-prioritize"],
)
def test_remaining_planning_families_predict_full_actual_state(
    gate, operation, capsys, monkeypatch
):
    gate.ready()
    if operation == "task-admit":
        data = proposal(gate)
    elif operation in {"task-split", "task-retire"}:
        data = decomposition_input(gate, operation)
    elif operation == "objective-membership":
        data = membership(gate, ["S01"])
    else:
        data = planned_input(gate, operation)
        data["changes"] = [{"id": "S01", "before": None, "after": -7}]
    name = (
        runtime_input(gate, data)
        if operation == "task-prioritize"
        else reviewed_input(gate, data)[0]
    )
    result = preview(gate.fixture, [operation, "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert gate.fixture.run(operation, "--input", name) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])


def test_question_lifecycle_previews_use_real_question_guards(gate, capsys, monkeypatch):
    fixture = gate.fixture
    bound = {
        "source_head": HEAD,
        "scope": ["src/S01.py"],
        "source_digest": gate.program.source_digest(["src/S01.py"]),
    }
    receipt = {
        **bound,
        "id": "question-one",
        "version": 1,
        "actor_id": "director",
        "prompt": "Which synthetic scope?",
        "why": "Scope affects acceptance",
        "requested_decision": "Choose scope",
        "options": ["Small", "Large"],
        "affected_task_ids": ["S01"],
        "objective_id": "goal",
    }
    name = fixture.write("question.json", receipt)
    result = preview(fixture, ["question-issue", "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert fixture.run("question-issue", "--input", name) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])
    fp.questions.record_answer(
        gate.program.repo, gate.program.directory, "question-one", 1, "Small", "answer-one"
    )
    for command in ("question-acknowledge", "question-resolve"):
        name = fixture.write(
            command + ".json",
            {
                **bound,
                "question_id": "question-one",
                "version": 1,
                "actor_id": "director",
                "submission_id": "answer-one",
                "summary": "Apply actual answer",
            },
        )
        result = preview(fixture, [command, "--input", name], capsys, monkeypatch)
        assert result["status"] == "validated", result
        assert fixture.run(command, "--input", name) == 0
        assert normalized(gate.program.load()) == normalized(result["predicted_state"])
    receipt["id"] = "question-two"
    name = fixture.write("question-two.json", receipt)
    assert fixture.run("question-issue", "--input", name) == 0
    name = fixture.write(
        "withdraw.json",
        {
            **bound,
            "question_id": "question-two",
            "version": 1,
            "actor_id": "director",
            "summary": "Explicit withdrawal",
        },
    )
    result = preview(fixture, ["question-withdraw", "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert fixture.run("question-withdraw", "--input", name) == 0
    assert normalized(gate.program.load()) == normalized(result["predicted_state"])


def test_reconciliation_prepares_all_available_guards_but_never_claims_publication(
    gate, capsys, monkeypatch
):
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
    name = publication_decision(gate, {"action": "cancel-registration", "operation_id": identifier})
    result = preview(gate.fixture, ["publication-reconcile", "--input", name], capsys, monkeypatch)
    assert result["status"] == "not_assessed", result
    assert result["preparation"]["proposed_operation"]["stage"] == "cancelled"
    assert "observed_ledger" not in result["preparation"]
    assert "prepared_partial_ledger" in result["preparation"]
    assert (
        fp.planning.PublicationStore(gate.program.repo).load()["active"][-1]["stage"]
        == "registered"
    )
    assert gate.fixture.run("publication-reconcile", "--input", name) == 0
    assert (
        fp.planning.PublicationStore(gate.program.repo).load()["history"][-1]["stage"]
        == "cancelled"
    )


def complete_tasks(gate):
    gate.ready()
    for task in ("S01", "S02"):
        assert gate.fixture.claim(task) == 0
        gate.fixture.submit(task)
        review = gate.fixture.review(task)
        assert gate.fixture.run("complete", task, "--review", review) == 0


def provider_request(gate, conclusion="success"):
    provider = {
        "branch": "fixture",
        "base": "beta",
        "commits": [HEAD],
        "tested_commit": HEAD,
        "pr_url": "https://example.invalid/pr/12",
        "merge_status": "merged",
        "merge_commit": "b" * 40,
        "ci": [
            {
                "name": "unit",
                "url": "https://example.invalid/run/12",
                "commit": HEAD,
                "conclusion": conclusion,
            }
        ],
    }
    name = gate.fixture.write("provider.json", provider)
    state = gate.program.load()
    bindings = []
    for task_id in ("S01", "S02"):
        attempt = gate.program.current_attempt(gate.program.task(state, task_id))
        bindings.append(
            {
                "task_id": task_id,
                "attempt": attempt["number"],
                "submission_sha256": attempt["submission"]["receipt"]["sha256"],
                "reviewed_source_digest": attempt["submission"]["source_digest"],
                "required_ci": ["unit"],
                "evidence": [gate.fixture.evidence(task_id, label="provider")],
            }
        )
    return {
        "provider": {"path": name, "sha256": fp.sha(gate.program.path(name).read_bytes())},
        "tasks": bindings,
    }


@pytest.mark.parametrize("conclusion", ["success", "failure", "pending"])
def test_two_distinct_delivery_inputs_from_one_frozen_provider_observation(
    gate, capsys, monkeypatch, conclusion
):
    complete_tasks(gate)
    request = provider_request(gate, conclusion)
    request["tasks"][0]["cleanup"] = {
        "status": "deferred",
        "reason": "Keep reviewed branch for investigation",
        "merge_identity": "b" * 40,
        "actor_id": "director",
        "recorded_at": "2026-10-13T00:00:00+00:00",
        "evidence": request["tasks"][0]["evidence"],
    }
    name = gate.fixture.write("delivery-request.json", request)
    before = tree(gate.program.repo)
    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(fp.Program, "lock", forbidden)
        patch.setattr(fp, "git", forbidden)
        patch.setattr(fp, "atomic_write", forbidden)
        assert gate.fixture.run("delivery-inputs", "--input", name) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "not_assessed"
    assert tree(gate.program.repo) == before

    assert len(result["inputs"]) == 2
    assert {row["task_id"] for row in result["inputs"]} == {"S01", "S02"}
    for row in result["inputs"]:
        assert row["remote_verified_by_tool"] is False
        assert row["delivery_accepted"] is False
        expected = {
            "success": "ci_recorded_success",
            "failure": "ci_failed",
            "pending": "ci_pending",
        }[conclusion]
        assert row["ci_status"] == expected
        receipt = gate.fixture.write(
            f"evidence/{row['task_id']}/attempt-1/delivery.json", row["receipt"]
        )
        assert gate.fixture.run("delivery", row["task_id"], "--receipt", receipt) == 0
    state = gate.program.load()
    for task_id in ("S01", "S02"):
        attempt = gate.program.current_attempt(gate.program.task(state, task_id))
        assert request["provider"] not in attempt["evidence"]
        assert request["provider"] in author.delivery_evidence_references(state)
    gate.program.path("provider.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(fp.ProgramError, match="Frozen provider observation changed"):
        gate.program.load()


@pytest.mark.parametrize("artifact", [".feature-program.lock", "RELOCATION_INCOMPLETE.json"])
def test_unreadable_recovery_observations_remain_unknown_and_preserve_files(
    gate, artifact, capsys, monkeypatch
):
    gate.program.path(artifact).write_bytes(b'{"partial":')
    before = tree(gate.program.repo)
    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(fp.Program, "lock", forbidden)
        patch.setattr(fp, "atomic_write", forbidden)
        assert gate.fixture.run("recovery-readiness") == 0
    result = json.loads(capsys.readouterr().out)
    observed = result["lock"] if artifact.startswith(".") else result["markers"][artifact]
    assert observed["status"] == "unavailable"
    assert result["status"] == "attention"
    assert result["liveness"] == "unknown"
    assert result["lock_stealable"] == "not_assessed"
    assert any(item.get("path") == artifact for item in result["findings"])
    assert tree(gate.program.repo) == before


@pytest.mark.parametrize(
    "fault", ["attempt", "submission", "source", "checks", "provider_hash", "cleanup"]
)
def test_delivery_inputs_reject_individual_binding_or_frozen_fact_drift(gate, fault):
    complete_tasks(gate)
    request = provider_request(gate)
    task = request["tasks"][1]
    if fault == "attempt":
        task["attempt"] = 2
    elif fault == "submission":
        task["submission_sha256"] = "0" * 64
    elif fault == "source":
        task["reviewed_source_digest"] = "0" * 64
    elif fault == "checks":
        task["required_ci"] = ["missing"]
    elif fault == "provider_hash":
        request["provider"]["sha256"] = "0" * 64
    else:
        task["cleanup"] = {
            "status": "cleaned",
            "reason": "Claimed cleanup",
            "merge_identity": "c" * 40,
            "actor_id": "director",
            "recorded_at": "2026-10-13T00:00:00+00:00",
            "evidence": task["evidence"],
        }
    before = tree(gate.program.repo)
    with pytest.raises(author._errors()):
        author.delivery_inputs(gate.program, gate.program.load(), request)
    assert tree(gate.program.repo) == before


def test_standalone_cli_preview_and_original_failure_are_readonly(gate):
    root = gate.program.load()["tasks"][0]["workstream"]
    name = gate.fixture.write(
        "categories.json",
        {
            "reason": "Exact synthetic grouping",
            "categories": [{"id": "c", "title": "C", "parent_id": root, "task_ids": ["S01"]}],
        },
    )
    request = gate.fixture.write("preview.json", {"argv": ["category-set", "--input", name]})
    prefix = [
        sys.executable,
        "-B",
        fp.__file__,
        "--repo",
        str(gate.program.repo),
        "--program-dir",
        gate.program.directory.relative_to(gate.program.repo).as_posix(),
    ]
    before = tree(gate.program.repo)
    observed = subprocess.run(
        [*prefix, "preview", "--request", request], capture_output=True, text=True, check=False
    )
    assert observed.returncode == 0, observed.stderr
    proposed = json.loads(observed.stdout)
    assert proposed["status"] == "validated"
    assert tree(gate.program.repo) == before
    actual = subprocess.run(
        [*prefix, "category-set", "--input", name], capture_output=True, text=True, check=False
    )
    assert actual.returncode == 0, actual.stderr
    assert normalized(gate.program.load()) == normalized(proposed["predicted_state"])
    state = gate.program.load()
    del state["objective"]["criteria"]
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    before = tree(gate.program.repo)
    observed = subprocess.run(
        [*prefix, "preview", "--request", request], capture_output=True, text=True, check=False
    )
    actual = subprocess.run(
        [*prefix, "category-set", "--input", name], capture_output=True, text=True, check=False
    )
    assert observed.returncode == 0 and actual.returncode == 1
    rejected = json.loads(observed.stdout)
    assert rejected["status"] == "failed"
    assert actual.stderr.strip() == "feature-program: " + rejected["reasons"][0]
    assert "Traceback" not in observed.stderr + actual.stderr
    assert tree(gate.program.repo) == before


def test_five_template_scaffolds_are_nonpassing_without_claiming_execution(
    gate, capsys, monkeypatch
):
    gate.ready()

    def scaffold(kind):
        before = tree(gate.program.repo)
        capsys.readouterr()
        with monkeypatch.context() as patch:
            patch.setattr(fp.Program, "lock", forbidden)
            patch.setattr(fp, "atomic_write", forbidden)
            assert (
                gate.fixture.run(
                    "scaffold",
                    "--operation",
                    "template",
                    "--kind",
                    kind,
                    "--task",
                    "S01",
                    "--scope",
                    "src/S01.py",
                )
                == 0
            )
        result = json.loads(capsys.readouterr().out)
        assert result["acceptance_complete"] is False
        assert result["status"] == "not_assessed"
        assert tree(gate.program.repo) == before
        return result["receipt"]

    assert scaffold("revalidation")["acceptance_reviewed"] is False
    assert gate.fixture.claim() == 0
    assert all(item["status"] == "not_run" for item in scaffold("execution")["checks"])
    assert (
        gate.fixture.run(
            "block",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Synthetic blocker",
            "--next-action",
            "Resolve fixture",
        )
        == 0
    )
    assert scaffold("resume")["evidence"] == []
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Explicit release",
            "--next-action",
            "New verified attempt",
        )
        == 0
    )
    assert gate.fixture.claim(number=2) == 0
    gate.fixture.submit()
    assert scaffold("review")["verdict"] == "pending"
    review = gate.fixture.review()
    assert gate.fixture.run("complete", "S01", "--review", review) == 0
    receipt = scaffold("delivery")
    assert receipt["tested_commit"] is None
    assert receipt["commits"] == receipt["ci"] == []
    assert receipt["cleanup"]["status"] == "unknown"


@pytest.mark.parametrize("fault", ["missing", "changed", "oversized", "facts"])
def test_provider_reader_guards_authoring_and_actual_delivery_identically(gate, fault, capsys):
    complete_tasks(gate)
    request = provider_request(gate)
    authored = author.delivery_inputs(gate.program, gate.program.load(), request)
    row = authored["inputs"][0]
    if fault == "facts":
        row["receipt"]["branch"] = "invented-branch"
    else:
        path = gate.program.path("provider.json")
        if fault == "missing":
            path.unlink()
        elif fault == "changed":
            path.write_bytes(b"{}\n")
        else:
            path.write_bytes(b"x" * (author.LIMIT + 1))
        with pytest.raises(author._errors()):
            author.delivery_inputs(gate.program, gate.program.load(), request)
    name = gate.fixture.write("evidence/S01/attempt-1/provider-bound-delivery.json", row["receipt"])
    before = tree(gate.program.repo)
    assert gate.fixture.run("delivery", "S01", "--receipt", name) == 1
    assert capsys.readouterr().err
    assert tree(gate.program.repo) == before
