"""Optional local governance; the Program owner controls locking and persistence.

Recorded actors are identities supplied by the operator, not authenticated people
or verified live processes. Transfers never release or reclaim task ownership.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
from typing import Final, Protocol, TypedDict

Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Record = dict[str, Json]

ROLES: Final = frozenset({"director", "sub_director", "worker", "reviewer"})
OBSERVATIONS: Final = frozenset({"unknown", "running", "idle", "completed", "unavailable"})
COMMANDS: Final = frozenset(
    {
        "category-set",
        "actor-record",
        "director-appoint",
        "transfer",
        "transfer-ack",
        "transfer-accept",
        "objective-record",
        "objective-complete",
    }
)
RESPONSIBILITIES: Final[dict[str, list[str]]] = {
    "director": [
        "Prioritize, assign and resolve scope conflicts; record staffing decisions.",
        "Verify independent review, current source and actual applicable CI before integration.",
        "Exercise only recorded human merge authority; never infer authority from inactivity.",
        "Decompose the supplied human objective and decide completion from acceptance evidence.",
        "After completion, await the user's next major objective; never invent a new goal.",
    ],
    "sub_director": [
        "Audit coverage, dependencies, decisions, evidence and continuity.",
        "Manage delegated staffing; report gaps to the final director.",
        "Do not inherit final integration or merge authority through this role.",
    ],
    "worker": [
        "Implement only claimed scope, preserve other changes and collect acceptance evidence.",
        "Transfer findings before release; successors revalidate and reclaim a new attempt.",
    ],
    "reviewer": [
        "Assess exact submitted source and evidence independently from the implementer.",
        "Report unresolved findings; a recorded review is not authenticated external verification.",
    ],
}


class GovernanceError(ValueError):
    """Invalid optional governance or a rejected recorded transition."""


class ProgramAccess(Protocol):
    """Existing Program safety boundaries used only inside its mutation lock."""

    def path(self, name: str) -> Path: ...

    def current_head(self) -> str: ...

    def source_digest(self, scope: list[str]) -> str: ...

    def evidence(self, task_id: str, attempt: int, names: list[str]) -> list[Json]: ...

    def verify_refs(self, refs: list[Json]) -> None: ...

    def event(self, state: Record, action: str, detail: Record) -> None: ...

    def current_delivery(self, task: Record) -> Record: ...

    def verify_terminal(self, task: Record, attempt: Record) -> None: ...

    def pending_questions(
        self, state: Record, objective_id: str, task_ids: list[str]
    ) -> list[Record]: ...


class Metadata(TypedDict):
    categories: list[Json]
    actors: list[Json]
    handoffs: list[Json]
    director: Json
    responsibilities: dict[str, list[str]]
    objective: Json


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GovernanceError(message)


def _record(value: Json, name: str) -> Record:
    match value:
        case dict():
            return value
        case _:
            raise GovernanceError(f"{name} must be an object")


def _items(value: Json, name: str) -> list[Json]:
    match value:
        case list():
            return value
        case _:
            raise GovernanceError(f"{name} must be an array")


def _text(value: Json, name: str) -> str:
    match value:
        case str() if value.strip():
            return value
        case _:
            raise GovernanceError(f"{name} must be nonempty text")


def _texts(value: Json, name: str) -> list[str]:
    return [_text(item, name) for item in _items(value, name)]


def _optional_text(value: Json, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _choice(value: Json, allowed: frozenset[str] | set[str], name: str) -> str:
    text = _text(value, name)
    _require(text in allowed, f"Invalid {name}")
    return text


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Json, name: str) -> None:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError(f"Invalid {name}") from exc
    _require(parsed.utcoffset() is not None, f"{name} must include a timezone")


def _relative(value: Json) -> str:
    name = _text(value, "relative path")
    path = PurePosixPath(name)
    _require(
        "\\" not in name
        and ":" not in name
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in name.split("/")),
        "Paths must be portable and relative",
    )
    return name


def _indexed(values: Json, name: str) -> dict[str, Record]:
    result: dict[str, Record] = {}
    for value in _items(values, name):
        item = _record(value, name)
        identifier = _text(item.get("id"), f"{name} ID")
        _require(identifier not in result, f"Duplicate {name} ID: {identifier}")
        result[identifier] = item
    return result


def _parents(items: dict[str, Record], name: str) -> None:
    for identifier in items:
        visited: set[str] = set()
        current: str | None = identifier
        while current is not None:
            _require(current in items, f"Unknown {name} parent: {current}")
            _require(current not in visited, f"{name} cycle at {current}")
            visited.add(current)
            current = _optional_text(items[current].get("parent_id"), f"{name} parent")


def _categories(state: Record) -> list[Json]:
    """Existing workstreams remain roots; uncovered tasks keep their root membership."""
    tasks = _indexed(state.get("tasks"), "task")
    streams = _indexed(state.get("workstreams", []), "workstream")
    categories = deepcopy(_indexed(state.get("categories", []), "category"))
    for identifier, stream in streams.items():
        categories.setdefault(
            identifier,
            {
                "id": identifier,
                "title": stream.get("title", stream.get("name", identifier)),
                "parent_id": None,
                "task_ids": [],
            },
        )
        _require(categories[identifier].get("parent_id") is None, "Workstreams must remain roots")
    _parents(categories, "Category")
    assigned: set[str] = set()
    for identifier, category in categories.items():
        _text(category.get("title"), "category title")
        members = _texts(category.get("task_ids", []), "category task IDs")
        _require(len(members) == len(set(members)), "Duplicate category task membership")
        root = identifier
        while categories[root].get("parent_id") is not None:
            root = _text(categories[root]["parent_id"], "category parent")
        _require(root in streams, f"Category must belong to a workstream: {identifier}")
        for task_id in members:
            _require(task_id in tasks, f"Unknown category task: {task_id}")
            _require(task_id not in assigned, f"Task belongs to multiple categories: {task_id}")
            _require(tasks[task_id].get("workstream") == root, "Category/workstream mismatch")
            assigned.add(task_id)
        category["task_ids"] = list(members)
    for task_id, task in tasks.items():
        stream_id = _text(task.get("workstream"), "task workstream")
        _require(stream_id in categories, f"Unknown task workstream: {stream_id}")
        if task_id not in assigned:
            _items(categories[stream_id]["task_ids"], "category task IDs").append(task_id)
    return list(categories.values())


def _actor_fields(actor: Record) -> None:
    _choice(actor.get("role"), ROLES, "actor role")
    _optional_text(actor.get("parent_id"), "actor parent")
    provenance = _choice(
        actor.get("provenance", "unknown"),
        {"verified_launch", "inherited", "unknown"},
        "provenance",
    )
    if provenance == "verified_launch":
        _text(actor.get("model_id"), "exact model ID")
        _text(actor.get("reasoning_effort"), "reasoning effort")
    else:
        _require(
            actor.get("model_id") is None and actor.get("reasoning_effort") is None,
            "Unverified or inherited model settings must remain explicitly unknown",
        )
    status = _choice(
        actor.get("observed_status", "unknown"), OBSERVATIONS, "observed runtime status"
    )
    observed = actor.get("observed_at")
    if observed is not None:
        _timestamp(observed, "observation timestamp")
    _require(
        status == "unknown" or observed is not None, "Runtime observation requires a timestamp"
    )
    _choice(actor.get("staffing_status", "active"), {"active", "retired"}, "staffing status")


def _binding_fields(value: Record) -> list[str]:
    _require(
        bool(re.fullmatch(r"[a-f0-9]{40}", _text(value.get("source_head"), "source HEAD"))),
        "Invalid source HEAD",
    )
    _require(
        bool(re.fullmatch(r"[a-f0-9]{64}", _text(value.get("source_digest"), "source digest"))),
        "Invalid source digest",
    )
    scope = [_relative(path) for path in _items(value.get("scope"), "scope")]
    _require(bool(scope) and len(scope) == len(set(scope)), "Scope must be nonempty and unique")
    return sorted(scope)


def _authority(value: Json) -> Record:
    authority = _record(value, "explicit human authority")
    _text(authority.get("authorized_by"), "authority authorizer")
    _text(authority.get("basis"), "authority basis")
    _require(
        _optional_text(authority.get("merge_target"), "merge target") in {None, "beta"},
        "Unsupported recorded merge target",
    )
    return authority


def validate_governance(state: Record) -> None:
    """Validate optional metadata without loading files or altering legacy state."""
    _categories(state)
    actors = _indexed(state.get("actors", []), "actor")
    _parents(actors, "Actor")
    for task in _indexed(state["tasks"], "task").values():
        for value in _items(task.get("attempts", []), "attempts"):
            attempt = _record(value, "attempt")
            assignment = attempt.get("assignment")
            if assignment is not None:
                metadata = _record(assignment, "assignment metadata")
                _require(
                    metadata.get("actor_id") == attempt.get("owner"), "Assignment actor mismatch"
                )
                _actor_fields(metadata)
    for actor in actors.values():
        _actor_fields(actor)
        parent = actor.get("parent_id")
        if parent is not None:
            _require(
                actors[_text(parent, "parent")].get("role") in {"director", "sub_director"},
                "Actor parent must have a management role",
            )
            _require(actor.get("role") != "director", "Final director cannot have a manager")
        for item in _items(actor.get("history", []), "actor history"):
            entry = _record(item, "actor history")
            _choice(
                entry.get("decision"), {"new", "reuse", "replace", "retire"}, "staffing decision"
            )
            _text(entry.get("reason"), "staffing decision reason")
            _timestamp(entry.get("timestamp"), "staffing timestamp")
            for key in ("predecessor_id", "successor_id"):
                target = _optional_text(entry.get(key), key)
                _require(target is None or target in actors, "Unknown staffing actor reference")
    director = state.get("director")
    if director is not None:
        leader = _record(director, "single current director")
        for historical in [*_items(leader.get("history", []), "director history"), leader]:
            entry = _record(historical, "director history record")
            actor_id = _text(entry.get("actor_id"), "director actor")
            _require(
                actor_id in actors and actors[actor_id].get("role") == "director",
                "Unknown director actor",
            )
            _authority(entry.get("authority"))
            _binding_fields(entry)
            _timestamp(entry.get("acknowledged_at"), "director acknowledgement")
            _require(
                bool(_items(entry.get("evidence"), "director evidence")),
                "Director authority lacks frozen acknowledgement evidence",
            )
        _require(
            actors[_text(leader["actor_id"], "director actor")].get("staffing_status", "active")
            != "retired",
            "Current director cannot be retired",
        )
    handoffs = _indexed(state.get("handoffs", []), "handoff")
    for handoff in handoffs.values():
        outgoing = _record(handoff.get("outgoing"), "outgoing receipt")
        role = _choice(handoff.get("role"), {"worker", "director", "sub_director"}, "transfer role")
        for key in ("from_actor", "to_actor"):
            actor_id = _text(handoff.get(key), "transfer actor")
            _require(
                actor_id in actors and actors[actor_id].get("role") == role,
                "Unknown/mismatched transfer actor",
            )
        _require(handoff["from_actor"] != handoff["to_actor"], "Transfer needs a successor")
        _binding_fields(outgoing)
        for key in ("goal", "next_action"):
            _text(outgoing.get(key), key)
        for key in ("constraints", "changed_paths", "decisions", "unresolved_findings", "evidence"):
            _texts(outgoing.get(key), key)
        _record(handoff.get("outgoing_ref"), "outgoing evidence reference")
        _require(
            handoff["outgoing_ref"] in _items(handoff.get("evidence"), "handoff evidence"),
            "Outgoing receipt lacks frozen evidence",
        )
        if role == "worker":
            tasks = _indexed(state["tasks"], "task")
            task_id = _text(handoff.get("task_id"), "handoff task")
            attempt = handoff.get("attempt")
            _require(task_id in tasks, "Unknown handoff task")
            attempts = _items(tasks[task_id].get("attempts"), "task attempts")
            _require(
                type(attempt) is int and 0 < attempt <= len(attempts), "Invalid handoff attempt"
            )
            _require(
                _record(attempts[attempt - 1], "handoff attempt").get("owner")
                == handoff["from_actor"],
                "Handoff predecessor attempt mismatch",
            )
        if role == "director":
            _authority(outgoing.get("authority"))
        ack = handoff.get("acknowledgement")
        if ack is not None:
            receipt = _record(ack, "successor acknowledgement")
            _require(
                receipt.get("actor_id") == handoff["to_actor"]
                and receipt.get("transfer_id") == handoff["id"],
                "Acknowledgement identity mismatch",
            )
            _require(
                _binding_fields(receipt) == _binding_fields(outgoing),
                "Acknowledgement scope mismatch",
            )
            _text(receipt.get("summary"), "fresh validation summary")
            _record(handoff.get("ack_ref"), "acknowledgement evidence reference")
            _require(
                handoff["ack_ref"] in _items(handoff["evidence"], "handoff evidence"),
                "Acknowledgement lacks frozen evidence",
            )
        revisions = _items(handoff.get("acknowledgements", []), "acknowledgement history")
        for value in revisions:
            revision = _record(value, "acknowledgement revision")
            receipt = _record(revision.get("receipt"), "historical acknowledgement")
            _require(
                receipt.get("actor_id") == handoff["to_actor"]
                and receipt.get("transfer_id") == handoff["id"],
                "Historical acknowledgement identity mismatch",
            )
            _require(
                _binding_fields(receipt) == _binding_fields(outgoing),
                "Historical acknowledgement scope mismatch",
            )
            _text(receipt.get("summary"), "historical validation summary")
            _timestamp(revision.get("timestamp"), "acknowledgement revision timestamp")
            _require(
                revision.get("ref") in _items(handoff["evidence"], "handoff evidence"),
                "Historical acknowledgement lacks frozen evidence",
            )
        if revisions:
            latest = _record(revisions[-1], "latest acknowledgement revision")
            _require(
                latest["receipt"] == ack and latest["ref"] == handoff.get("ack_ref"),
                "Latest acknowledgement pointer mismatch",
            )
        accepted = handoff.get("acceptance")
        if accepted is not None:
            acceptance = _record(accepted, "accepted succession")
            _require(ack is not None, "Succession requires successor acknowledgement")
            _require(
                _binding_fields(acceptance) == _binding_fields(outgoing), "Accepted scope mismatch"
            )
            _text(acceptance.get("summary"), "succession decision")
            _require(
                handoff.get("accept_ref") in _items(handoff["evidence"], "handoff evidence"),
                "Acceptance lacks frozen evidence",
            )
    _validate_objective(state.get("objective"), set(_indexed(state["tasks"], "task")))
    evidence_references(state)


def evidence_references(state: Record) -> list[Json]:
    """Return frozen references for the core Program's normal integrity checks."""
    refs: list[Json] = []
    director = state.get("director")
    if director is not None:
        leader = _record(director, "director")
        for entry in [*_items(leader.get("history", []), "director history"), leader]:
            refs.extend(
                _items(_record(entry, "director record").get("evidence", []), "director evidence")
            )
    for value in _items(state.get("handoffs", []), "handoffs"):
        handoff = _record(value, "handoff")
        refs.extend(_items(handoff.get("evidence", []), "handoff evidence"))
    objective = state.get("objective")
    if objective is not None:
        goal = _record(objective, "objective")
        for entry in [*_items(goal.get("history", []), "objective history"), goal]:
            refs.extend(
                _items(_record(entry, "objective record").get("evidence", []), "objective evidence")
            )
    for value in refs:
        ref = _record(value, "frozen evidence reference")
        _relative(ref.get("path"))
        _require(
            bool(re.fullmatch(r"[a-f0-9]{64}", _text(ref.get("sha256"), "evidence hash"))),
            "Invalid frozen evidence hash",
        )
    return refs


