"""Durable local question inbox; core Program owns locking and atomic persistence.

Local answers record unverified operator input, not authenticated human identity.
Reading does not acknowledge questions or launch/wake a director session.
"""

# noqa: SIZE_OK - One owned inbox protocol: validation, lifecycle and its fixed transaction adapter.

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Final, Literal, Protocol, TypedDict

if __package__:
    from tools import feature_program_coordination as coordination
    from tools import feature_program_storage as storage
else:
    import feature_program_coordination as coordination
    import feature_program_storage as storage

Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Record = dict[str, Json]
ErrorCode = Literal["invalid", "conflict", "unknown", "busy"]
MAX_QUESTIONS: Final = 500
MAX_ANSWER: Final = 4000
IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
COMMANDS: Final = frozenset(
    {"question-issue", "question-acknowledge", "question-resolve", "question-withdraw"}
)
STATUSES: Final = frozenset({"open", "answered", "acknowledged", "resolved"})


class QuestionError(ValueError):
    """A bounded input failure or conflicting transition; no state was applied."""

    def __init__(self, message: str, code: ErrorCode = "invalid") -> None:
        super().__init__(message)
        self.code = code


class AnswerReceipt(TypedDict):
    question_id: str
    version: int
    submission_id: str
    answer: str
    saved_at: str
    channel: str
    provenance: str
    status: str


class ProgramAccess(Protocol):
    """Existing safety boundaries used only under the core mutation lock."""

    def path(self, relative: str) -> Path: ...

    def current_head(self) -> str: ...

    def source_digest(self, scope: list[str]) -> str: ...

    def event(self, state: Record, action: str, detail: Record) -> None: ...


def _require(condition: bool, message: str, code: ErrorCode = "invalid") -> None:
    if not condition:
        raise QuestionError(message, code)


def _record(value: Json, label: str) -> Record:
    match value:  # noqa: MATCH_OK - JSON boundary rejects every incompatible input variant.
        case dict():
            return value
        case _:
            raise QuestionError(f"{label} must be an object")


def _items(value: Json, label: str) -> list[Json]:
    match value:  # noqa: MATCH_OK - JSON boundary rejects every incompatible input variant.
        case list():
            return value
        case _:
            raise QuestionError(f"{label} must be an array")


def _text(value: Json, label: str, limit: int = MAX_ANSWER) -> str:
    match value:  # noqa: MATCH_OK - JSON boundary rejects every incompatible input variant.
        case str() if value.strip() and len(value) <= limit:
            _require(
                all(ord(char) >= 32 or char in "\n\r\t" for char in value)
                and not any(0xD800 <= ord(char) <= 0xDFFF for char in value),
                f"{label} contains invalid characters",
            )
            return value
        case _:
            raise QuestionError(f"{label} must be nonempty text of at most {limit} characters")


def _identifier(value: Json, label: str) -> str:
    identifier = _text(value, label, 128)
    _require(IDENTIFIER.fullmatch(identifier) is not None, f"Invalid {label}")
    return identifier


def _version(value: Json) -> int:
    _require(type(value) is int and 0 < value <= MAX_QUESTIONS, "Invalid question version")
    return value


def _timestamp(value: Json) -> None:
    timestamp = _text(value, "timestamp", 64)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QuestionError("Invalid question timestamp") from exc
    _require(parsed.utcoffset() is not None, "Question timestamp requires a timezone")


def _utc() -> str:
    return coordination.observed_timestamp(datetime.now(timezone.utc).isoformat())


def _digest(value: Record) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _texts(value: Json, label: str, count: int, length: int) -> list[str]:
    items = _items(value, label)
    _require(len(items) <= count, f"Too many {label}")
    result = [_text(item, label, length) for item in items]
    _require(len(result) == len(set(result)), f"Duplicate {label}")
    return result


def _binding(value: Record) -> list[str]:
    for field, length in (("source_head", 40), ("source_digest", 64)):
        text = _text(value.get(field), field, length)
        _require(re.fullmatch(f"[a-f0-9]{{{length}}}", text) is not None, f"Invalid {field}")
    scope = _texts(value.get("scope"), "source scope", 200, 512)
    _require(bool(scope), "Source scope cannot be empty")
    for path in scope:
        _require(
            "\\" not in path
            and ":" not in path
            and not path.startswith("/")
            and all(part not in {"", ".", ".."} for part in path.split("/")),
            "Source scope must be portable and relative",
        )
    return scope


