"""Synthetic question lifecycle and real atomic transaction checks; no private records."""

# noqa: SIZE_OK - Cohesive inbox scenarios share one isolated program fixture and ownership scope.

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Barrier, Event

import pytest

from tools import feature_program as core
from tools import feature_program_questions as inbox

HEAD = "a" * 40


@dataclass(frozen=True, slots=True)
class Fixture:
    program: core.Program

    def binding(self) -> inbox.Record:
        return {
            "source_head": HEAD,
            "scope": ["src/task.py"],
            "source_digest": self.program.source_digest(["src/task.py"]),
        }

    def run(self, state: inbox.Record, command: str, receipt: inbox.Record) -> bool:
        path = self.program.path("question-input.json")
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return inbox.apply_command(
            self.program, state, argparse.Namespace(command=command, input=path.name)
        )

    def issue(
        self,
        state: inbox.Record,
        identifier: str = "choice-one",
        objective: str | None = None,
        actor_id: str = "director-one",
        affected: str = "TASK-A",
    ) -> None:
        self.run(
            state,
            "question-issue",
            {
                **self.binding(),
                "id": identifier,
                "version": 1,
                "actor_id": actor_id,
                "prompt": "Which acceptance path should the director use?",
                "options": ["Small scope", "Broader scope"],
                "why": "The chosen path changes the required review scope.",
                "requested_decision": "Select the required scope",
                "affected_task_ids": [affected],
                "objective_id": objective,
            },
        )

    def transition(self, state: inbox.Record, command: str, actor_id: str = "director-one") -> None:
        self.run(
            state,
            command,
            {
                **self.binding(),
                "question_id": "choice-one",
                "version": 1,
                "actor_id": actor_id,
                "submission_id": "response-one",
                "summary": "Applied scoped choice",
            },
        )

    def answer(
        self,
        question_id: str = "choice-one",
        version: int = 1,
        answer: str = "Small scope",
        submission_id: str = "response-one",
    ) -> inbox.AnswerReceipt:
        return inbox.record_answer(
            self.program.repo, self.program.directory, question_id, version, answer, submission_id
        )


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    repo = tmp_path / "repository"
    (repo / "src").mkdir(parents=True)
    (repo / "src/task.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "report.md").write_text(
        "### TASK-A. Primary task\nAcceptance: reviewed scoped result.\n"
        "### TASK-B. Separate task\nAcceptance: independent result.\n",
        encoding="utf-8",
    )
    plan: core.Record = {
        "schema_version": 1,
        "program_id": "synthetic-questions",
        "baseline_head": HEAD,
        "workstreams": [
            {
                "id": "build",
                "name": "Build",
                "default_agent": "worker-one",
                "focus": "Synthetic scope",
            }
        ],
        "tasks": [
            {
                "id": name,
                "workstream": "build",
                "phase": 1,
                "depends_on": [],
                "related_to": [],
                "conflict_groups": [],
                "candidate_paths": ["src/task.py"],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Inspect acceptance",
            }
            for name in ("TASK-A", "TASK-B")
        ],
    }
    (repo / "plan.json").write_bytes(core.canonical(plan))
    monkeypatch.setattr(core, "git", lambda _repo, _arguments: HEAD)
    program = core.Program(repo, repo / "program")
    with program.lock():
        state = program.initialize(
            argparse.Namespace(
                report="report.md",
                planning="plan.json",
                review_baseline="b" * 40,
                reference_base="src",
            )
        )
        program.save(state)
    return Fixture(program)


def question(state: inbox.Record) -> inbox.Record:
    section = state["questions"]
    assert isinstance(section, dict)
    items = section["items"]
    assert isinstance(items, list) and isinstance(items[0], dict)
    return items[0]


def save_issue(fixture: Fixture) -> None:
    with fixture.program.lock():
        state = fixture.program.load()
        fixture.issue(state)
        fixture.program.save(state)


def actor(identifier: str) -> inbox.Record:
    return {"id": identifier, "role": "director", "staffing_status": "active"}


def governance(state: inbox.Record) -> None:
    state["actors"] = [actor("director-one"), actor("director-two")]
    state["director"] = {"actor_id": "director-one", "acknowledged_at": "2025-01-01T00:00:00+00:00"}