def snapshot_metadata(state: Record) -> Metadata:
    """Detach metadata; saved assignments never imply verified runtime liveness."""
    validate_governance(state)
    actors = deepcopy(_indexed(state.get("actors", []), "actor"))
    for actor in actors.values():
        actor.setdefault("parent_id", None)
        actor.setdefault("model_id", None)
        actor.setdefault("reasoning_effort", None)
        actor.setdefault("provenance", "unknown")
        actor.setdefault("observed_status", "unknown")
        actor.setdefault("observed_at", None)
        actor.setdefault("staffing_status", "active")
        actor["assignments"] = []
    for value in _items(state.get("tasks"), "tasks"):
        task = _record(value, "task")
        for attempt_value in _items(task.get("attempts", []), "attempts"):
            attempt = _record(attempt_value, "attempt")
            owner = _text(attempt.get("owner"), "attempt owner")
            if owner not in actors:
                actors[owner] = {
                    "id": owner,
                    "role": "worker",
                    "parent_id": None,
                    "model_id": None,
                    "reasoning_effort": None,
                    "provenance": "unknown",
                    "observed_status": "unknown",
                    "observed_at": None,
                    "staffing_status": "active",
                    "history": [],
                    "assignments": [],
                }
            _items(actors[owner]["assignments"], "assignments").append(
                {
                    "task_id": task["id"],
                    "attempt": attempt.get("number"),
                    "status": attempt.get("status"),
                    "scope": deepcopy(attempt.get("scope", [])),
                    "metadata": deepcopy(
                        attempt.get(
                            "assignment",
                            {
                                "actor_id": owner,
                                "role": "worker",
                                "model_id": None,
                                "reasoning_effort": None,
                                "provenance": "unknown",
                                "observed_status": "unknown",
                                "observed_at": None,
                            },
                        )
                    ),
                    "current": task.get("owner") == owner
                    and attempt is _items(task["attempts"], "attempts")[-1],
                }
            )
    return {
        "categories": _categories(state),
        "actors": list(actors.values()),
        "handoffs": deepcopy(_items(state.get("handoffs", []), "handoffs")),
        "director": deepcopy(state.get("director")),
        "responsibilities": deepcopy(RESPONSIBILITIES),
        "objective": deepcopy(state.get("objective")),
    }


