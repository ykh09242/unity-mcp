"""Pure submission review packets and retained native handoff observations.

The host owns filesystem/source checks, materialization and canonical publication.
No function here samples clocks or executes native, provider or stored commands.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Final, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
MAX_RECORDS: Final = 2000
LIMIT: Final = 8 * 1024 * 1024
KINDS: Final = frozenset(
    {
        "submitted",
        "dispatch_claimed",
        "dispatch_sent",
        "dispatch_failed",
        "dispatch_unknown",
        "reviewer_acknowledged",
        "review_started",
        "terminal",
    }
)
ACTUAL_SOURCES: Final = frozenset(
    {
        "live_postcondition_observation",
        "native_tool_observation",
        "reviewer_observation",
        "recovery_postcondition_observation",
    }
)


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _require(condition: bool, message: str) -> None:
    _core().require(condition, message)


def _record(value: Json, label: str = "handoff record") -> Record:
    return _core().record(value, label)


def _rows(value: Json) -> list[Record]:
    return [_record(item) for item in _core().sequence(value, "handoff rows")]


def _text(value: Json, label: str) -> str:
    return _core().string(value, label)


def _hash(value: Json, label: str) -> str:
    result = _text(value, label)
    _require(
        len(result) == 64 and all(char in "0123456789abcdef" for char in result), f"Invalid {label}"
    )
    return result


def _ref(value: Json) -> Record:
    ref = _record(value)
    _require(set(ref) == {"path", "sha256"}, "Exact handoff reference required")
    name = _core().safe_relative(_text(ref["path"], "evidence path"))
    _require(name.startswith("evidence/"), "Handoff reference requires private evidence")
    _hash(ref["sha256"], "evidence hash")
    return deepcopy(ref)


def _refs(value: Json) -> list[Json]:
    refs = [_ref(item) for item in _core().sequence(value, "handoff evidence")]
    _require(len(refs) <= MAX_RECORDS, "Too many handoff evidence references")
    _require(
        len({str(ref["path"]) for ref in refs}) == len(refs), "Duplicate handoff evidence paths"
    )
    return sorted(refs, key=lambda ref: str(ref["path"]))


def _task(state: Record, identifier: str) -> Record:
    task = next((item for item in _rows(state["tasks"]) if item["id"] == identifier), None)
    _require(task is not None, "Unknown handoff task")
    return task


def _actor(state: Record, identifier: Json) -> Record:
    return next(
        (actor for actor in _rows(state.get("actors", [])) if actor["id"] == identifier), {}
    )


def _director(state: Record) -> str:
    return _core().coordination.current_director_id(state) or "leader"


def _subject(
    state: Record, task_id: str, context: Record, review_inputs: Json, *, task: Record | None = None
) -> Record:
    core = _core()
    task = _task(state, task_id) if task is None else task
    attempt = core.Program.current_attempt(task)
    submission = _record(attempt.get("submission"), "actual submitted review")
    objective = core.coverage.objective_binding(state) if state.get("objective") else None
    definition = core.digest(core.planning.task_definition(task))
    _require(
        context.get("program_id") == state["program_id"]
        and context.get("task_id") == task_id
        and context.get("attempt") == attempt["number"],
        "Handoff task/attempt context changed",
    )
    _require(
        context.get("definition_digest") == definition
        and context.get("objective_binding") == objective,
        "Handoff definition/objective changed",
    )
    expected = {
        "round": submission["round"],
        "receipt": submission["receipt"],
        "source_digest": submission["source_digest"],
    }
    _require(context.get("submission") == expected, "Handoff submitted receipt/round changed")
    _require(
        context.get("source_digest") == submission["source_digest"],
        "Handoff submitted source changed",
    )
    _require(
        context.get("required_checks") == task["required_checks"], "Handoff obligations changed"
    )
    _require(task.get("disposition") not in {"split", "retired"}, "Disposed review task")
    if objective:
        _require(task_id in objective["member_ids"], "Review excluded from current objective")
    return {
        "program_id": state["program_id"],
        "task_id": task_id,
        "attempt": attempt["number"],
        "submission_round": submission["round"],
        "receipt_sha256": _ref(submission["receipt"])["sha256"],
        "source_digest": _hash(submission["source_digest"], "source digest"),
        "definition_digest": definition,
        "objective_binding": objective,
        "required_obligation_digest": core.digest(
            {
                "checks": task["required_checks"],
                "original": task["source"],
                "objective": objective,
            }
        ),
        "review_input_digest": core.digest(review_inputs),
    }


def _records(state: Record) -> list[Record]:
    extension = state.get("review_handoffs")
    if extension is None:
        return []
    value = _record(extension)
    _require(
        set(value) == {"schema_version", "records"}
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1,
        "Invalid review handoff extension",
    )
    records = _rows(value["records"])
    _require(len(records) <= MAX_RECORDS, "Review handoff history exceeds record limit")
    return records


def _payload(record: Record) -> Record:
    return {
        key: value
        for key, value in record.items()
        if key not in {"digest", "previous_digest", "recorded_at"}
    }


def _milestone(details: Record, kind: str) -> None:
    _require(
        details.get("recording_mode", "current") in {"current", "historical"},
        "Invalid retained recording mode",
    )
    _require(
        details.get("recording_mode") != "historical"
        or kind not in {"submitted", "dispatch_claimed"},
        "Historical activity cannot authorize a new dispatch",
    )
    observed = details.get("observed_at")
    if observed is not None:
        try:
            parsed = datetime.fromisoformat(_text(observed, "observed time").replace("Z", "+00:00"))
        except ValueError as exc:
            raise _core().ProgramError("Invalid handoff observed time") from exc
        _require(parsed.utcoffset() is not None, "Actual observation needs aware timestamp")
        _text(details.get("clock_id"), "actual clock")
        _require(
            details.get("timestamp_source") in ACTUAL_SOURCES,
            "Preparation/recorded clocks are not actual observations",
        )
        _text(details.get("boundary"), "actual boundary")
    if kind != "submitted":
        _require(bool(details.get("evidence_refs")), "Actual native observation requires evidence")
    if kind in {"dispatch_claimed", "dispatch_sent", "dispatch_failed", "dispatch_unknown"}:
        _text(details.get("dispatch_attempt_id"), "dispatch attempt")
        _text(details.get("contact"), "actual native contact")
        status = details.get("recipient_status")
        _require(status in {"idle", "completed", "running"}, "Actual recipient status required")
        expected = "send_message" if status == "running" else "followup_task"
        _require(
            details.get("transport") == expected, "Native transport cannot wake this recipient"
        )
    if kind in {"reviewer_acknowledged", "review_started"}:
        _text(details.get("dispatch_attempt_id"), "ack/start dispatch attempt")
    if kind == "terminal":
        terminal_kind = details.get("terminal_kind")
        _require(
            terminal_kind in {"finished", "canceled", "canonical_verdict_observed"},
            "Explicit actual terminal kind required",
        )
        if terminal_kind == "canonical_verdict_observed":
            _ref(details.get("terminal_ref"))
        else:
            _text(details.get("dispatch_attempt_id"), "actual terminal dispatch attempt")
        _require(
            details.get("outcome")
            in {None, "accepted", "rejected", "failed", "canceled", "unknown"},
            "Invalid actual terminal outcome",
        )
    attached = details.get("submitted_observation")
    if attached is not None:
        value = _record(attached)
        _require(value.get("kind") == "submitted", "Invalid retained submitted milestone")
        _ref(value.get("reference"))
        _milestone(value, "submitted")


@dataclass(frozen=True, slots=True)
class _Chronology:
    """Accumulate only validated prior rows for one exact assignment."""

    claims: dict[str, int] = field(default_factory=dict)
    dispatches: dict[str, Record] = field(default_factory=dict)
    reconciled: set[str] = field(default_factory=set)
    unknown: dict[str, int] = field(default_factory=dict)
    sent: dict[str, int] = field(default_factory=dict)
    terminal: set[str] = field(default_factory=set)
    milestones: set[tuple[str, str]] = field(default_factory=set)

    def observe(self, item: Record, position: int) -> None:
        """Advance after validation, never pre-index future authorizing observations."""
        details = _record(item["details"])
        match details.get("reconciles"):
            case str() as identifier:
                self.reconciled.add(identifier)
                self.unknown.pop(identifier, None)
        dispatch_id = details.get("dispatch_attempt_id")
        match dispatch_id:
            case str():
                kind = str(item["kind"])
                self.milestones.add((kind, dispatch_id))
                if kind == "dispatch_claimed":
                    self.claims[dispatch_id] = position
                if kind in {"dispatch_sent", "dispatch_failed", "dispatch_unknown"}:
                    self.dispatches[dispatch_id] = item
                    self.claims.pop(dispatch_id, None)
                if kind == "dispatch_unknown" and item["id"] not in self.reconciled:
                    self.unknown[str(item["id"])] = position
                if kind == "dispatch_sent" and dispatch_id not in self.terminal:
                    self.sent[dispatch_id] = position
                if kind == "terminal" and details.get("terminal_kind") in {"finished", "canceled"}:
                    self.terminal.add(dispatch_id)
                    self.sent.pop(dispatch_id, None)

    def require_clear(self, own_claim: str | None = None) -> None:
        """Report the same earliest retained unresolved delivery/process boundary."""
        blockers = [(position, "unknown") for position in self.unknown.values()]
        blockers.extend(
            (position, "claim") for key, position in self.claims.items() if key != own_claim
        )
        blockers.extend((position, "sent") for position in self.sent.values())
        match min(blockers, default=None):
            case (_, "claim"):
                _require(False, "Retained dispatch claim must be reconciled")
            case (_, "unknown"):
                _require(False, "Retained unknown delivery was replayed")
            case (_, "sent"):
                _require(False, "Retained sent process was replayed")


@dataclass(frozen=True, slots=True)
class _History:
    """One complete validated image and its chronological assignment groups."""

    records: list[Record]
    tasks: dict[str, Record]
    groups: dict[tuple[str, int], list[Record]]
    latest_generation: dict[str, int]


def _validate_retained_observation(
    item: Record, intent: Record, prior: _Chronology, review: Json
) -> None:
    """Check event relationships from retained facts, without today's actor availability."""
    kind = item["kind"]
    details = _record(item["details"])
    actor, director, reviewer = item["actor_id"], item["director_id"], intent["reviewer_id"]
    reporter = item.get("reporter_id")
    dispatch_id = details.get("dispatch_attempt_id")
    dispatch = prior.dispatches.get(str(dispatch_id))
    if kind in {
        "dispatch_claimed",
        "dispatch_sent",
        "dispatch_failed",
        "dispatch_unknown",
        "submitted",
    }:
        _require(actor == director, "Retained dispatch/submission lacks director attribution")
    else:
        _require(
            actor == reviewer or actor == director, "Retained activity lacks reviewer attribution"
        )
        if actor == director and kind != "terminal":
            _require(reporter == reviewer, "Retained activity lacks actual reviewer reporter")
    if kind == "dispatch_claimed":
        _require(
            details.get("action_id") == "ha-" + _core().digest(_review_action_identity(intent)),
            "Retained claim action correlation changed",
        )
        _require(
            _record(intent["eligibility"])["status"] == "eligible",
            "Claim lacks eligible assignment",
        )
        _require(
            str(dispatch_id) not in prior.dispatches, "Dispatch claim reuses an actual attempt"
        )
        prior.require_clear()
    if kind in {"dispatch_sent", "dispatch_failed", "dispatch_unknown"}:
        _require(
            _record(intent["eligibility"])["status"] == "eligible",
            "Retained native dispatch lacks eligible assignment",
        )
        if dispatch is not None:
            _require(
                dispatch["kind"] == "dispatch_unknown"
                and details.get("reconciles") == dispatch["id"]
                and kind in {"dispatch_sent", "dispatch_failed"},
                "Retained dispatch attempt lacks exact reconciliation",
            )
        else:
            prior.require_clear(str(dispatch_id))
    process_terminal = (
        kind == "terminal" and details["terminal_kind"] != "canonical_verdict_observed"
    )
    if kind in {"reviewer_acknowledged", "review_started"} or process_terminal:
        _require(
            dispatch is not None and dispatch["kind"] == "dispatch_sent",
            "Retained activity lacks exact sent attempt",
        )
        _require(
            dispatch_id not in prior.terminal,
            "Retained actual process already terminal",
        )
        _require(
            (str(kind), str(dispatch_id)) not in prior.milestones,
            "Retained actual milestone duplicates its process",
        )
    if kind == "terminal":
        if details["terminal_kind"] == "canonical_verdict_observed":
            _require(
                actor == director and details["terminal_ref"] == review,
                "Retained verdict observation differs from ordinary review",
            )
        else:
            _require(
                actor == reviewer or reporter == reviewer,
                "Retained terminal lacks actual reviewer attribution",
            )
    attached = details.get("submitted_observation")
    if attached is not None:
        _require(
            _record(attached).get("subject") == intent["subject"],
            "Retained submitted boundary has different subject",
        )