def test_legacy_reads_leave_registry_and_views_byte_identical(fixture: Fixture) -> None:
    # Given a legacy program without question metadata.
    state = fixture.program.load()
    before = deepcopy(state)
    files = {
        path.name: path.read_bytes()
        for path in fixture.program.directory.iterdir()
        if path.is_file()
    }
    # When inbox reads and the full snapshot are requested.
    assert inbox.snapshot_questions(state) == []
    assert inbox.pending_questions(state) == []
    assert core.read_snapshot(fixture.program.repo, fixture.program.directory)["questions"] == []
    # Then no optional section, acknowledgement or view mutation is manufactured.
    assert state == before and "questions" not in state
    assert files == {
        path.name: path.read_bytes()
        for path in fixture.program.directory.iterdir()
        if path.is_file()
    }


def test_answer_receipt_acknowledgement_and_outcome_are_distinct(fixture: Fixture) -> None:
    # Given a director-issued durable question.
    save_issue(fixture)
    original_tasks = deepcopy(fixture.program.load()["tasks"])
    # When the local response is saved, read and explicitly acknowledged/resolved.
    receipt = fixture.answer()
    state = fixture.program.load()
    assert question(state)["status"] == "answered"
    assert question(state)["acknowledgement"] is None
    snapshot = inbox.snapshot_questions(state)
    snapshot[0]["prompt"] = "Detached snapshot"
    assert question(state)["status"] == "answered"
    fixture.transition(state, "question-acknowledge")
    assert question(state)["status"] == "acknowledged"
    assert question(state)["outcome"] is None
    fixture.transition(state, "question-resolve")
    # Then original task history stays intact and only the director's outcome closes the gate.
    assert state["tasks"] == original_tasks
    assert question(state)["status"] == "resolved"
    assert question(state)["outcome"]["kind"] == "applied"
    assert receipt["channel"] == "local_web"
    assert receipt["provenance"] == "unverified_local_human"
    assert inbox.pending_questions(state) == []
    assert [entry["action"] for entry in question(state)["history"]] == [
        "issued",
        "answered",
        "acknowledged",
        "resolved",
    ]


def test_successful_retries_return_original_receipt_after_resolution(fixture: Fixture) -> None:
    # Given a saved response that has since been acknowledged and applied.
    save_issue(fixture)
    original = fixture.answer()
    with fixture.program.lock():
        state = fixture.program.load()
        fixture.transition(state, "question-acknowledge")
        fixture.transition(state, "question-resolve")
        fixture.program.save(state)
    before = fixture.program.path("backlog.json").read_bytes()
    # When the same submission identity and exact payload are retried.
    repeated = fixture.answer()
    # Then original receipt/time is returned without rewriting registry or history.
    assert repeated == original
    assert fixture.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "question_id,version,answer,submission_id,code",
    [
        ("missing", 1, "Small scope", "new-response", "unknown"),
        ("choice-one", 2, "Small scope", "new-response", "conflict"),
        ("choice-one", True, "Small scope", "new-response", "invalid"),
        ("choice-one", 1, "", "new-response", "invalid"),
        ("choice-one", 1, " " * 30, "new-response", "invalid"),
        ("choice-one", 1, "x" * 4001, "new-response", "invalid"),
        ("choice-one", 1, "invalid\x00payload", "new-response", "invalid"),
        ("choice-one", 1, "invalid\ud800payload", "new-response", "invalid"),
        ("choice-one", 1, "Small scope", "../response", "invalid"),
    ],
)
def test_invalid_or_stale_answers_preserve_all_bytes(
    fixture: Fixture, question_id: str, version: int, answer: str, submission_id: str, code: str
) -> None:
    # Given an open question and a frozen original registry.
    save_issue(fixture)
    before = fixture.program.path("backlog.json").read_bytes()
    # When malformed, unknown or stale data is submitted.
    with pytest.raises(core.ProgramError) as rejected:
        fixture.answer(question_id, version, answer, submission_id)
    # Then rejection is typed and all durable bytes remain unchanged.
    assert rejected.value.code == code
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_conflicting_retry_and_second_response_cannot_replace_answer(fixture: Fixture) -> None:
    # Given an accepted local response.
    save_issue(fixture)
    fixture.answer()
    before = fixture.program.path("backlog.json").read_bytes()
    # When either the identity is reused with different text or another answer is attempted.
    for submission in ("response-one", "response-two"):
        with pytest.raises(core.ProgramError) as rejected:
            fixture.answer(answer="Broader scope", submission_id=submission)
        assert rejected.value.code == "conflict"
    # Then the first answer and all durable history remain authoritative.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_submission_identity_cannot_be_reused_for_another_question(fixture: Fixture) -> None:
    # Given two questions and a response to the first.
    save_issue(fixture)
    fixture.answer()
    with fixture.program.lock():
        state = fixture.program.load()
        fixture.issue(state, "choice-two")
        fixture.program.save(state)
    before = fixture.program.path("backlog.json").read_bytes()
    # When the successful submission identity is reused against the second question.
    with pytest.raises(core.ProgramError) as rejected:
        fixture.answer(question_id="choice-two")
    # Then the second question remains open and the first receipt is preserved.
    assert rejected.value.code == "conflict"
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_current_director_replacement_preserves_question_and_rejects_predecessor(
    fixture: Fixture,
) -> None:
    # Given a question issued under acknowledged director governance.
    state = fixture.program.load()
    governance(state)
    fixture.issue(state)
    issued = deepcopy(question(state)["issued"])
    # When acknowledged authority moves to a successor.
    state["director"] = {"actor_id": "director-two", "acknowledged_at": "2025-01-02T00:00:00+00:00"}
    before = deepcopy(state)
    with pytest.raises(inbox.QuestionError, match="Only current director"):
        fixture.transition(state, "question-withdraw")
    assert state == before
    fixture.transition(state, "question-withdraw", actor_id="director-two")
    # Then only the successor records the outcome without rewriting original authorship.
    assert question(state)["issued"] == issued
    assert question(state)["outcome"]["actor_id"] == "director-two"
    assert question(state)["answer"] is None