def add_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register local receipt commands; main retains the existing Program lock."""
    for name in sorted(COMMANDS):
        command = subparsers.add_parser(name, help="Record local governance from a JSON receipt")
        command.add_argument("--input", required=True, help="Program-relative JSON file")


def _load(program: ProgramAccess, name: str) -> tuple[Record, str]:
    # Core and extension are also runnable as standalone sibling CLI modules.
    if __package__:
        from tools.feature_program import ProgramError, read_json_frozen
    else:
        from feature_program import ProgramError, read_json_frozen

    path = program.path(_relative(name))
    _require(path.is_file(), "Missing governance receipt")
    try:
        return read_json_frozen(path, limit=1024 * 1024)
    except ProgramError as exc:
        raise GovernanceError(str(exc)) from exc


def _fresh(program: ProgramAccess, receipt: Record) -> list[str]:
    scope = _binding_fields(receipt)
    _require(receipt["source_head"] == program.current_head(), "Stale source HEAD")
    _require(receipt["source_digest"] == program.source_digest(scope), "Stale scoped source digest")
    return scope


def _freeze(
    program: ProgramAccess,
    receipt: Record,
    name: str,
    task_id: str = "_coordination",
    attempt: int = 1,
    *,
    parsed_hash: str,
) -> list[Json]:
    paths = [name, *_texts(receipt.get("evidence", []), "receipt evidence")]
    _require(len(paths) == len(set(paths)), "Duplicate evidence path")
    refs = program.evidence(task_id, attempt, paths)
    _require(
        _record(refs[0], "receipt reference").get("sha256") == parsed_hash,
        "Receipt changed while recording frozen metadata",
    )
    program.verify_refs(refs)
    return refs


def _actor_record(state: Record, receipt: Record) -> None:
    actors = _indexed(state.get("actors", []), "actor")
    identifier = _text(receipt.get("id"), "actor ID")
    previous = actors.get(identifier)
    decision = _choice(
        receipt.get("decision"), {"new", "reuse", "replace", "retire"}, "staffing decision"
    )
    _require(
        (previous is None) == (decision == "new"), "Staffing decision does not match actor history"
    )
    _text(receipt.get("reason"), "staffing decision reason")
    if previous is not None:
        _require(
            receipt.get("role") == previous.get("role"),
            "Actor role is historical; register a new actor identity for another role",
        )
    if decision == "replace":
        predecessor = _text(receipt.get("predecessor_id"), "replacement predecessor")
        _require(predecessor in actors, "Unknown replacement predecessor")
        _require(
            any(
                item.get("from_actor") == predecessor
                and item.get("to_actor") == identifier
                and item.get("acceptance") is not None
                for item in _indexed(state.get("handoffs", []), "handoff").values()
            ),
            "Replacement requires accepted succession; register a successor with new first",
        )
    if decision == "retire":
        director = state.get("director")
        _require(
            director is None or _record(director, "director")["actor_id"] != identifier,
            "Transfer final director authority before retiring",
        )
        _require(
            not any(
                _record(task, "task").get("owner") == identifier
                for task in _items(state["tasks"], "tasks")
            ),
            "Release owned tasks before retiring an actor",
        )
    actor = {
        key: deepcopy(receipt.get(key))
        for key in (
            "id",
            "role",
            "parent_id",
            "model_id",
            "reasoning_effort",
            "provenance",
            "observed_status",
            "observed_at",
        )
    }
    actor["staffing_status"] = "retired" if decision == "retire" else "active"
    _actor_fields(actor)
    history = deepcopy(_items(previous.get("history", []), "actor history")) if previous else []
    history.append(
        {
            "timestamp": _utc(),
            "decision": decision,
            "reason": receipt["reason"],
            "predecessor_id": receipt.get("predecessor_id"),
            "successor_id": receipt.get("successor_id"),
            "metadata": deepcopy(actor),
        }
    )
    actor["history"] = history
    actors[identifier] = actor
    state["actors"] = list(actors.values())


def _appoint(
    program: ProgramAccess, state: Record, receipt: Record, name: str, parsed_hash: str
) -> None:
    _require(state.get("director") is None, "Director already appointed; use acknowledged transfer")
    actors = _indexed(state.get("actors", []), "actor")
    actor_id = _text(receipt.get("actor_id"), "director actor")
    _require(
        actor_id in actors and actors[actor_id].get("role") == "director", "Unknown director actor"
    )
    _require(receipt.get("acknowledged") is True, "Director must explicitly acknowledge authority")
    _fresh(program, receipt)
    authority = _authority(receipt.get("authority"))
    refs = _freeze(program, receipt, name, parsed_hash=parsed_hash)
    state["director"] = {
        "actor_id": actor_id,
        "authority": deepcopy(authority),
        "source_head": receipt["source_head"],
        "source_digest": receipt["source_digest"],
        "scope": deepcopy(receipt["scope"]),
        "acknowledged_at": _utc(),
        "evidence": refs,
    }


def _transfer(
    program: ProgramAccess, state: Record, receipt: Record, name: str, parsed_hash: str
) -> None:
    handoffs = _indexed(state.get("handoffs", []), "handoff")
    identifier = _text(receipt.get("id"), "transfer ID")
    _require(identifier not in handoffs, "Transfer already recorded; history is immutable")
    scope = _fresh(program, receipt)
    role = receipt.get("role")
    source = _text(receipt.get("from_actor"), "predecessor")
    target = _text(receipt.get("to_actor"), "successor")
    _require(
        not any(
            item.get("from_actor") == source and item.get("acceptance") is None
            for item in handoffs.values()
        ),
        "Predecessor already has an outgoing transfer",
    )
    task_id: str | None = None
    attempt: int | None = None
    if role == "worker":
        task_id = _text(receipt.get("task_id"), "worker task ID")
        tasks = _indexed(state["tasks"], "task")
        _require(
            task_id in tasks and tasks[task_id].get("owner") == source,
            "Worker must own the transferred task",
        )
        current = _record(_items(tasks[task_id]["attempts"], "attempts")[-1], "attempt")
        attempt = current.get("number")
        _require(type(attempt) is int and attempt > 0, "Invalid attempt")
        _require(
            sorted(_texts(current.get("scope"), "attempt scope")) == scope,
            "Transfer scope must equal owned scope",
        )
    if role == "director":
        director = _record(state.get("director"), "current director")
        _require(director.get("actor_id") == source, "Only current director can transfer authority")
    refs = _freeze(
        program, receipt, name, task_id or "_coordination", attempt or 1, parsed_hash=parsed_hash
    )
    changed = [_relative(path) for path in _items(receipt.get("changed_paths"), "changed paths")]
    _require(
        all(any(path == root or path.startswith(root + "/") for root in scope) for path in changed),
        "Changed paths escape transfer scope",
    )
    handoffs[identifier] = {
        "id": identifier,
        "role": role,
        "from_actor": source,
        "to_actor": target,
        "task_id": task_id,
        "attempt": attempt,
        "outgoing": deepcopy(receipt),
        "outgoing_ref": refs[0],
        "evidence": refs,
        "acknowledgement": None,
        "ack_ref": None,
        "acknowledgements": [],
        "acceptance": None,
        "accept_ref": None,
        "created_at": _utc(),
    }
    state["handoffs"] = list(handoffs.values())


def _ack(
    program: ProgramAccess, state: Record, receipt: Record, name: str, parsed_hash: str
) -> None:
    handoffs = _indexed(state.get("handoffs", []), "handoff")
    identifier = _text(receipt.get("transfer_id"), "transfer ID")
    _require(identifier in handoffs, "Unknown transfer")
    handoff = handoffs[identifier]
    _require(handoff.get("acceptance") is None, "Accepted succession cannot be reacknowledged")
    previous_ack = handoff.get("acknowledgement")
    previous_ref = deepcopy(handoff.get("ack_ref"))
    previous_timestamp = handoff.get("acknowledged_at")
    if previous_ack is not None:
        previous = _record(previous_ack, "previous acknowledgement")
        _require(
            receipt.get("source_head") != previous.get("source_head")
            or receipt.get("source_digest") != previous.get("source_digest"),
            "Acknowledgement already binds current source",
        )
    _require(receipt.get("actor_id") == handoff["to_actor"], "Only successor can acknowledge")
    program.verify_refs(_items(handoff["evidence"], "transfer evidence"))
    _require(
        _fresh(program, receipt) == _binding_fields(_record(handoff["outgoing"], "outgoing")),
        "Acknowledgement scope mismatch",
    )
    _text(receipt.get("summary"), "fresh validation summary")
    task_id = handoff.get("task_id")
    attempt = handoff.get("attempt")
    if handoff["role"] == "worker":
        task = _indexed(state["tasks"], "task")[_text(task_id, "task ID")]
        attempts = _items(task["attempts"], "attempts")
        _require(type(attempt) is int and 0 < attempt <= len(attempts), "Invalid outgoing attempt")
        old = _record(attempts[attempt - 1], "outgoing attempt")
        _require(
            old.get("status") == "released" and task.get("owner") is None,
            "Release predecessor claim before acknowledgement; successor must revalidate and reclaim",
        )
    if handoff["role"] == "director":
        _require(
            _record(state.get("director"), "director").get("actor_id") == handoff["from_actor"],
            "Director authority changed before acknowledgement",
        )
    actors = _indexed(state.get("actors", []), "actor")
    _require(
        actors[_text(handoff["to_actor"], "successor")].get("staffing_status", "active")
        != "retired",
        "Retired actor cannot acknowledge succession",
    )
    refs = _freeze(
        program,
        receipt,
        name,
        _text(task_id, "task ID") if task_id is not None else "_coordination",
        attempt if type(attempt) is int else 1,
        parsed_hash=parsed_hash,
    )
    handoff["acknowledgement"] = deepcopy(receipt)
    handoff["ack_ref"] = refs[0]
    handoff["acknowledged_at"] = _utc()
    _items(handoff["evidence"], "handoff evidence").extend(refs)
    revisions = _items(handoff.setdefault("acknowledgements", []), "acknowledgement history")
    if previous_ack is not None and not revisions:
        # Preserve a receipt recorded before acknowledgement histories were available.
        revisions.append(
            {
                "receipt": deepcopy(previous_ack),
                "ref": previous_ref,
                "timestamp": previous_timestamp,
            }
        )
    revisions.append(
        {"receipt": deepcopy(receipt), "ref": refs[0], "timestamp": handoff["acknowledged_at"]}
    )
    # Acknowledgement records understanding only; the final director accepts succession.


def _accept_transfer(
    program: ProgramAccess, state: Record, receipt: Record, name: str, parsed_hash: str
) -> None:
    handoffs = _indexed(state.get("handoffs", []), "handoff")
    identifier = _text(receipt.get("transfer_id"), "transfer ID")
    _require(identifier in handoffs, "Unknown transfer")
    handoff = handoffs[identifier]
    _require(handoff.get("acceptance") is None, "Succession already accepted")
    acknowledged = _record(handoff.get("acknowledgement"), "successor acknowledgement")
    scope = _fresh(program, receipt)
    _require(
        scope == _binding_fields(acknowledged)
        and receipt["source_head"] == acknowledged["source_head"]
        and receipt["source_digest"] == acknowledged["source_digest"],
        "Source changed after acknowledgement; record a fresh successor acknowledgement",
    )
    director = _record(state.get("director"), "current final director")
    actor_id = _text(receipt.get("actor_id"), "accepting director")
    normal = actor_id == director.get("actor_id")
    recovery = handoff["role"] == "director" and actor_id == handoff["to_actor"]
    _require(normal or recovery, "Final director must accept succession")
    if not normal:
        _authority(receipt.get("recovery_authority"))
    _text(receipt.get("summary"), "succession acceptance summary")
    successor = _indexed(state.get("actors", []), "actor")[_text(handoff["to_actor"], "successor")]
    _require(
        successor.get("staffing_status", "active") != "retired", "Retired actor cannot succeed"
    )
    program.verify_refs(_items(handoff["evidence"], "handoff evidence"))
    task_id = handoff.get("task_id")
    attempt = handoff.get("attempt")
    if handoff["role"] == "worker":
        task = _indexed(state["tasks"], "task")[_text(task_id, "task ID")]
        _require(task.get("owner") is None, "Do not accept succession over an owned task")
    if handoff["role"] == "director":
        _require(director.get("actor_id") == handoff["from_actor"], "Current director changed")
    refs = _freeze(
        program,
        receipt,
        name,
        _text(task_id, "task ID") if task_id is not None else "_coordination",
        attempt if type(attempt) is int else 1,
        parsed_hash=parsed_hash,
    )
    handoff["acceptance"] = deepcopy(receipt)
    handoff["accept_ref"] = refs[0]
    handoff["accepted_at"] = _utc()
    _items(handoff["evidence"], "handoff evidence").extend(refs)
    if handoff["role"] == "director":
        outgoing = _record(handoff["outgoing"], "outgoing")
        previous = _record(state["director"], "director")
        history = deepcopy(_items(previous.get("history", []), "director history"))
        history.append(
            {key: deepcopy(value) for key, value in previous.items() if key != "history"}
        )
        state["director"] = {
            "actor_id": handoff["to_actor"],
            "authority": deepcopy(outgoing["authority"]),
            "source_head": receipt["source_head"],
            "source_digest": receipt["source_digest"],
            "scope": deepcopy(receipt["scope"]),
            "acknowledged_at": handoff["accepted_at"],
            "evidence": deepcopy(handoff["evidence"]),
            "history": history,
        }


def validate_claim_transfer(state: Record, task_id: str, owner: str) -> None:
    """Core claim calls this gate; handoffs never replace normal release/revalidation."""
    actors = _indexed(state.get("actors", []), "actor")
    if owner in actors:
        _require(
            actors[owner].get("staffing_status", "active") != "retired",
            "Retired actor cannot claim",
        )
    task = _indexed(state["tasks"], "task")[task_id]
    attempts = _items(task.get("attempts", []), "attempts")
    latest = len(attempts)
    for handoff in _indexed(state.get("handoffs", []), "handoff").values():
        if handoff.get("task_id") == task_id and handoff.get("attempt") == latest:
            _require(
                handoff.get("acceptance") is not None, "Accept successor transfer before reclaiming"
            )
            _require(owner == handoff.get("to_actor"), "Claim must follow accepted successor")


def assignment_metadata(state: Record, owner: str) -> Record:
    """Freeze current launch provenance on a new claim, never backfill older attempts."""
    actors = _indexed(state.get("actors", []), "actor")
    actor = actors.get(owner, {"id": owner, "role": "worker"})
    return {
        "actor_id": owner,
        "actor_revision": len(_items(actor.get("history", []), "history")),
        "role": actor.get("role", "worker"),
        "model_id": actor.get("model_id"),
        "reasoning_effort": actor.get("reasoning_effort"),
        "provenance": actor.get("provenance", "unknown"),
        "parent_id": actor.get("parent_id"),
        "observed_status": actor.get("observed_status", "unknown"),
        "observed_at": actor.get("observed_at"),
        "recorded_at": _utc(),
    }


def current_director_id(state: Record) -> str | None:
    """Return recorded final authority, not an authenticated identity or live actor."""
    director = state.get("director")
    return (
        None
        if director is None
        else _text(_record(director, "director").get("actor_id"), "director actor")
    )


def objective_contract(state: Record) -> Record | None:
    """Return declared objective identity and criteria, excluding runtime progress."""
    value = state.get("objective")
    if value is None:
        return None
    goal = _record(value, "objective")
    progress = {
        "status",
        "next_action_owner",
        "next_action",
        "history",
        "completion",
        "completed_at",
        "evidence",
        "integrated_review_ref",
        "membership_history",
    }
    return {key: deepcopy(value) for key, value in goal.items() if key not in progress}


def _validate_objective(value: Json, task_ids: set[str]) -> None:
    if value is None:
        return
    goal = _record(value, "objective")
    for historical in [*_items(goal.get("history", []), "objective history"), goal]:
        entry = _record(historical, "objective history entry")
        _text(entry.get("id"), "objective ID")
        _text(entry.get("title"), "objective title")
        criteria = _texts(entry.get("criteria"), "objective criteria")
        _require(
            bool(criteria) and len(criteria) == len(set(criteria)),
            "Objective needs unique acceptance criteria",
        )
        members = _texts(entry.get("task_ids"), "objective task IDs")
        _require(
            bool(members) and len(members) == len(set(members)) and set(members) <= task_ids,
            "Objective needs explicit unique known task membership",
        )
        _validate_membership(entry)
        status = _choice(
            entry.get("status"),
            {"active", "director_review", "awaiting_next_goal"},
            "objective status",
        )
        _require(entry.get("supplied_by") == "user", "Major objective must originate from the user")
        _require(
            entry.get("decision_owner") == "director", "Final director decides objective completion"
        )
        _require(
            entry.get("next_action_owner")
            == ("user" if status == "awaiting_next_goal" else "director"),
            "Objective next action owner mismatch",
        )
        if status == "awaiting_next_goal":
            _record(entry.get("completion"), "director completion receipt")
            _timestamp(entry.get("completed_at"), "objective completion timestamp")
            _require(
                bool(_items(entry.get("evidence"), "completion evidence")),
                "Objective completion lacks frozen evidence",
            )
            _require(
                entry.get("integrated_review_ref")
                in _items(entry["evidence"], "completion evidence"),
                "Objective completion lacks frozen integrated review",
            )


def _validate_membership(goal: Record) -> None:
    """Reconstruct amendment history instead of trusting redundant current member lists."""
    if "membership_revision" not in goal:
        _require("membership_history" not in goal, "Membership history lacks revision")
        return
    history = _items(goal.get("membership_history"), "membership history")
    revision = goal.get("membership_revision")
    _require(
        type(revision) is int and revision > 0 and len(history) == revision + 1,
        "Invalid membership revision history",
    )
    previous: list[str] | None = None
    for number, value in enumerate(history):
        entry = _record(value, "membership revision")
        _require(entry.get("revision") == number, "Nonmonotonic membership history")
        before = _texts(entry.get("before"), "before membership")
        after = _texts(entry.get("after"), "after membership")
        _require(
            bool(after) and len(after) == len(set(after)), "Invalid membership history members"
        )
        _require(previous is None or before == previous, "Membership history chain changed")
        _require(
            set(_texts(entry.get("added"), "added IDs")) == set(after) - set(before)
            and set(_texts(entry.get("removed"), "removed IDs")) == set(before) - set(after),
            "Membership history delta mismatch",
        )
        if number == 0:
            _require(before == after, "Membership baseline must preserve original members")
        else:
            _text(entry.get("amendment_id"), "membership amendment ID")
            _text(entry.get("actor_id"), "membership actor")
            _text(entry.get("reason"), "membership reason")
            _timestamp(entry.get("recorded_at"), "membership recording timestamp")
        previous = after
    _require(
        previous == _texts(goal["task_ids"], "current members"),
        "Current membership differs from revision history",
    )


def amend_membership(state: Record, receipt: Record, admitted: list[str]) -> None:
    """Amend the same active goal, preserving its human criteria and major history."""
    goal = _record(state.get("objective"), "objective")
    membership = _record(receipt.get("membership"), "membership amendment")
    before = _texts(membership.get("before"), "before members")
    after = _texts(membership.get("after"), "after members")
    added = _texts(membership.get("added"), "added members")
    removed = _texts(membership.get("removed"), "removed members")
    tasks = _indexed(state["tasks"], "task")
    _require(before == _texts(goal["task_ids"], "current members"), "Stale objective membership")
    _require(
        bool(after) and len(after) == len(set(after)) and set(after) <= set(tasks),
        "Membership needs exact unique known nonempty IDs",
    )
    _require(
        set(added) == set(after) - set(before) and set(removed) == set(before) - set(after),
        "Membership delta mismatch",
    )
    _require(
        bool(added or removed) and set(admitted) <= set(added),
        "Amendment must change membership and include every admitted task",
    )
    for task_id in removed:
        _require(
            tasks[task_id].get("owner") is None
            and tasks[task_id].get("status") not in {"active", "blocked", "review"},
            "Release owned work before removing membership",
        )
    for task_id in after:
        for dependency in _texts(tasks[task_id]["depends_on"], "dependencies"):
            _require(
                dependency in after or tasks.get(dependency, {}).get("status") == "done",
                "Current member depends on excluded unfinished prerequisite",
            )
    history = _items(
        goal.setdefault(
            "membership_history",
            [{"revision": 0, "before": before, "after": before, "added": [], "removed": []}],
        ),
        "membership history",
    )
    revision = goal.get("membership_revision", 0)
    _require(type(revision) is int and revision >= 0, "Invalid current membership revision")
    revision += 1
    history.append(
        {
            "revision": revision,
            "before": before,
            "after": after,
            "added": added,
            "removed": removed,
            "amendment_id": receipt["id"],
            "actor_id": receipt["actor_id"],
            "reason": receipt["reason"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    goal.update({"task_ids": after, "membership_revision": revision})


def _objective_record(state: Record, receipt: Record) -> None:
    _require(
        receipt.get("supplied_by") == "user", "New major objective requires an explicit human goal"
    )
    _text(receipt.get("human_authorization"), "human goal authorization")
    previous = state.get("objective")
    history: list[Json] = []
    if previous is not None:
        old = _record(previous, "previous objective")
        _require(
            old.get("status") == "awaiting_next_goal",
            "Finish current objective before beginning another",
        )
        _require(receipt.get("id") != old.get("id"), "New objective needs a new identity")
        history = deepcopy(_items(old.get("history", []), "objective history"))
        history.append({key: deepcopy(value) for key, value in old.items() if key != "history"})
        _require(
            receipt.get("id")
            not in {
                _text(_record(item, "historical objective")["id"], "objective ID")
                for item in history
            },
            "Objective identity already used",
        )
    state["objective"] = {
        "id": receipt.get("id"),
        "title": receipt.get("title"),
        "criteria": deepcopy(receipt.get("criteria")),
        "task_ids": deepcopy(receipt.get("task_ids")),
        "status": "active",
        "supplied_by": "user",
        "decision_owner": "director",
        "next_action_owner": "director",
        "history": history,
        "human_authorization": receipt["human_authorization"],
        "created_at": _utc(),
    }


def _objective_complete(
    program: ProgramAccess, state: Record, receipt: Record, name: str, parsed_hash: str
) -> None:
    goal = _record(state.get("objective"), "current objective")
    _require(
        goal.get("status") in {"active", "director_review"},
        "Objective already awaiting the user's next goal",
    )
    director = _record(state.get("director"), "current final director")
    _require(
        receipt.get("actor_id") == director.get("actor_id"),
        "Only current final director decides objective completion",
    )
    _require(receipt.get("objective_id") == goal.get("id"), "Objective identity mismatch")
    if "membership_revision" in goal:
        _require(
            receipt.get("membership_revision") == goal["membership_revision"],
            "Completion membership revision is stale",
        )
    _fresh(program, receipt)
    _text(receipt.get("summary"), "completion decision summary")
    _require(receipt.get("unresolved_findings") == [], "Objective has unresolved findings")
    results = [
        _record(item, "criterion result")
        for item in _items(receipt.get("criteria_results"), "criteria results")
    ]
    names = [_text(item.get("criterion"), "criterion") for item in results]
    _require(
        len(names) == len(set(names)) and set(names) == set(_texts(goal["criteria"], "criteria")),
        "Completion must cover exact objective criteria",
    )
    for result in results:
        _require(
            result.get("status") == "passed"
            and bool(_texts(result.get("evidence"), "criterion evidence")),
            "Objective criterion needs passing evidence",
        )
    tasks = _indexed(state["tasks"], "task")
    members = _texts(goal.get("task_ids"), "objective task membership")
    _require(
        not program.pending_questions(state, _text(goal["id"], "objective ID"), members),
        "Objective has unresolved director questions",
    )
    aggregate_scope = [path.casefold() for path in _texts(receipt["scope"], "objective scope")]
    for handoff in _indexed(state.get("handoffs", []), "handoff").values():
        if handoff.get("acceptance") is not None:
            continue
        outgoing = _record(handoff.get("outgoing"), "outgoing transfer")
        transfer_scope = [
            path.casefold() for path in _texts(outgoing.get("scope"), "transfer scope")
        ]
        overlaps = any(
            left == right or left.startswith(right + "/") or right.startswith(left + "/")
            for left in transfer_scope
            for right in aggregate_scope
        )
        _require(
            handoff.get("role") not in {"director", "sub_director"}
            and handoff.get("task_id") not in members
            and not overlaps,
            "Objective has an unresolved relevant handoff",
        )
    accepted = _indexed(
        [
            dict(
                _record(item, "task acceptance"), id=_record(item, "task acceptance").get("task_id")
            )
            for item in _items(receipt.get("task_acceptance"), "task acceptance")
        ],
        "accepted task",
    )
    _require(set(accepted) == set(members), "Completion must bind every objective task")
    implementers: set[str] = set()
    for task_id in members:
        task = tasks[task_id]
        _require(task.get("status") == "done", "Objective has unfinished required tasks")
        attempts = _items(task.get("attempts"), "attempts")
        _require(bool(attempts), "Reviewed task lacks attempt")
        implementers.update(
            _text(_record(value, "member attempt").get("owner"), "implementer")
            for value in attempts
        )
        attempt = _record(attempts[-1], "completed attempt")
        program.verify_terminal(task, attempt)
        delivery = program.current_delivery(task)
        _require(
            delivery.get("status") == "ci_recorded_success",
            "Objective has incomplete required delivery/CI",
        )
        binding = accepted[task_id]
        _require(
            binding.get("attempt") == attempt.get("number")
            and binding.get("source_digest")
            == _record(attempt.get("submission"), "accepted submission").get("source_digest")
            and binding.get("review_sha256")
            == _record(attempt.get("review"), "accepted review").get("sha256")
            and binding.get("delivery_sha256")
            == _record(delivery.get("receipt"), "delivery receipt").get("sha256"),
            "Objective task acceptance is stale or mismatched",
        )
        required_scope = _texts(attempt.get("scope"), "accepted scope")
        _require(
            all(
                any(
                    path == scope or path.startswith(scope + "/")
                    for scope in _texts(receipt["scope"], "objective scope")
                )
                for path in required_scope
            ),
            "Objective source binding does not cover accepted task scope",
        )
    _require(
        receipt.get("remote_state_checked") is True,
        "Director must record actual remote-state inspection; tool cannot authenticate it",
    )
    checks = [
        _record(item, "objective CI check") for item in _items(receipt.get("ci"), "objective CI")
    ]
    required = _texts(receipt.get("required_ci"), "required objective CI")
    observed = [_text(item.get("name"), "CI name") for item in checks]
    _require(
        bool(required) and len(observed) == len(set(observed)) and set(required) == set(observed),
        "Objective CI must cover exact required checks",
    )
    for check in checks:
        _text(check.get("url"), "actual CI URL")
        _require(
            check.get("conclusion") == "success" and check.get("commit") == receipt["source_head"],
            "Objective CI must pass on current source HEAD",
        )
    evidence = _texts(receipt.get("evidence", []), "completion evidence")
    _require(
        all(
            set(_texts(result["evidence"], "criterion evidence")) <= set(evidence)
            for result in results
        ),
        "Criterion evidence must be frozen by completion",
    )
    refs = _freeze(program, receipt, name, parsed_hash=parsed_hash)
    review_refs = _integrated_review(program, goal, receipt, implementers)
    goal.update(
        {
            "status": "awaiting_next_goal",
            "next_action_owner": "user",
            "completion": deepcopy(receipt),
            "completed_at": _utc(),
            "evidence": refs + review_refs,
            "integrated_review_ref": review_refs[0],
        }
    )


def _integrated_review(
    program: ProgramAccess, goal: Record, receipt: Record, implementers: set[str]
) -> list[Json]:
    """Freeze an independent review of the actual integrated bytes, not old task hashes."""
    name = _relative(receipt.get("integrated_review"))
    review, parsed_hash = _load(program, name)
    _require(review.get("objective_id") == goal["id"], "Integrated review objective mismatch")
    if "membership_revision" in goal:
        _require(
            review.get("membership_revision") == goal["membership_revision"],
            "Integrated review membership revision is stale",
        )
    _require(
        _fresh(program, review) == _binding_fields(receipt)
        and review["source_head"] == receipt["source_head"]
        and review["source_digest"] == receipt["source_digest"],
        "Integrated review scope/source mismatch",
    )
    reviewer = _text(review.get("reviewer"), "integrated reviewer identity")
    _require(
        review.get("reviewer_role") == "independent"
        and reviewer != receipt.get("actor_id")
        and reviewer not in implementers,
        "Integrated reviewer must be independent of final director and implementers",
    )
    _require(
        review.get("verdict") == "accepted"
        and review.get("acceptance_complete") is True
        and review.get("unresolved_findings") == [],
        "Integrated review has unresolved acceptance",
    )
    criteria = _texts(review.get("criteria"), "integrated review criteria")
    _require(
        len(criteria) == len(set(criteria))
        and set(criteria) == set(_texts(goal["criteria"], "goal criteria")),
        "Integrated review must assess complete goal criteria",
    )
    return _freeze(program, review, name, parsed_hash=parsed_hash)


def apply_command(program: ProgramAccess, state: Record, args: argparse.Namespace) -> bool:
    """Stage metadata atomically in memory; core validates/saves under its own lock."""
    if args.command not in COMMANDS:
        return False
    receipt, parsed_hash = _load(program, args.input)
    proposal = deepcopy(state)
    match args.command:
        case "category-set":
            proposal["categories"] = deepcopy(_items(receipt.get("categories"), "categories"))
            _text(receipt.get("reason"), "category decision reason")
        case "actor-record":
            _actor_record(proposal, receipt)
        case "director-appoint":
            _appoint(program, proposal, receipt, args.input, parsed_hash)
        case "transfer":
            _transfer(program, proposal, receipt, args.input, parsed_hash)
        case "transfer-ack":
            _ack(program, proposal, receipt, args.input, parsed_hash)
        case "transfer-accept":
            _accept_transfer(program, proposal, receipt, args.input, parsed_hash)
        case "objective-record":
            _objective_record(proposal, receipt)
        case "objective-complete":
            _objective_complete(program, proposal, receipt, args.input, parsed_hash)
        case _:
            raise GovernanceError("Unsupported coordination command")
    validate_governance(proposal)
    program.event(
        proposal,
        args.command,
        {
            "actor": receipt.get("actor_id", receipt.get("from_actor", receipt.get("id"))),
            "id": receipt.get("task_id"),
            "reason": receipt.get(
                "reason",
                receipt.get("summary", receipt.get("next_action", "Recorded local governance")),
            ),
            "previous_state": None,
            "new_state": "recorded",
            "evidence": [],
            "receipt": deepcopy(receipt),
        },
    )
    state.clear()
    state.update(proposal)
    return True