def _validated_history(state: Record) -> _History:
    """Inspect every row chronologically once before exposing any grouped facts."""
    seen: set[str] = set()
    intents: dict[tuple[str, int], Record] = {}
    previous = None
    records = _records(state)
    tasks = {str(task["id"]): task for task in reversed(_rows(state["tasks"]))}
    groups: dict[tuple[str, int], list[Record]] = {}
    chronology: dict[tuple[str, int], _Chronology] = {}
    latest_generation: dict[str, int] = {}
    for position, item in enumerate(records):
        identifier = _text(item.get("id"), "handoff record ID")
        _text(item.get("kind"), "handoff record kind")
        _require(identifier.casefold() not in seen, "Duplicate handoff record ID")
        seen.add(identifier.casefold())
        _require(item.get("previous_digest") == previous, "Handoff history chain changed")
        expected = _core().digest({key: value for key, value in item.items() if key != "digest"})
        _require(item.get("digest") == expected, "Handoff record digest changed")
        previous = expected
        common = {
            "id",
            "kind",
            "handoff_id",
            "assignment_generation",
            "subject",
            "packet_ref",
            "reviewer_id",
            "director_id",
            "eligibility",
            "evidence_refs",
            "receipt_ref",
            "recorded_at",
            "actor_id",
            "details",
            "previous_digest",
            "digest",
        }
        _require(
            set(item) == common | ({"reporter_id"} if item.get("kind") != "intent" else set()),
            "Invalid retained handoff record fields",
        )
        for field in ("recorded_at", "actor_id", "director_id"):
            _text(item.get(field), "handoff " + field)
        subject = _record(item.get("subject"))
        _require(
            set(subject)
            == {
                "program_id",
                "task_id",
                "attempt",
                "submission_round",
                "receipt_sha256",
                "source_digest",
                "definition_digest",
                "objective_binding",
                "required_obligation_digest",
                "review_input_digest",
            },
            "Invalid handoff subject fields",
        )
        for field in (
            "receipt_sha256",
            "source_digest",
            "definition_digest",
            "required_obligation_digest",
            "review_input_digest",
        ):
            _hash(subject[field], "handoff " + field)
        _require(
            item.get("handoff_id") == "rh-" + _core().digest(subject)[:40],
            "Handoff identity differs from exact subject",
        )
        eligibility = _record(item.get("eligibility"))
        _require(
            set(eligibility) == {"status", "reasons"}
            and eligibility["status"] in {"eligible", "not_assessed"},
            "Invalid retained eligibility",
        )
        _core().strings(eligibility["reasons"], "eligibility reasons")
        _require(
            (
                eligibility["status"] == "eligible"
                and item.get("reviewer_id") is not None
                and eligibility["reasons"] == []
            )
            or (eligibility["status"] == "not_assessed" and bool(eligibility["reasons"])),
            "Retained eligibility contradicts missing prerequisites",
        )
        if item.get("reviewer_id") is not None:
            _text(item["reviewer_id"], "designated reviewer")
        _require(subject.get("program_id") == state["program_id"], "Handoff program mismatch")
        task_id = _text(subject.get("task_id"), "handoff task")
        _require(task_id in tasks, "Unknown handoff task")
        task = tasks[task_id]
        attempts = _rows(task["attempts"])
        number = subject.get("attempt")
        _require(type(number) is int and 0 < number <= len(attempts), "Invalid handoff attempt")
        rounds = _rows(attempts[number - 1]["rounds"])
        number = subject.get("submission_round")
        _require(type(number) is int and 0 < number <= len(rounds), "Invalid handoff round")
        original = rounds[number - 1]
        _require(
            _ref(original["receipt"])["sha256"] == subject["receipt_sha256"]
            and original["source_digest"] == subject["source_digest"],
            "Historical submitted subject changed",
        )
        packet_ref = _ref(item["packet_ref"])
        expected_path = (
            f"evidence/{subject['task_id']}/attempt-{subject['attempt']}/review/"
            f"round-{subject['submission_round']}/{item['handoff_id']}.json"
        )
        _require(
            packet_ref["path"] == expected_path, "Packet is outside exact submitted task/attempt"
        )
        _refs(item["evidence_refs"])
        if item.get("receipt_ref") is not None:
            _ref(item["receipt_ref"])
        generation = item.get("assignment_generation")
        _require(type(generation) is int and generation > 0, "Invalid reviewer generation")
        key = (_text(item.get("handoff_id"), "handoff ID"), generation)
        match item.get("kind"):
            case "intent":
                _require(
                    identifier == key[0] + f"-assignment-{generation}" and item["details"] == {},
                    "Invalid handoff intent identity/details",
                )
                _require(key not in intents, "Duplicate reviewer assignment generation")
                _require(
                    generation == latest_generation.get(key[0], 0) + 1,
                    "Reviewer generation skipped",
                )
                latest_generation[key[0]] = generation
                intents[key] = item
                groups[key] = []
                chronology[key] = _Chronology()
            case kind if kind in KINDS:
                _require(key in intents, "Observation lacks exact handoff intent")
                intent = intents[key]
                _require(
                    subject == intent["subject"]
                    and item["packet_ref"] == intent["packet_ref"]
                    and item["reviewer_id"] == intent["reviewer_id"],
                    "Observation subject/packet/reviewer differs",
                )
                details = _record(item["details"])
                _require(
                    details.get("evidence_refs") == item["evidence_refs"],
                    "Observation evidence differs from retained record",
                )
                _milestone(details, str(kind))
                _validate_retained_observation(
                    item, intent, chronology[key], original.get("review")
                )
                chronology[key].observe(item, position)
                groups[key].append(item)
            case invalid:
                raise _core().ProgramError(f"Unknown handoff kind: {invalid}")
    _require(
        len(_core().canonical(state.get("review_handoffs", {}))) <= LIMIT,
        "Handoff extension exceeds document budget",
    )

    return _History(records, tasks, groups, latest_generation)