def test_withdrawal_records_reason_without_fabricating_answer_and_rejects_late_response(
    fixture: Fixture,
) -> None:
    # Given an obsolete question closed by the director with an explicit reason.
    save_issue(fixture)
    with fixture.program.lock():
        state = fixture.program.load()
        fixture.transition(state, "question-withdraw")
        fixture.program.save(state)
    before = fixture.program.path("backlog.json").read_bytes()
    # When a response to the old question arrives after withdrawal.
    with pytest.raises(core.ProgramError) as rejected:
        fixture.answer()
    # Then no user approval is fabricated and withdrawal stays visible in durable history.
    saved = question(fixture.program.load())
    assert rejected.value.code == "conflict"
    assert saved["status"] == "resolved"
    assert saved["answer"] is None and saved["acknowledgement"] is None
    assert saved["outcome"]["kind"] == "withdrawn"
    assert saved["outcome"]["summary"] == "Applied scoped choice"
    assert [item["action"] for item in saved["history"]] == ["issued", "resolved"]
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_new_objective_cycle_preserves_old_questions_and_filters_relevant_pending(
    fixture: Fixture,
) -> None:
    # Given a completed prior objective question and a pending legacy task question.
    state = fixture.program.load()
    fixture.issue(state, "legacy-choice")
    governance(state)
    state["objective"] = {
        "id": "goal-old",
        "status": "active",
        "task_ids": ["TASK-A"],
        "history": [],
    }
    fixture.issue(state, objective="goal-old")
    fixture.transition(state, "question-withdraw")
    prior = deepcopy(question(state))
    old = deepcopy(state["objective"])
    old["status"] = "awaiting_next_goal"
    # When a new human objective is explicitly recorded, retaining the old cycle.
    state["objective"] = {
        "id": "goal-new",
        "status": "active",
        "task_ids": ["TASK-B"],
        "history": [old],
    }
    fixture.issue(state, "new-choice", objective="goal-new", affected="TASK-B")
    # Then questions stay intact; unrelated prior task scope does not block the new goal.
    assert question(state) == prior
    pending = inbox.pending_questions(state, "goal-new", ["TASK-B"])
    assert [item["id"] for item in pending] == ["new-choice"]
    assert [item["id"] for item in inbox.pending_questions(state, "goal-other", ["TASK-A"])] == [
        "legacy-choice"
    ]


@pytest.mark.parametrize("command", ["question-acknowledge", "question-resolve"])
def test_director_cannot_skip_response_or_acknowledgement(fixture: Fixture, command: str) -> None:
    # Given a question that has no human response.
    state = fixture.program.load()
    fixture.issue(state)
    before = deepcopy(state)
    # When the director attempts a later lifecycle stage.
    with pytest.raises(inbox.QuestionError):
        fixture.transition(state, command)
    # Then neither answer nor acknowledgement is inferred.
    assert state == before


def test_stale_source_and_reissued_identity_reject_without_mutation(fixture: Fixture) -> None:
    # Given a recorded question and original source context.
    state = fixture.program.load()
    fixture.issue(state)
    before = deepcopy(state)
    # When another issue reuses the identity or stale source HEAD.
    with pytest.raises(inbox.QuestionError, match="identity already used"):
        fixture.issue(state)
    with pytest.raises(inbox.QuestionError, match="Stale source HEAD"):
        fixture.run(
            state,
            "question-issue",
            {
                **fixture.binding(),
                "source_head": "c" * 40,
                "actor_id": "director-one",
                "id": "new-choice",
                "version": 1,
            },
        )
    # Then existing question and task histories are unchanged.
    assert state == before