def _fresh(program: ProgramAccess, receipt: Record, observed_head: str | None = None) -> None:
    scope = _binding(receipt)
    _require(
        receipt["source_head"]
        == (observed_head if observed_head is not None else program.current_head()),
        "Stale source HEAD",
        "conflict",
    )
    _require(
        receipt["source_digest"] == program.source_digest(scope), "Stale source digest", "conflict"
    )


def _director(state: Record, actor_id: str) -> None:
    # Optional legacy metadata is never manufactured merely by recording a question.
    if not any(key in state for key in ("director", "actors", "handoffs", "objective")):
        return
    director = _record(state.get("director"), "acknowledged current director")
    _require(
        coordination.current_director_id(state) == actor_id,
        "Only current director may change questions",
        "conflict",
    )
    _timestamp(director.get("acknowledged_at"))
    actors = [_record(item, "actor") for item in _items(state.get("actors", []), "actors")]
    _require(
        any(
            actor.get("id") == actor_id
            and actor.get("role") == "director"
            and actor.get("staffing_status", "active") == "active"
            for actor in actors
        ),
        "Current director must be an active recorded director",
        "conflict",
    )


def _questions(state: Record) -> list[Record]:
    if "questions" not in state:
        return []
    section = state["questions"]
    inbox = _record(section, "questions")
    _require(
        type(inbox.get("schema_version")) is int and inbox["schema_version"] == 1,
        "Unsupported question inbox schema",
    )
    items = _items(inbox.get("items"), "question items")
    _require(len(items) <= MAX_QUESTIONS, "Question inbox capacity exceeded")
    return [_record(item, "question") for item in items]


def _target(state: Record, question_id: str, version: int) -> Record:
    matches = [item for item in _questions(state) if item.get("id") == question_id]
    _require(bool(matches), "Unknown question", "unknown")
    _require(matches[-1].get("version") == version, "Stale question version", "conflict")
    return matches[-1]


def _answer(value: Json) -> AnswerReceipt:
    answer = _record(value, "answer receipt")
    identifier = _identifier(answer.get("question_id"), "question ID")
    version = _version(answer.get("version"))
    submission = _identifier(answer.get("submission_id"), "submission ID")
    text = _text(answer.get("answer"), "answer", MAX_ANSWER)
    saved = _text(answer.get("saved_at"), "saved timestamp", 64)
    _timestamp(saved)
    _require(
        answer.get("channel") == "local_web"
        and answer.get("provenance") == "unverified_local_human"
        and answer.get("status") == "answered",
        "Invalid local answer provenance",
    )
    return {
        "question_id": identifier,
        "version": version,
        "submission_id": submission,
        "answer": text,
        "saved_at": saved,
        "channel": "local_web",
        "provenance": "unverified_local_human",
        "status": "answered",
    }