def validate(state: Record) -> None:
    """Inspect all retained history before any presentation reduction."""
    _validated_history(state)


def validate_append(original: Record, future: Record) -> None:
    """Preserve the exact retained prefix during ordinary publication preparation."""
    validate(original)
    validate(future)
    before = _records(original)
    _require(_records(future)[: len(before)] == before, "Retained handoff history was rewritten")


def evidence_references(state: Record) -> list[Json]:
    """List exact packet/control/native references for host filesystem freshness guards."""
    refs: dict[str, Record] = {}
    for item in _records(state):
        values = [item["packet_ref"], *item["evidence_refs"]]
        if item.get("receipt_ref"):
            values.append(item["receipt_ref"])
        details = _record(item.get("details") or {})
        if details.get("terminal_ref") is not None:
            values.append(details["terminal_ref"])
        attached = details.get("submitted_observation")
        if attached:
            values.append(_record(attached)["reference"])
        for value in values:
            ref = _ref(value)
            name = str(ref["path"])
            _require(name not in refs or refs[name] == ref, "Conflicting frozen handoff reference")
            refs[name] = ref
    return [refs[name] for name in sorted(refs)]


def _append(state: Record, item: Record) -> Record:
    proposed = deepcopy(state)
    existing = _records(state)
    _require(len(existing) < MAX_RECORDS, "Review handoff history exceeds record limit")
    item = deepcopy(item)
    item["previous_digest"] = existing[-1]["digest"] if existing else None
    item["digest"] = _core().digest(item)
    extension = proposed.setdefault("review_handoffs", {"schema_version": 1, "records": []})
    _record(extension)["records"].append(item)
    validate_append(state, proposed)
    return proposed