def test_local_answer_executes_no_git_or_native_commands(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an already issued question.
    save_issue(fixture)

    def forbidden(_repo: Path, _arguments: list[str]) -> str:
        pytest.fail("Answering must not execute Git")

    monkeypatch.setattr(core, "git", forbidden)
    # When local input is recorded.
    receipt = fixture.answer(answer="Literal data: $(command); <script>ignored</script>\n\uac00")
    # Then the exact text is data only, with a durable receipt.
    assert receipt["answer"].endswith("\n\uac00")
    assert fixture.program.load()["tasks"][0]["status"] == "planned"


def test_competing_answers_never_overwrite_and_busy_writer_can_retry(fixture: Fixture) -> None:
    # Given one question and two simultaneous distinct responses.
    save_issue(fixture)
    barrier = Barrier(2)

    def submit(identifier: str) -> inbox.AnswerReceipt | core.ProgramError:
        barrier.wait(timeout=5)
        try:
            return fixture.answer(answer=identifier, submission_id=identifier)
        except core.ProgramError as exc:
            return exc

    # When both threads reach the core exclusive transaction boundary.
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, ["response-a", "response-b"]))
    # Then exactly one durable receipt exists; the loser gets explicit conflict/busy.
    successes = [item for item in outcomes if isinstance(item, dict)]
    failures = [item for item in outcomes if isinstance(item, core.ProgramError)]
    assert len(successes) == 1 and len(failures) == 1
    saved = question(fixture.program.load())
    assert saved["answer"]["submission_id"] == successes[0]["submission_id"]
    assert len(saved["history"]) == 2
    assert not fixture.program.path(".feature-program.lock").exists()


def test_lock_owner_is_preserved_and_answer_retry_succeeds_after_release(fixture: Fixture) -> None:
    # Given an existing active transaction whose lock is held by another thread.
    save_issue(fixture)
    acquired = Event()
    released = Event()

    def holder() -> None:
        with fixture.program.lock():
            acquired.set()
            assert released.wait(timeout=5)

    # When an answer is attempted while that owner holds the lock.
    with ThreadPoolExecutor(max_workers=1) as executor:
        held = executor.submit(holder)
        assert acquired.wait(timeout=5)
        lock_before = fixture.program.path(".feature-program.lock").read_bytes()
        try:
            with pytest.raises(core.ProgramError, match="Program busy"):
                fixture.answer()
            assert fixture.program.path(".feature-program.lock").read_bytes() == lock_before
        finally:
            released.set()
        held.result(timeout=5)
    # Then normal explicit retry saves the response after the owner releases safely.
    assert fixture.answer()["status"] == "answered"