def validate_questions(state: Record) -> None:
    """Reject malformed inboxes while leaving registries without the optional section intact."""
    questions = _questions(state)
    tasks = {_record(item, "task").get("id") for item in _items(state.get("tasks"), "tasks")}
    objectives: dict[str, Record] = {}
    current = state.get("objective")
    if current is not None:
        goal = _record(current, "objective")
        for entry in [*_items(goal.get("history", []), "objective history"), goal]:
            old = _record(entry, "objective")
            objectives[_text(old.get("id"), "objective ID", 128)] = old
    identities: set[str] = set()
    submissions: set[str] = set()
    for question in questions:
        identifier = _identifier(question.get("id"), "question ID")
        version = _version(question.get("version"))
        _require(identifier not in identities, "Question identity already used")
        identities.add(identifier)
        issued = _record(question.get("issued"), "issued question")
        _require(question.get("issued_sha256") == _digest(issued), "Issued question changed")
        _require(
            issued.get("id") == identifier and issued.get("version") == version,
            "Question identity mismatch",
        )
        _text(issued.get("actor_id"), "issuing actor", 128)
        _timestamp(issued.get("issued_at"))
        for field in ("prompt", "why", "requested_decision"):
            _text(issued.get(field), field, 2000)
        _texts(issued.get("options"), "question options", 10, 500)
        affected = _texts(issued.get("affected_task_ids"), "affected task IDs", 200, 128)
        _require(set(affected) <= tasks, "Question refers to unknown tasks")
        objective_id = issued.get("objective_id")
        _require(
            objective_id is not None or bool(affected),
            "Question needs an objective or affected task scope",
        )
        if objective_id is not None:
            identifier_goal = _text(objective_id, "objective ID", 128)
            _require(identifier_goal in objectives, "Question refers to unknown objective")
            _require(
                set(affected)
                <= set(
                    _texts(
                        objectives[identifier_goal].get("task_ids"), "objective tasks", 2000, 128
                    )
                ),
                "Question tasks outside objective",
            )
        _binding(issued)
        status = _text(question.get("status"), "question status", 32)
        _require(status in STATUSES, "Invalid question status")
        answer = question.get("answer")
        acknowledgement = question.get("acknowledgement")
        outcome = question.get("outcome")
        withdrawn = outcome is not None and _record(outcome, "outcome").get("kind") == "withdrawn"
        _require(
            (status == "resolved") or not withdrawn, "Only resolved questions can be withdrawn"
        )
        if not withdrawn:
            has_answer = status in {"answered", "acknowledged", "resolved"}
            _require((answer is not None) == has_answer, "Question answer/status mismatch")
            _require(
                (acknowledgement is not None) == (status in {"acknowledged", "resolved"}),
                "Question acknowledgement/status mismatch",
            )
        _require(
            acknowledgement is None or answer is not None, "Acknowledgement requires an answer"
        )
        _require(
            (outcome is not None) == (status == "resolved"), "Question outcome/status mismatch"
        )
        if answer is not None:
            accepted = _answer(answer)
            _require(
                accepted["question_id"] == question["id"] and accepted["version"] == version,
                "Answer refers to another question",
            )
            _require(accepted["submission_id"] not in submissions, "Duplicate submission identity")
            submissions.add(accepted["submission_id"])
        for receipt in (acknowledgement, outcome):
            if receipt is not None:
                item = _record(receipt, "director question receipt")
                _text(item.get("actor_id"), "director actor", 128)
                _text(item.get("summary"), "director summary")
                _timestamp(item.get("recorded_at"))
                _binding(item)
                _require(
                    item.get("submission_id")
                    == (
                        _record(answer, "answer").get("submission_id")
                        if answer is not None
                        else None
                    ),
                    "Director receipt refers to another answer",
                )
        if outcome is not None:
            _require(
                _record(outcome, "outcome").get("kind") in {"applied", "withdrawn"},
                "Invalid decision outcome kind",
            )
        history = [
            _record(item, "question history")
            for item in _items(question.get("history"), "question history")
        ]
        expected = ["issued"]
        if answer is not None:
            expected.append("answered")
        if acknowledgement is not None:
            expected.append("acknowledged")
        if status == "resolved":
            expected.append("resolved")
        _require(
            [entry.get("action") for entry in history] == expected,
            "Question history/status mismatch",
        )
        for entry in history:
            _timestamp(entry.get("timestamp"))


def pending_questions(
    state: Record, objective_id: str | None = None, task_ids: list[str] | None = None
) -> list[Record]:
    """Return detached unresolved questions for a director checkpoint or completion gate."""
    validate_questions(state)
    pending: list[Record] = []
    for item in _questions(state):
        issued = _record(item["issued"], "issued")
        relevant = objective_id is None or issued.get("objective_id") == objective_id
        if issued.get("objective_id") is None and task_ids is not None:
            relevant = relevant or bool(
                set(_texts(issued["affected_task_ids"], "affected task IDs", 200, 128))
                & set(task_ids)
            )
        if item["status"] in {"open", "answered", "acknowledged"} and relevant:
            pending.append(item)
    return deepcopy(pending)


def snapshot_questions(state: Record) -> list[Record]:
    """Expose bounded detached history; reads never mark answers seen or acknowledged."""
    validate_questions(state)
    return [
        {
            **deepcopy(_record(item["issued"], "issued")),
            **{
                key: deepcopy(value)
                for key, value in item.items()
                if key not in {"issued", "issued_sha256"}
            },
        }
        for item in _questions(state)
    ]


def evidence_references(state: Record) -> list[Json]:
    """Question records are embedded in canonical state, with no external file references."""
    validate_questions(state)
    return []


def add_commands(subparsers) -> None:
    """Add director-only recorded transitions; local web has only an answer operation."""
    for name in sorted(COMMANDS):
        command = subparsers.add_parser(name, help="Record a durable director question transition")
        command.add_argument("--input", required=True, help="Program-relative bounded JSON receipt")