def _eligibility(
    state: Record,
    task: Record,
    reviewer: Json,
    declaration: Json,
    *,
    actors: dict[str, Record] | None = None,
    director: str | None = None,
) -> Record:
    reasons = []
    if reviewer is None:
        reasons.append("reviewer_unassigned")
    else:
        actor = _actor(state, reviewer) if actors is None else actors.get(str(reviewer), {})
        if not actor:
            reasons.append("reviewer_unregistered")
        if actor.get("staffing_status") == "retired":
            reasons.append("reviewer_retired")
        if reviewer in {attempt["owner"] for attempt in _rows(task["attempts"])}:
            reasons.append("reviewer_implemented_task")
        if reviewer == (director if director is not None else _director(state)):
            reasons.append("reviewer_is_director")
    if _record(declaration or {}).get("status") != "eligible":
        reasons.append("reviewer_eligibility_unassessed")
    return {"status": "eligible" if not reasons else "not_assessed", "reasons": reasons}


def prepare_handoff(state: Record, assessed_inputs: Record) -> Record:
    """Return detached submission intent and exact immutable packet, without IO."""
    core = _core()
    validate(state)
    task_id = _text(assessed_inputs.get("task_id"), "handoff task")
    task = _task(state, task_id)
    _require(task["status"] == "review", "Handoff requires actual submitted review")
    actor = _text(assessed_inputs.get("actor_id"), "acting actor")
    core.coordination.require_task_authority(state, actor, task.get("owner"))
    context = _record(assessed_inputs.get("context"))
    review_inputs = deepcopy(
        _record(assessed_inputs.get("review_inputs", {}), "declared review inputs")
    )
    subject = _subject(state, task_id, context, review_inputs)
    identifier = "rh-" + core.digest(subject)[:40]
    reviewer = assessed_inputs.get("reviewer_id")
    if reviewer is not None:
        reviewer = _text(reviewer, "chosen reviewer")
    eligibility = _eligibility(state, task, reviewer, assessed_inputs.get("eligibility"))
    attempt = core.Program.current_attempt(task)
    submission = _record(attempt["submission"])
    packet = {
        "schema_version": 1,
        "kind": "immutable_review_packet",
        "subject": subject,
        "original": deepcopy(task["source"]),
        "owned_write_scope": deepcopy(attempt["scope"]),
        "required_checks": deepcopy(task["required_checks"]),
        "review_inputs": review_inputs,
        "submitted_receipt": deepcopy(submission["receipt"]),
        "submitted_evidence": sorted(
            deepcopy(submission["evidence"]), key=lambda ref: (str(ref["path"]), str(ref["sha256"]))
        ),
        "acceptance_complete": False,
    }
    packet_ref = {
        "path": f"evidence/{task_id}/attempt-{subject['attempt']}/review/"
        f"round-{subject['submission_round']}/{identifier}.json",
        "sha256": core.sha(core.canonical(packet)),
    }
    previous = [
        item
        for item in _records(state)
        if item["kind"] == "intent" and item["handoff_id"] == identifier
    ]
    missing = list(eligibility["reasons"])
    if previous:
        latest = previous[-1]
        _require(
            latest["subject"] == subject and latest["packet_ref"] == packet_ref,
            "Immutable handoff packet changed",
        )
        if latest["reviewer_id"] == reviewer and latest["eligibility"] == eligibility:
            return {
                "status": "already_recorded",
                "state": deepcopy(state),
                "delta": None,
                "handoff_id": identifier,
                "subject": subject,
                "packet_document": packet,
                "packet_ref": packet_ref,
                "missing_fields": missing,
                "acceptance_complete": False,
            }
        core.coordination.require_task_authority(state, actor)
    generation = len(previous) + 1
    item = {
        "id": identifier + f"-assignment-{generation}",
        "kind": "intent",
        "handoff_id": identifier,
        "assignment_generation": generation,
        "subject": subject,
        "packet_ref": packet_ref,
        "reviewer_id": reviewer,
        "director_id": _director(state),
        "eligibility": eligibility,
        "evidence_refs": _refs(assessed_inputs.get("evidence_refs", [])),
        "receipt_ref": _ref(assessed_inputs["receipt_ref"])
        if assessed_inputs.get("receipt_ref")
        else None,
        "recorded_at": _text(assessed_inputs.get("recorded_at"), "intent recorded time"),
        "actor_id": actor,
        "details": {},
    }
    proposed = _append(state, item)
    link = {"handoff_id": identifier, "packet_ref": packet_ref}
    proposed_attempt = core.Program.current_attempt(_task(proposed, task_id))
    _record(proposed_attempt["submission"]).update(link)
    _rows(proposed_attempt["rounds"])[int(subject["submission_round"]) - 1].update(link)
    return {
        "status": "prepared",
        "state": proposed,
        "delta": {"record": deepcopy(_records(proposed)[-1]), "submission_link": link},
        "handoff_id": identifier,
        "subject": subject,
        "packet_document": packet,
        "packet_ref": packet_ref,
        "missing_fields": missing,
        "acceptance_complete": False,
    }