def test_rejected_save_keeps_original_canonical_bytes(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an open question and a simulated filesystem failure before core atomic replace.
    save_issue(fixture)
    before = fixture.program.path("backlog.json").read_bytes()

    def failed_write(_path: Path, _payload: bytes) -> None:
        raise OSError("Synthetic atomic-write failure")

    monkeypatch.setattr(core, "atomic_write", failed_write)
    # When the answer transaction cannot commit.
    with pytest.raises(OSError, match="Synthetic atomic-write failure"):
        fixture.answer()
    # Then no partial response reaches canonical state and the lock is released.
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert not fixture.program.path(".feature-program.lock").exists()


def test_withdrawal_preserves_prior_answer_and_exact_retry(fixture: Fixture) -> None:
    # Given an answered question that the director later determines is obsolete.
    save_issue(fixture)
    receipt = fixture.answer()
    with fixture.program.lock():
        state = fixture.program.load()
        fixture.transition(state, "question-withdraw")
        fixture.program.save(state)
    # When the accepted original submission is retried after withdrawal.
    repeated = fixture.answer()
    # Then the original response survives and no implicit acknowledgement is added.
    assert repeated == receipt
    closed = question(fixture.program.load())
    assert closed["answer"] == receipt
    assert closed["acknowledgement"] is None
    assert closed["outcome"]["kind"] == "withdrawn"
    assert [entry["action"] for entry in closed["history"]] == ["issued", "answered", "resolved"]


def test_unacknowledged_governance_rejects_issuing_without_state_change(fixture: Fixture) -> None:
    # Given registered director actors whose authority has no acknowledgement.
    state = fixture.program.load()
    governance(state)
    del state["director"]["acknowledged_at"]
    before = deepcopy(state)
    # When that actor attempts to issue a director question.
    with pytest.raises(inbox.QuestionError):
        fixture.issue(state)
    # Then the optional question inbox is not created.
    assert state == before and "questions" not in state


@pytest.mark.parametrize(
    "section",
    [None, [], {"schema_version": True, "items": []}, {"schema_version": 1, "items": "wrong"}],
)
def test_malformed_optional_section_rejects_without_repair(
    fixture: Fixture, section: inbox.Json
) -> None:
    # Given a malformed optional section rather than a legacy missing section.
    state = fixture.program.load()
    state["questions"] = section
    before = deepcopy(state)
    # When any question view is requested.
    with pytest.raises(inbox.QuestionError):
        inbox.snapshot_questions(state)
    # Then reading refuses malformed data and does not silently repair it.
    assert state == before


def test_completed_goal_question_stays_historical_when_next_goal_starts(fixture: Fixture) -> None:
    # Given the director asking about the next human goal after completed acceptance.
    state = fixture.program.load()
    governance(state)
    state["objective"] = {
        "id": "goal-old",
        "status": "awaiting_next_goal",
        "task_ids": ["TASK-A"],
        "history": [],
    }
    fixture.issue(state, objective="goal-old")
    prior = deepcopy(question(state))
    # When an independently authorized new cycle is recorded for the same task IDs.
    old = deepcopy(state["objective"])
    state["objective"] = {
        "id": "goal-new",
        "status": "active",
        "task_ids": ["TASK-A"],
        "history": [old],
    }
    # Then an unresolved old-cycle question stays visible without blocking the new goal.
    assert [item["id"] for item in inbox.pending_questions(state)] == ["choice-one"]
    assert inbox.pending_questions(state, "goal-new", ["TASK-A"]) == []
    assert question(state) == prior


def test_unscoped_question_rejects_instead_of_creating_ambiguous_blocker(fixture: Fixture) -> None:
    # Given a focused issue receipt with neither task nor objective scope.
    state = fixture.program.load()
    before = deepcopy(state)
    receipt = {
        **fixture.binding(),
        "id": "choice-one",
        "version": 1,
        "actor_id": "director-one",
        "prompt": "Select the next step",
        "why": "Scope is unclear",
        "requested_decision": "Choose scope",
        "affected_task_ids": [],
        "objective_id": None,
    }
    # When the director attempts to persist it.
    with pytest.raises(inbox.QuestionError, match="objective or affected task scope"):
        fixture.run(state, "question-issue", receipt)
    # Then no ambiguous pending decision is manufactured.
    assert state == before


def test_duplicate_json_keys_in_director_receipt_reject_without_mutation(fixture: Fixture) -> None:
    # Given otherwise valid director input whose actor field appears twice.
    fixture.issue(fixture.program.load())
    path = fixture.program.path("question-input.json")
    raw = path.read_bytes()
    path.write_bytes(b'{"actor_id":"different-actor",' + raw[1:])
    state = fixture.program.load()
    before = deepcopy(state)
    backlog = fixture.program.path("backlog.json").read_bytes()
    # When the receipt crosses the command JSON boundary.
    with pytest.raises(inbox.QuestionError, match="Duplicate JSON key"):
        inbox.apply_command(
            fixture.program, state, argparse.Namespace(command="question-issue", input=path.name)
        )
    # Then duplicate-key ambiguity never reaches the transition or canonical save.
    assert state == before
    assert fixture.program.path("backlog.json").read_bytes() == backlog


def test_director_receipt_growth_after_stat_is_bounded(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given valid small director input that grows after the metadata check.
    fixture.issue(fixture.program.load())
    path = fixture.program.path("question-input.json")
    raw = path.read_bytes()
    state = fixture.program.load()
    before = deepcopy(state)
    backlog = fixture.program.path("backlog.json").read_bytes()
    original_open = Path.open

    def growing_open(candidate: Path, mode: str = "r", *arguments, **options):
        if candidate == path and mode in {"r", "rb"}:
            with original_open(candidate, "wb") as writer:
                writer.write(raw + b" " * (64 * 1024))
        return original_open(candidate, mode, *arguments, **options)

    monkeypatch.setattr(Path, "open", growing_open)
    # When the file opens with more bytes than the director receipt limit.
    with pytest.raises(inbox.QuestionError, match="bounded read"):
        inbox.apply_command(
            fixture.program, state, argparse.Namespace(command="question-issue", input=path.name)
        )
    # Then actual read allocation stays capped and no durable response is applied.
    assert state == before
    assert fixture.program.path("backlog.json").read_bytes() == backlog