def _issue(program: ProgramAccess, state: Record, receipt: Record, *, observed_head: str) -> None:
    actor_id = _text(receipt.get("actor_id"), "director actor", 128)
    _director(state, actor_id)
    _fresh(program, receipt, observed_head)
    identifier = _identifier(receipt.get("id"), "question ID")
    version = _version(receipt.get("version"))
    items = _questions(state)
    _require(len(items) < MAX_QUESTIONS, "Question inbox capacity exceeded")
    _require(
        not any(item.get("id") == identifier for item in items),
        "Question identity already used; issue a new identity for revised wording",
        "conflict",
    )
    goal = state.get("objective")
    if goal is not None:
        objective = _record(goal, "objective")
        _require(
            objective.get("status") in {"active", "director_review", "awaiting_next_goal"},
            "Invalid current objective lifecycle",
            "conflict",
        )
        _require(
            receipt.get("objective_id") == objective.get("id"),
            "Question must bind current objective",
            "conflict",
        )
    else:
        _require(receipt.get("objective_id") is None, "Question refers to unknown objective")
    issued: Record = {
        key: deepcopy(receipt.get(key))
        for key in (
            "id",
            "version",
            "actor_id",
            "prompt",
            "why",
            "requested_decision",
            "affected_task_ids",
            "objective_id",
            "source_head",
            "source_digest",
            "scope",
        )
    }
    issued["options"] = deepcopy(receipt.get("options", []))
    issued["issued_at"] = _utc()
    items.append(
        {
            "id": identifier,
            "version": version,
            "issued": issued,
            "issued_sha256": _digest(issued),
            "status": "open",
            "answer": None,
            "acknowledgement": None,
            "outcome": None,
            "history": [{"action": "issued", "timestamp": issued["issued_at"]}],
        }
    )
    state["questions"] = {"schema_version": 1, "items": items}


def _director_transition(
    program: ProgramAccess, state: Record, receipt: Record, resolve: bool, *, observed_head: str
) -> None:
    actor_id = _text(receipt.get("actor_id"), "director actor", 128)
    _director(state, actor_id)
    question = _target(
        state,
        _identifier(receipt.get("question_id"), "question ID"),
        _version(receipt.get("version")),
    )
    expected = "acknowledged" if resolve else "answered"
    _require(question["status"] == expected, f"Question must be {expected}", "conflict")
    answer = _answer(question["answer"])
    _require(
        receipt.get("submission_id") == answer["submission_id"], "Stale answer identity", "conflict"
    )
    _fresh(program, receipt, observed_head)
    summary = _text(
        receipt.get("summary"),
        "decision/application summary" if resolve else "acknowledgement summary",
    )
    timestamp = _utc()
    recorded: Record = {
        "actor_id": actor_id,
        "submission_id": answer["submission_id"],
        "summary": summary,
        "recorded_at": timestamp,
        **{key: deepcopy(receipt[key]) for key in ("source_head", "source_digest", "scope")},
    }
    status = "resolved" if resolve else "acknowledged"
    if resolve:
        recorded["kind"] = "applied"
    question["outcome" if resolve else "acknowledgement"] = recorded
    question["status"] = status
    _items(question["history"], "history").append({"action": status, "timestamp": timestamp})


def _withdraw(
    program: ProgramAccess, state: Record, receipt: Record, *, observed_head: str
) -> None:
    actor_id = _text(receipt.get("actor_id"), "director actor", 128)
    _director(state, actor_id)
    question = _target(
        state,
        _identifier(receipt.get("question_id"), "question ID"),
        _version(receipt.get("version")),
    )
    _require(question["status"] != "resolved", "Question is already closed", "conflict")
    _fresh(program, receipt, observed_head)
    timestamp = _utc()
    answer = question.get("answer")
    question["outcome"] = {
        "kind": "withdrawn",
        "actor_id": actor_id,
        "submission_id": _record(answer, "answer")["submission_id"] if answer is not None else None,
        "summary": _text(receipt.get("summary"), "withdrawal reason"),
        "recorded_at": timestamp,
        **{key: deepcopy(receipt[key]) for key in ("source_head", "source_digest", "scope")},
    }
    question["status"] = "resolved"
    _items(question["history"], "history").append({"action": "resolved", "timestamp": timestamp})


def apply_command(program: ProgramAccess, state: Record, args: argparse.Namespace) -> bool:
    """Stage a validated transition without changing state on rejected input."""
    if args.command not in COMMANDS:
        return False
    proposal = prepare_command(program, state, args, observed_head=program.current_head())
    state.clear()
    state.update(proposal)
    return True