def prepare_observation(state: Record, actual_input: Record) -> Record:
    """Append actual attributed outcome; never invoke/replay the recorded native action."""
    core = _core()
    validate(state)
    _require(
        actual_input.get("schema_version") == 1
        and type(actual_input.get("schema_version")) is int
        and actual_input.get("operation") == "review-handoff-record",
        "Invalid handoff observation operation/schema",
    )
    allowed = {
        "schema_version",
        "operation",
        "id",
        "kind",
        "task_id",
        "handoff_id",
        "assignment_generation",
        "subject",
        "packet_ref",
        "actor_id",
        "reporter_id",
        "context",
        "expected_revision",
        "recorded_at",
        "receipt_ref",
        "review_inputs",
        "evidence_refs",
        "reason",
        "next_trigger",
        "dispatch_attempt_id",
        "transport",
        "recipient_status",
        "contact",
        "observed_at",
        "clock_id",
        "timestamp_source",
        "boundary",
        "reconciles",
        "submitted_observation",
        "terminal_kind",
        "terminal_ref",
        "outcome",
        "recording_mode",
        "action_id",
    }
    _require(set(actual_input) <= allowed, "Unknown actual handoff observation fields")
    identifier = _text(actual_input.get("id"), "observation ID")
    handoff_id = _text(actual_input.get("handoff_id"), "handoff ID")
    generation = actual_input.get("assignment_generation")
    kind = _text(actual_input.get("kind"), "actual handoff kind")
    mode = actual_input.get("recording_mode", "current")
    _require(mode in {"current", "historical"}, "Invalid handoff recording mode")
    historical = mode == "historical"
    _require(
        not historical or kind not in {"submitted", "dispatch_claimed"},
        "Historical activity cannot authorize a new dispatch",
    )
    _require(kind in KINDS, "Unknown actual handoff observation")
    intents = [
        item
        for item in _records(state)
        if item["kind"] == "intent" and item["handoff_id"] == handoff_id
    ]
    _require(bool(intents), "Unknown handoff")
    intent = next((item for item in intents if item["assignment_generation"] == generation), None)
    _require(intent is not None, "Unknown reviewer generation")
    _require(
        actual_input.get("subject") == intent["subject"]
        and actual_input.get("packet_ref") == intent["packet_ref"],
        "Exact handoff packet/subject required",
    )
    actor = _text(actual_input.get("actor_id"), "acting actor")
    _require(_actor(state, actor).get("staffing_status") != "retired", "Retired participant")
    director = _director(state)
    reporter = actual_input.get("reporter_id")
    match kind:
        case (
            "dispatch_claimed"
            | "dispatch_sent"
            | "dispatch_failed"
            | "dispatch_unknown"
            | "submitted"
        ):
            _require(actor == director, "Only current director records native dispatch/submission")
        case "reviewer_acknowledged" | "review_started" | "terminal":
            _require(
                actor == intent["reviewer_id"] or actor == director,
                "Only designated reviewer/current director records activity",
            )
            if actor == director:
                _require(
                    reporter == intent["reviewer_id"] or kind == "terminal",
                    "Director must retain actual reviewer reporter",
                )
        case invalid:
            raise core.ProgramError(f"Unknown observation kind: {invalid}")
    details = {
        key: deepcopy(value)
        for key, value in actual_input.items()
        if key
        not in {
            "schema_version",
            "operation",
            "id",
            "handoff_id",
            "assignment_generation",
            "subject",
            "packet_ref",
            "actor_id",
            "reporter_id",
            "context",
            "expected_revision",
            "recorded_at",
            "receipt_ref",
            "review_inputs",
            "kind",
            "task_id",
        }
    }
    details["evidence_refs"] = _refs(actual_input.get("evidence_refs", []))
    _text(details.get("reason"), "actual observation reason")
    _milestone(details, kind)
    attached = details.get("submitted_observation")
    if attached is not None:
        _require(
            _record(attached).get("subject") == intent["subject"],
            "Retained submitted observation subject differs",
        )
    item = {
        "id": identifier,
        "kind": kind,
        "handoff_id": handoff_id,
        "assignment_generation": generation,
        "subject": deepcopy(intent["subject"]),
        "packet_ref": deepcopy(intent["packet_ref"]),
        "reviewer_id": intent["reviewer_id"],
        "director_id": director,
        "eligibility": deepcopy(intent["eligibility"]),
        "evidence_refs": details["evidence_refs"],
        "receipt_ref": _ref(actual_input["receipt_ref"])
        if actual_input.get("receipt_ref")
        else None,
        "actor_id": actor,
        "reporter_id": reporter,
        "details": details,
        "recorded_at": _text(actual_input.get("recorded_at"), "observation recorded time"),
    }
    old = next(
        (
            record
            for record in _records(state)
            if str(record["id"]).casefold() == identifier.casefold()
        ),
        None,
    )
    if old is not None:
        _require(_payload(old) == _payload(item), "Conflicting repeated observation")
        return {"status": "already_recorded", "state": deepcopy(state), "record": deepcopy(old)}
    _require(
        actual_input.get("expected_revision", core.phases.revision(state))
        == core.phases.revision(state),
        "Stale handoff record revision",
    )
    if kind != "terminal" and not historical:
        _require(generation == intents[-1]["assignment_generation"], "Reviewer assignment changed")
        task_id = str(intent["subject"]["task_id"])
        context = _record(actual_input.get("context"), "current authoritative context")
        task = _task(state, task_id)
        attempt = core.Program.current_attempt(task)
        submission = _record(attempt["submission"])
        _require(
            task["status"] == "review"
            and submission.get("review") is None
            and _rows(attempt["rounds"])[int(submission["round"]) - 1].get("review") is None,
            "Submitted review is already terminal or no longer reviewable",
        )
        _require(
            submission.get("handoff_id", handoff_id) == handoff_id,
            "Submitted review handoff was replaced",
        )
        expected = _subject(state, task_id, context, actual_input.get("review_inputs", {}))
        _require(expected == intent["subject"], "Handoff subject is no longer current")
    history = [
        record
        for record in _records(state)
        if record["handoff_id"] == handoff_id
        and record["assignment_generation"] == generation
        and record["kind"] != "intent"
    ]
    dispatch_id = details.get("dispatch_attempt_id")
    dispatches = [
        record
        for record in history
        if record["kind"] in {"dispatch_sent", "dispatch_failed", "dispatch_unknown"}
        and _record(record["details"]).get("dispatch_attempt_id") == dispatch_id
    ]
    if kind == "dispatch_claimed":
        _require(
            details.get("action_id") == "ha-" + core.digest(_review_action_identity(intent)),
            "Claim current action correlation changed",
        )
        core.coordination.require_task_authority(state, actor)
        _require(
            _eligibility(
                state,
                _task(state, str(intent["subject"]["task_id"])),
                intent["reviewer_id"],
                intent["eligibility"],
            )["status"]
            == "eligible",
            "Reviewer eligibility no longer current",
        )
        processes = dispatch_processes(state)
        _require(
            not any(row["handoff_id"] == handoff_id and row["occupied"] for row in processes),
            "Prior dispatch unresolved across assignments",
        )
        policy = _actor(state, intent["reviewer_id"]).get("review_wip_limit")
        if policy is not None:
            _require(type(policy) is int and policy >= 0, "Invalid recorded review WIP policy")
            occupied = sum(
                row["reviewer_id"] == intent["reviewer_id"] and row["occupied"] for row in processes
            )
            if __package__:
                from tools import feature_program_queue as queue
            else:
                import feature_program_queue as queue
            occupied += sum(
                use["verifier_id"] == intent["reviewer_id"]
                for use in queue._recorded_use(queue._capture_context(state))
            )
            _require(occupied < policy, "Recorded review WIP limit reached")
    if kind in {"dispatch_sent", "dispatch_failed", "dispatch_unknown"}:
        core.coordination.require_task_authority(state, actor)
        current_eligibility = _eligibility(
            state,
            _task(state, str(intent["subject"]["task_id"])),
            intent["reviewer_id"],
            intent["eligibility"],
        )
        _require(
            historical or current_eligibility["status"] == "eligible",
            "Reviewer eligibility no longer current",
        )
        if historical:
            _require(
                any(
                    record["kind"] == "dispatch_claimed"
                    and _record(record["details"]).get("dispatch_attempt_id") == dispatch_id
                    for record in history
                ),
                "Historical outcome requires exact prior claim",
            )
        unresolved = [
            record
            for record in history
            if record["kind"] == "dispatch_unknown"
            and not any(
                _record(later["details"]).get("reconciles") == record["id"] for later in history
            )
        ]
        if dispatches:
            latest = dispatches[-1]
            _require(
                latest["kind"] == "dispatch_unknown"
                and details.get("reconciles") == latest["id"]
                and kind in {"dispatch_sent", "dispatch_failed"},
                "Dispatch attempt needs actual reconciliation",
            )
        else:
            _require(not unresolved, "Unknown native delivery must be reconciled before retry")
            sent = [record for record in history if record["kind"] == "dispatch_sent"]
            for record in sent:
                sent_id = _record(record["details"])["dispatch_attempt_id"]
                _require(
                    any(
                        later["kind"] == "terminal"
                        and _record(later["details"]).get("terminal_kind")
                        in {"finished", "canceled"}
                        and _record(later["details"]).get("dispatch_attempt_id") == sent_id
                        for later in history
                    ),
                    "Sent native attempt still lacks actual terminal outcome",
                )
    if kind in {"reviewer_acknowledged", "review_started"}:
        _require(
            not any(
                record["kind"] == "terminal"
                and _record(record["details"]).get("terminal_kind") in {"finished", "canceled"}
                and _record(record["details"]).get("dispatch_attempt_id") == dispatch_id
                for record in history
            ),
            "Actual native attempt already terminal",
        )
        _require(
            not any(
                record["kind"] == kind
                and _record(record["details"]).get("dispatch_attempt_id") == dispatch_id
                for record in history
            ),
            "Use stable ID for repeated ACK/start observation",
        )
        _require(
            historical or _actor(state, intent["reviewer_id"]).get("staffing_status") != "retired",
            "Retired designated reviewer",
        )
        current_eligibility = _eligibility(
            state,
            _task(state, str(intent["subject"]["task_id"])),
            intent["reviewer_id"],
            intent["eligibility"],
        )
        _require(
            historical or current_eligibility["status"] == "eligible",
            "Reviewer eligibility no longer current",
        )
        _require(
            bool(dispatches) and dispatches[-1]["kind"] == "dispatch_sent",
            "ACK/start requires exact actually sent attempt",
        )
    if kind == "terminal":
        terminal_kind = details["terminal_kind"]
        if terminal_kind == "canonical_verdict_observed":
            _require(actor == director, "Current director records ordinary verdict observation")
            subject = _record(intent["subject"])
            task = _task(state, str(subject["task_id"]))
            original_round = _rows(_rows(task["attempts"])[int(subject["attempt"]) - 1]["rounds"])[
                int(subject["submission_round"]) - 1
            ]
            _require(
                details["terminal_ref"] == original_round.get("review"),
                "Actual verdict observation lacks exact ordinary review reference",
            )
        else:
            _require(
                not any(
                    record["kind"] == "terminal"
                    and _record(record["details"]).get("terminal_kind") in {"finished", "canceled"}
                    and _record(record["details"]).get("dispatch_attempt_id") == dispatch_id
                    for record in history
                ),
                "Actual native attempt already terminal",
            )
            _require(
                actor == intent["reviewer_id"] or reporter == intent["reviewer_id"],
                "Actual process terminal must retain designated reviewer attribution",
            )
            _require(
                bool(dispatches) and dispatches[-1]["kind"] == "dispatch_sent",
                "Actual process terminal requires exact sent attempt",
            )
    proposed = _append(state, item)
    return {"status": "prepared", "state": proposed, "record": deepcopy(_records(proposed)[-1])}