def prepare_command(
    program: ProgramAccess, state: Record, args: argparse.Namespace, *, observed_head: str
) -> Record:
    """Stage real question semantics with an explicit HEAD observation and no publication."""
    _require(args.command in COMMANDS, "Unsupported question command")
    _require(
        isinstance(observed_head, str) and re.fullmatch(r"[a-f0-9]{40}", observed_head) is not None,
        "Verified current HEAD observation required",
    )
    if __package__:
        from tools import feature_program as core
    else:
        import feature_program as core
    path = program.path(args.input)
    _require(
        path.is_file() and path.stat().st_size <= 64 * 1024, "Missing or oversized question receipt"
    )
    try:
        receipt = core.read_json(path, limit=64 * 1024)
    except core.ProgramError as exc:
        raise QuestionError(str(exc)) from exc
    proposal = deepcopy(state)
    validate_questions(proposal)
    match args.command:  # noqa: MATCH_OK - CLI boundary explicitly rejects unknown commands.
        case "question-issue":
            _issue(program, proposal, receipt, observed_head=observed_head)
        case "question-acknowledge":
            _director_transition(program, proposal, receipt, False, observed_head=observed_head)
        case "question-resolve":
            _director_transition(program, proposal, receipt, True, observed_head=observed_head)
        case "question-withdraw":
            _withdraw(program, proposal, receipt, observed_head=observed_head)
        case _:
            raise QuestionError("Unsupported question command")
    validate_questions(proposal)
    program.event(
        proposal,
        args.command,
        {
            "actor": receipt.get("actor_id"),
            "id": None,
            "question_id": receipt.get("question_id", receipt.get("id")),
            "version": receipt.get("version"),
            "reason": receipt.get("summary", receipt.get("why")),
            "previous_state": None,
            "new_state": "recorded",
            "evidence": [],
        },
    )
    return proposal


def _stage_answer(state: Record, request: AnswerReceipt) -> tuple[AnswerReceipt, bool]:
    validate_questions(state)
    # Global idempotency identity prevents reuse against another question/version.
    for item in _questions(state):
        existing = item.get("answer")
        if existing is None:
            continue
        original = _answer(existing)
        if original["submission_id"] == request["submission_id"]:
            _require(
                all(original[key] == request[key] for key in ("question_id", "version", "answer")),
                "Conflicting submission identity",
                "conflict",
            )
            return original, False
    question = _target(state, request["question_id"], request["version"])
    _require(question["status"] == "open", "Question is already answered or closed", "conflict")
    question["answer"] = dict(request)
    question["status"] = "answered"
    _items(question["history"], "history").append(
        {"action": "answered", "timestamp": request["saved_at"]}
    )
    validate_questions(state)
    return request, True


def record_answer(
    repo: Path, program_dir: Path, question_id: str, version: int, answer: str, submission_id: str
) -> AnswerReceipt:
    """Save one answer through core's existing lock/atomic save; never execute Git.

    The explicit parameters are the fixed HTTP integration contract. Input is data;
    there are no arbitrary filename, command, native-agent or network operations.
    """
    if __package__:
        from tools import feature_program as core
    else:
        import feature_program as core
    try:
        request = _answer(
            {
                "question_id": question_id,
                "version": version,
                "answer": answer,
                "submission_id": submission_id,
                "saved_at": _utc(),
                "channel": "local_web",
                "provenance": "unverified_local_human",
                "status": "answered",
            }
        )
        repo = repo.absolute()
        directory = program_dir.absolute() if program_dir.is_absolute() else repo / program_dir
        _require(
            directory.is_relative_to(repo) and directory != repo,
            "Program must be within configured repository",
        )
        program = core.Program(repo, core.safe_path(repo, directory.relative_to(repo).as_posix()))
        _require(
            program.path("backlog.json").is_file(), "Program registry is unavailable", "unknown"
        )
        with program.lock():
            backlog = program.path("backlog.json")
            _require(
                backlog.stat().st_size <= storage.RAW_MAX_BYTES,
                "Question registry exceeds 8 MiB plus bounded storage header",
            )
            # Only this append-only answer route defers artifact reads to strict save.
            # Retry has no save, so it must run the same full validation before returning.
            program.require_current_location()
            state = core.parse_registry_state(program.read_registry_observation(), path=backlog)
            program.validate(state, verify_artifacts=False)
            proposal = deepcopy(state)
            result, changed = _stage_answer(proposal, request)
            if changed:
                program.event(
                    proposal,
                    "question-answer",
                    {
                        "actor": "local_human_unverified",
                        "id": None,
                        "question_id": question_id,
                        "version": version,
                        "reason": "Local answer saved; awaiting director acknowledgement",
                        "previous_state": "open",
                        "new_state": "answered",
                        "evidence": [],
                    },
                )
                program.save(proposal)
            else:
                program.validate(state)
            return result
    except QuestionError as exc:
        error = core.ProgramError(str(exc))
        error.code = exc.code
        raise error from exc