def _locator(position: int, task_id: str, revision: str, record_digest: Json) -> Record:
    return {
        "path": "backlog.json",
        "pointer": f"/review_handoffs/records/{position}",
        "revision": revision,
        "task_id": task_id,
        "record_sha256": record_digest,
    }


def project_handoffs(state: Record, assessed_bindings: Record, limit: int | None = None) -> Record:
    """Project all retained facts before consumer bounds; never sample a read-time clock."""
    history = _validated_history(state)
    _require(limit is None or type(limit) is int and 0 <= limit <= 50, "Invalid row bound")
    revision = _core().phases.revision(state)
    records = history.records
    actors = {str(actor["id"]): actor for actor in reversed(_rows(state.get("actors", [])))}
    director = _director(state)
    positions = {str(item["id"]): index for index, item in enumerate(records)}
    intents = [item for item in records if item["kind"] == "intent"]
    latest = history.latest_generation
    rows: list[Json] = []
    for intent in intents:
        handoff_id = str(intent["handoff_id"])
        subject = _record(intent["subject"])
        task_id = str(subject["task_id"])
        generation = intent["assignment_generation"]
        reference = _locator(positions[str(intent["id"])], task_id, revision, intent["digest"])
        task = history.tasks[task_id]
        assessment = _record(assessed_bindings.get(handoff_id, assessed_bindings.get(task_id, {})))
        currentness, reasons = "unavailable", ["current_handoff_context_unavailable"]
        if assessment.get("context") is not None:
            try:
                current = _subject(
                    state,
                    task_id,
                    _record(assessment["context"]),
                    assessment.get("review_inputs", {}),
                    task=task,
                )
                currentness = "current" if current == subject else "stale"
                reasons = [] if currentness == "current" else ["handoff_subject_changed"]
            except _core().ProgramError:
                currentness, reasons = "stale", ["handoff_subject_changed"]
        if generation != latest[handoff_id]:
            currentness, reasons = "stale", [*reasons, "reviewer_assignment_changed"]
        if assessment.get("evidence_status") == "unavailable":
            currentness, reasons = "unavailable", [*reasons, "handoff_evidence_unavailable"]
        current_submission = _record(_core().Program.current_attempt(task).get("submission") or {})
        if current_submission.get("handoff_id") not in {None, handoff_id}:
            currentness, reasons = "stale", [*reasons, "handoff_subject_changed"]
        original = _rows(_rows(task["attempts"])[int(subject["attempt"]) - 1]["rounds"])[
            int(subject["submission_round"]) - 1
        ]
        terminal_ref = original.get("review")
        canonical_terminal = terminal_ref is not None
        related = history.groups[(handoff_id, int(generation))]
        observations: list[Json] = [
            {
                "id": intent["id"],
                "kind": "ready_recorded",
                "subject": deepcopy(subject),
                "observed_at": None,
                "clock_id": None,
                "timestamp_source": None,
                "boundary": None,
                "reference": reference,
                "actor_id": intent["actor_id"],
                "recorded_at": intent["recorded_at"],
                "dispatch_attempt_id": None,
            }
        ]
        submitted: set[str] = set()
        for item in related:
            details = _record(item["details"])
            ref = item.get("receipt_ref") or _locator(
                positions[str(item["id"])], task_id, revision, item["digest"]
            )
            observed = {
                **deepcopy(details),
                "id": item["id"],
                "kind": item["kind"],
                "subject": deepcopy(subject),
                "reference": deepcopy(ref),
                "actor_id": item["actor_id"],
                "reporter_id": item.get("reporter_id"),
                "recorded_at": item["recorded_at"],
            }
            for key in ("observed_at", "clock_id", "timestamp_source", "boundary"):
                observed.setdefault(key, None)
            observations.append(observed)
            attached = details.get("submitted_observation")
            if attached is not None:
                key = _core().digest(attached)
                if key not in submitted:
                    observations.append({**deepcopy(_record(attached)), "id": "submitted-" + key})
                    submitted.add(key)
        eligibility = deepcopy(_record(intent["eligibility"]))
        if eligibility["status"] == "eligible":
            eligibility = _eligibility(
                state,
                task,
                intent["reviewer_id"],
                {"status": "eligible"},
                actors=actors,
                director=director,
            )
            if eligibility["status"] != "eligible":
                currentness, reasons = "stale", [*reasons, "reviewer_eligibility_changed"]
        phase = "ready" if eligibility["status"] == "eligible" else "unassigned"
        next_actor = director
        for item in related:
            match item["kind"]:
                case "submitted":
                    pass
                case "dispatch_claimed":
                    phase = "claimed"
                case "dispatch_sent":
                    phase = "sent"
                case "dispatch_failed":
                    phase = "delivery_failed"
                case "dispatch_unknown":
                    phase = "delivery_unknown"
                case "reviewer_acknowledged":
                    phase = "acknowledged"
                case "review_started":
                    phase = "started"
                case "terminal":
                    phase = "terminal"
                case invalid:
                    raise _core().ProgramError(f"Invalid projected observation: {invalid}")
        if canonical_terminal:
            phase = "terminal"
        if phase in {"sent", "acknowledged", "started"}:
            next_actor = intent["reviewer_id"]
        action = {
            "ready": "Dispatch exact packet through actual native transport",
            "claimed": "Reconcile retained dispatch claim with actual native outcome",
            "unassigned": "Resolve explicit reviewer eligibility before dispatch",
            "sent": "Wait for actual reviewer acknowledgement/start",
            "delivery_failed": "Resolve actual transport failure before explicit retry",
            "delivery_unknown": "Reconcile actual native delivery before any resend",
            "acknowledged": "Reviewer starts the exact current packet review",
            "started": "Reviewer reports the actual bounded review outcome",
            "terminal": "Inspect ordinary review acceptance and director obligations",
        }[phase]
        if currentness != "current" and phase != "terminal":
            next_actor, action = director, "Revalidate stale or unavailable inputs"
        if phase == "terminal":
            next_actor = director
        rows.append(
            {
                "id": f"{handoff_id}:{generation}",
                "handoff_id": handoff_id,
                "assignment_generation": generation,
                "recorded_sequence": positions[str(intent["id"])],
                "task_id": task_id,
                "attempt": subject["attempt"],
                "submission_round": subject["submission_round"],
                "subject": deepcopy(subject),
                "packet_ref": deepcopy(intent["packet_ref"]),
                "reviewer_id": intent["reviewer_id"],
                "director_id": intent["director_id"],
                "eligibility": eligibility,
                "currentness": currentness,
                "canonical_terminal": canonical_terminal,
                "terminal_ref": deepcopy(terminal_ref),
                "phase": phase,
                "next_actor_id": next_actor,
                "next_action": action,
                "next_trigger": _record(related[-1]["details"]).get("next_trigger", action)
                if related
                else action,
                "reasons": reasons,
                "reference": reference,
                "observations": observations,
                "observations_total": len(observations),
                "runtime_capacity": "unknown",
                "native_liveness": "unknown",
                "dispatches": _dispatch_rows(related, positions, task_id, revision),
                "superseded_running_uncertainty": currentness != "current"
                and any(
                    item["occupied"]
                    for item in _dispatch_rows(related, positions, task_id, revision)
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            str(row["task_id"]),
            str(row["handoff_id"]),
            int(row["assignment_generation"]),
        )
    )
    counts = {
        "phase": dict(Counter(str(row["phase"]) for row in rows)),
        "currentness": dict(Counter(str(row["currentness"]) for row in rows)),
    }
    shown = rows if limit is None else rows[:limit]
    result = {
        "schema_version": 1,
        "status": "recorded" if "review_handoffs" in state else "absent",
        "rows": {
            "total": len(rows),
            "shown": len(shown),
            "omitted": len(rows) - len(shown),
            "items": shown,
            "reference": {
                "path": "backlog.json",
                "pointer": "/review_handoffs",
                "revision": revision,
            },
        },
        "counts": counts,
        "source_binding": {"revision": revision},
        "acceptance_complete": False,
    }
    _require(len(_core().canonical(result)) <= LIMIT, "Handoff projection exceeds output budget")
    return result


def bound_projection(full: Record, limit: int = 50) -> Record:
    """Detach a bounded view of complete assessed facts without replaying the producer."""
    _require(type(limit) is int and 0 <= limit <= 50, "Invalid row bound")
    collection = _record(full["rows"])
    rows = _rows(collection["items"])
    _require(
        collection.get("omitted") == 0
        and collection.get("total") == len(rows)
        and collection.get("shown") == len(rows),
        "Handoff bounding requires complete assessed facts",
    )
    result = deepcopy(
        {
            **full,
            "rows": {
                **collection,
                "items": rows[:limit],
                "shown": min(len(rows), limit),
                "omitted": max(0, len(rows) - limit),
            },
        }
    )
    _require(len(_core().canonical(result)) <= LIMIT, "Handoff projection exceeds output budget")
    return result


def _dispatch_rows(
    related: list[Record], positions: dict[str, int], task_id: str, revision: str
) -> list[Record]:
    """Reduce each actual process independently; another terminal never clears it."""
    result: dict[str, Record] = {}
    statuses = {
        "dispatch_claimed": "claimed",
        "dispatch_sent": "sent",
        "dispatch_failed": "failed",
        "dispatch_unknown": "unknown",
        "reviewer_acknowledged": "acknowledged",
        "review_started": "started",
    }
    for item in related:
        details = _record(item["details"])
        identifier = details.get("dispatch_attempt_id")
        kind = str(item["kind"])
        status = statuses.get(kind)
        if kind == "terminal" and details.get("terminal_kind") in {"finished", "canceled"}:
            status = str(details["terminal_kind"])
        if not isinstance(identifier, str) or status is None:
            continue
        result[identifier] = {
            "dispatch_attempt_id": identifier,
            "status": status,
            "occupied": status not in {"failed", "finished", "canceled"},
            "ambiguous": status in {"claimed", "unknown"},
            "reference": _locator(positions[str(item["id"])], task_id, revision, item["digest"]),
        }
    return [result[key] for key in sorted(result)]


def dispatch_processes(state: Record) -> list[Record]:
    """Full validated recorded workload, including superseded nonterminal processes."""
    history = _validated_history(state)
    positions = {str(item["id"]): index for index, item in enumerate(history.records)}
    revision = _core().phases.revision(state)
    result = []
    for (identifier, generation), related in history.groups.items():
        intent = next(
            item
            for item in history.records
            if item["kind"] == "intent"
            and item["handoff_id"] == identifier
            and item["assignment_generation"] == generation
        )
        for row in _dispatch_rows(related, positions, str(intent["subject"]["task_id"]), revision):
            result.append(
                {
                    **row,
                    "handoff_id": identifier,
                    "assignment_generation": generation,
                    "reviewer_id": intent["reviewer_id"],
                    "task_id": intent["subject"]["task_id"],
                    "superseded": generation != history.latest_generation[identifier],
                }
            )
    return result


def prepare_dispatch_claim(state: Record, actual_input: Record) -> Record:
    """Retain one guarded intent to call native transport; this never sends."""
    _require(actual_input.get("kind") == "dispatch_claimed", "Exact dispatch claim kind required")
    return prepare_observation(state, actual_input)


def prepare_report_records(state: Record, exact_action: Record, actual_report: Record) -> Record:
    """Convert explicitly attributed actual milestones into inert normal record templates."""
    core = _core()
    _require(
        actual_report.get("action_id") == exact_action.get("action_id"),
        "Report action correlation changed",
    )
    if exact_action.get("assignment_kind") != "independent_review":
        return {
            "schema_version": 1,
            "kind": "handoff_report_preparation",
            "status": "incomplete",
            "inputs": [],
            "missing_fields": ["worker_assignment_report_requires_ordinary_note"],
        }
    intent = next(
        (
            item
            for item in _validated_history(state).records
            if item["kind"] == "intent"
            and item["handoff_id"] == exact_action.get("handoff_id")
            and item["assignment_generation"] == exact_action.get("assignment_generation")
        ),
        None,
    )
    _require(
        intent is not None
        and exact_action.get("action_id") == "ha-" + core.digest(_review_action_identity(intent))
        and intent["subject"] == exact_action.get("subject")
        and intent["packet_ref"] == exact_action.get("packet_ref"),
        "Report exact assignment changed",
    )
    _require(
        actual_report.get("reporter_id") == intent["reviewer_id"],
        "Report lacks designated reviewer",
    )
    refs = _refs(actual_report.get("evidence_refs", []))
    _require(bool(refs), "Actual report evidence required")
    dispatch = _text(actual_report.get("dispatch_attempt_id"), "reported dispatch")
    _require(
        any(
            item["handoff_id"] == intent["handoff_id"]
            and item["assignment_generation"] == intent["assignment_generation"]
            and item["dispatch_attempt_id"] == dispatch
            and item["status"] in {"sent", "acknowledged", "started", "finished", "canceled"}
            for item in dispatch_processes(state)
        ),
        "Report requires exact actually sent process",
    )
    templates = []
    already_recorded = []
    seen = set()
    allowed = {
        "kind",
        "reason",
        "observed_at",
        "clock_id",
        "timestamp_source",
        "boundary",
        "terminal_kind",
        "outcome",
        "terminal_ref",
        "next_trigger",
    }
    for milestone in _rows(actual_report.get("milestones", [])):
        _require(set(milestone) <= allowed, "Unknown report milestone fields")
        kind = _text(milestone.get("kind"), "reported milestone")
        _require(
            kind in {"reviewer_acknowledged", "review_started", "terminal"} and kind not in seen,
            "Invalid or duplicate reported milestone",
        )
        seen.add(kind)
        details = {**deepcopy(milestone), "dispatch_attempt_id": dispatch, "evidence_refs": refs}
        _text(details.get("reason"), "actual report reason")
        _milestone(details, kind)
        template = {
            "schema_version": 1,
            "operation": "review-handoff-record",
            "id": "report-"
            + core.digest(
                {
                    "action": exact_action["action_id"],
                    "dispatch": dispatch,
                    "milestone": milestone,
                    "refs": refs,
                }
            ),
            "handoff_id": intent["handoff_id"],
            "assignment_generation": intent["assignment_generation"],
            "subject": deepcopy(intent["subject"]),
            "packet_ref": deepcopy(intent["packet_ref"]),
            "actor_id": _director(state),
            "reporter_id": intent["reviewer_id"],
            **details,
        }
        if actual_report.get("recording_mode") is not None:
            _require(
                actual_report["recording_mode"] == "historical", "Invalid report recording mode"
            )
            template["recording_mode"] = "historical"
        existing = next((item for item in _records(state) if item["id"] == template["id"]), None)
        if existing is not None:
            mechanical = {
                "schema_version",
                "operation",
                "id",
                "kind",
                "handoff_id",
                "assignment_generation",
                "subject",
                "packet_ref",
                "actor_id",
                "reporter_id",
            }
            expected_details = {
                key: value for key, value in template.items() if key not in mechanical
            }
            _require(
                all(
                    existing[key] == template[key]
                    for key in (
                        "kind",
                        "handoff_id",
                        "assignment_generation",
                        "subject",
                        "packet_ref",
                        "reporter_id",
                    )
                )
                and existing["details"] == expected_details,
                "Conflicting retained report milestone",
            )
            position = next(
                index for index, item in enumerate(_records(state)) if item["id"] == existing["id"]
            )
            already_recorded.append(
                {
                    "id": existing["id"],
                    "reference": deepcopy(existing.get("receipt_ref"))
                    or _locator(
                        position,
                        str(intent["subject"]["task_id"]),
                        core.phases.revision(state),
                        existing["digest"],
                    ),
                }
            )
        else:
            templates.append(template)
    _require(bool(seen), "Actual report has no milestones")
    return {
        "schema_version": 1,
        "kind": "handoff_report_preparation",
        "status": "prepared" if templates else "already_recorded",
        "inputs": templates,
        "already_recorded": already_recorded,
        "missing_fields": ["per_record_current_binding"] if templates else [],
        "acceptance_complete": False,
    }


def _review_action_identity(intent: Record) -> Record:
    return {
        "assignment_kind": "independent_review",
        "task_id": intent["subject"]["task_id"],
        "subject": deepcopy(intent["subject"]),
        "packet_ref": deepcopy(intent["packet_ref"]),
        "recipient_actor_id": intent["reviewer_id"],
        "handoff_id": intent["handoff_id"],
        "assignment_generation": intent["assignment_generation"],
    }
