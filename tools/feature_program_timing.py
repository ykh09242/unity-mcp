"""Pure timing from retained work-program observations, never read-time clocks.

Inputs are complete, coherently validated host assessments. These projections grant
no readiness, execution or review authority and never mutate the supplied records.
"""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from statistics import median
from typing import Final, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
REVIEW_METRICS: Final = (
    ("submission_to_dispatch", "submitted", "dispatch", "submission_observed_to_dispatch_outcome"),
    ("dispatch_to_ack", "dispatch", "ack", "dispatch_sent_to_acknowledged"),
    ("ack_to_start", "ack", "start", "acknowledged_to_review_started"),
    ("submission_to_start", "submitted", "start", "submission_observed_to_review_started"),
    ("start_to_terminal", "start", "terminal", "review_started_to_terminal_observed"),
    ("submission_to_terminal", "submitted", "terminal", "submission_observed_to_terminal_observed"),
)
TERMINAL_PHASES: Final = frozenset({"terminal", "finished", "canceled"})


@dataclass(frozen=True, slots=True)
class TimingError(ValueError):
    """A supplied assessment violates the finite pure projection contract."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise TimingError(reason)


def _record(value: Json) -> Record:
    _require(isinstance(value, dict), "Timing requires object facts")
    return value


def _records(value: Json) -> list[Record]:
    _require(isinstance(value, list), "Timing requires complete record lists")
    return [_record(item) for item in value]


def _stable(value: Json) -> Json:
    """Keep exact retained reference identity without a volatile projection revision."""
    match value:
        case dict():
            return {
                key: _stable(item)
                for key, item in value.items()
                if key != "revision" or "pointer" not in value or "path" not in value
            }
        case list():
            return [_stable(item) for item in value]
        case float():
            return value if math.isfinite(value) else {"invalid_number": str(value)}
        case str() | int() | bool() | None:
            return value
        case invalid:
            raise TimingError(f"Non-JSON timing fact: {type(invalid).__name__}")


def _digest(value: Json) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _reference(value: Record) -> Record:
    supplied = value.get("reference") or value.get("receipt")
    if supplied is not None:
        reference = _record(_stable(_record(supplied)))
        if "pointer" in reference and "record_sha256" not in reference:
            reference = {
                **reference,
                "record_sha256": _digest(
                    _stable({key: item for key, item in value.items() if key != "reference"})
                ),
            }
        return reference
    return {"path": "backlog.json", "record_sha256": _digest(_stable(value))}


def _endpoint(value: Record | None, *, boundary: str | None = None) -> Record | None:
    if value is None:
        return None
    return {
        "observed_at": value.get("observed_at")
        if isinstance(value.get("observed_at"), str)
        else None,
        "clock_id": value.get("clock_id") if isinstance(value.get("clock_id"), str) else None,
        "timestamp_source": value.get("timestamp_source") or "retained_observation",
        "boundary": boundary or value.get("boundary") or value.get("kind"),
        "reference": _reference(value),
    }


def _instant(value: Record | None) -> tuple[datetime | None, str | None]:
    if value is None:
        return None, "missing_endpoint"
    text = value.get("observed_at")
    if not isinstance(text, str) or not text:
        return None, "missing_observed_at"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid_timestamp"
    if parsed.tzinfo is None:
        return None, "naive_timestamp"
    try:
        return parsed.astimezone(timezone.utc), None
    except (OverflowError, ValueError):
        return None, "invalid_timestamp"


def _delta(start: Record | None, end: Record | None) -> tuple[float | None, list[Json]]:
    first, first_problem = _instant(start)
    last, last_problem = _instant(end)
    problems: list[Json] = [item for item in (first_problem, last_problem) if item]
    if start is not None and end is not None:
        clock = start.get("clock_id")
        if not isinstance(clock, str) or not clock.strip() or not end.get("clock_id"):
            problems.append("missing_clock")
        elif clock != end["clock_id"]:
            problems.append("mixed_clocks")
    if problems or first is None or last is None:
        return None, list(dict.fromkeys(problems))
    if last < first:
        return None, ["inverted_endpoints"]
    return (last - first).total_seconds(), []


def _as_of(value: Record | None) -> Record | None:
    if value is None or value.get("as_of") is None:
        return None
    supplied = value["as_of"]
    if isinstance(supplied, dict):
        return _endpoint(supplied, boundary="last_observed_as_of")
    return _endpoint({**value, "observed_at": supplied}, boundary="last_observed_as_of")


def _outcome(value: Json) -> str:
    match value:
        case "completed" | "complete" | "accepted" | "passed" | "done":
            return "completed"
        case "rejected" | "rework":
            return "rejected"
        case "canceled" | "cancelled":
            return "canceled"
        case "failed" | "dispatch_failed" | "timed_out" | "spawn_failed" | "interrupted":
            return "failed"
        case _:
            return "unknown"


def _interval(
    base: Record,
    kind: str,
    start_value: Record | None,
    end_value: Record | None,
    *,
    segment: Json,
    label: str,
    terminal: bool = False,
    as_of: Record | None = None,
) -> Record:
    start, end = _endpoint(start_value), _endpoint(end_value)
    seconds, reasons = _delta(start, end)
    state = "closed" if seconds is not None else "unknown"
    age = None
    if end is None and start is not None:
        instant, problem = _instant(start)
        if (
            instant is not None
            and problem is None
            and isinstance(start.get("clock_id"), str)
            and str(start.get("clock_id")).strip()
        ):
            state = "canonical_terminal_timing_unknown" if terminal else "open"
            reasons = ["terminal_time_unobserved"] if terminal else []
            if not terminal and as_of is not None:
                elapsed, age_problems = _delta(start, as_of)
                if elapsed is not None:
                    age = {
                        "as_of": as_of["observed_at"],
                        "clock_id": as_of["clock_id"],
                        "seconds": elapsed,
                        "reference": as_of["reference"],
                    }
                else:
                    reasons.extend(age_problems)
        else:
            if not isinstance(start.get("clock_id"), str) or not str(start.get("clock_id")).strip():
                reasons.append("missing_clock")
            if terminal:
                state = "canonical_terminal_timing_unknown"
    if as_of is not None:
        for point in (start, end):
            if point is None or point.get("clock_id") != as_of.get("clock_id"):
                continue
            instant, _ = _instant(point)
            observed, _ = _instant(as_of)
            if instant is not None and observed is not None and instant > observed:
                seconds, age, state = None, None, "unknown"
                reasons.append("future_endpoint")
    identity = [base.get("subject_id"), kind, segment]
    return {key: deepcopy(value) for key, value in base.items() if key != "subject_id"} | {
        "interval_id": "timing-" + _digest(identity),
        "kind": kind,
        "boundary_label": label,
        "start": start,
        "end": end,
        "state": state,
        "closed_seconds": seconds,
        "age": age,
        "reasons": list(dict.fromkeys(reasons)),
        "references": [point["reference"] for point in (start, end) if point is not None],
    }


def _task_map(state: Record) -> dict[str, Record]:
    return {str(task["id"]): task for task in _records(state.get("tasks", []))}


def _base(
    task: Record,
    *,
    attempt: Json,
    round_number: Json,
    subject_id: Json,
    handoff_id: Json = None,
    request_id: Json = None,
    capture_id: Json = None,
) -> Record:
    return {
        "subject_id": subject_id,
        "task_id": task["id"],
        "attempt": attempt,
        "round": round_number,
        "handoff_id": handoff_id,
        "request_id": request_id,
        "capture_id": capture_id,
        "canonical_phase": task.get("status"),
        "observation_phase": "unknown",
        "applicability": "historical",
        "outcome": "unknown",
        "cause": "unobserved",
        "resolver_id": None,
        "next_trigger": None,
    }


def _single(values: list[Record], kind: str) -> Record | None:
    matches = [value for value in values if value.get("kind") == kind]
    return matches[0] if len(matches) == 1 else None


def _review_rows(
    state: Record, handoffs: list[Record]
) -> tuple[list[Record], list[Record], set[tuple]]:
    tasks = _task_map(state)
    intervals: list[Record] = []
    current: list[Record] = []
    covered: set[tuple] = set()
    latest_rows = {}
    for position, candidate in enumerate(handoffs):
        key = (
            candidate.get("task_id"),
            candidate.get("attempt"),
            candidate.get("submission_round"),
        )
        sequence = (
            candidate.get("recorded_sequence", position),
            candidate.get("assignment_generation", 0),
        )
        if key not in latest_rows or sequence > latest_rows[key][0]:
            latest_rows[key] = (sequence, candidate["id"])
    for row in handoffs:
        task = tasks.get(str(row.get("task_id")))
        _require(task is not None, "Timing handoff task is unavailable")
        observations = _records(row.get("observations", []))
        _require(
            row.get("observations_total", len(observations)) == len(observations),
            "Timing requires complete handoff observations",
        )
        subject = _record(row.get("subject"))
        _require(
            subject.get("program_id") == state.get("program_id")
            and subject.get("task_id") == row.get("task_id")
            and subject.get("attempt") == row.get("attempt")
            and subject.get("submission_round") == row.get("submission_round"),
            "Timing handoff subject identity mismatch",
        )
        for value in observations:
            if value.get("subject") is not None:
                _require(value["subject"] == subject, "Timing observation subject mismatch")
        attempt, round_number = row.get("attempt"), row.get("submission_round")
        covered.add((task["id"], attempt, round_number))
        base = _base(
            task,
            attempt=attempt,
            round_number=round_number,
            subject_id=row["id"],
            handoff_id=row["handoff_id"],
        )
        base.update(
            {
                "observation_phase": row.get("phase", "unknown"),
                "applicability": row.get("currentness", "unavailable"),
                "resolver_id": row.get("next_actor_id"),
                "next_trigger": row.get("next_trigger"),
                "cause": {
                    "ready": "dispatch_pending",
                    "unassigned": "reviewer_unassigned",
                    "sent": "ack_pending",
                    "acknowledged": "start_pending",
                    "started": "terminal_pending",
                    "delivery_failed": "dispatch_failed",
                    "delivery_unknown": "dispatch_unknown",
                    "terminal": "terminal",
                }.get(str(row.get("phase")), "unobserved"),
            }
        )
        submitted = _single(observations, "submitted")
        dispatch_ids = list(
            dict.fromkeys(
                str(value["dispatch_attempt_id"])
                for value in observations
                if value.get("dispatch_attempt_id") is not None
            )
        ) or [None]
        row_intervals: list[Record] = []
        active_intervals: list[Record] = []
        for dispatch_id in dispatch_ids:
            selected = [
                value for value in observations if value.get("dispatch_attempt_id") == dispatch_id
            ]
            # Reconciliation can replace an explicitly referenced unknown outcome, not a sent start.
            sent = _single(selected, "dispatch_sent")
            dispatch = (
                sent
                or _single(selected, "dispatch_failed")
                or _single(selected, "dispatch_unknown")
            )
            acknowledged = _single(selected, "reviewer_acknowledged")
            started = _single(selected, "review_started")
            finished = _single(selected, "terminal")
            if (
                finished is None
                and started is not None
                and _single(observations, "review_started") is started
            ):
                finished = _single(
                    [
                        value
                        for value in observations
                        if value.get("dispatch_attempt_id") is None
                        and value.get("terminal_kind") == "canonical_verdict_observed"
                    ],
                    "terminal",
                )
            points = {
                "submitted": submitted,
                "dispatch": dispatch,
                "ack": acknowledged,
                "start": started,
                "terminal": finished,
            }
            outcome_base = {
                **base,
                "outcome": _outcome(
                    (finished or {}).get("outcome")
                    or (finished or {}).get("result")
                    or (dispatch or {}).get("kind")
                ),
            }
            active_intervals = []
            for kind, first, last, label in REVIEW_METRICS:
                if kind in {"submission_to_start", "submission_to_terminal"}:
                    continue
                start_value, end_value = points[first], points[last]
                if kind == "dispatch_to_ack" and sent is None:
                    start_value = None
                active_intervals.append(
                    _interval(
                        outcome_base,
                        kind,
                        start_value,
                        end_value,
                        segment=[dispatch_id, (start_value or {}).get("id") or first],
                        label=label,
                        terminal=row.get("canonical_terminal") is True or finished is not None,
                        as_of=_as_of(row),
                    )
                )
            row_intervals.extend(active_intervals)
        direct_start = _single(observations, "review_started")
        direct_terminal = _single(observations, "terminal")
        # These direct spans belong to the submission, not each failed dispatch trial.
        direct_base = {**base, "outcome": _outcome((direct_terminal or {}).get("outcome"))}
        direct_rows = []
        for kind, end_value, label in (
            ("submission_to_start", direct_start, "submission_observed_to_review_started"),
            ("submission_to_terminal", direct_terminal, "submission_observed_to_terminal_observed"),
        ):
            direct_rows.append(
                _interval(
                    direct_base,
                    kind,
                    submitted,
                    end_value,
                    segment="submission",
                    label=label,
                    terminal=row.get("canonical_terminal") is True,
                    as_of=_as_of(row),
                )
            )
        row_intervals.extend(direct_rows)
        active_intervals.extend(direct_rows)
        intervals.extend(row_intervals)
        task_attempts = _records(task.get("attempts", []))
        latest_attempt = task_attempts[-1] if task_attempts else {}
        task_rounds = _records(latest_attempt.get("rounds", []))
        latest_round = task_rounds[-1] if task_rounds else {}
        current_key = (task["id"], attempt, round_number)
        is_current_round = attempt == latest_attempt.get(
            "number"
        ) and round_number == latest_round.get("round")
        if is_current_round and latest_rows[current_key][1] == row["id"]:
            pending = next(
                (
                    item
                    for item in active_intervals
                    if item["kind"] == "submission_to_start" and item["state"] == "open"
                ),
                None,
            )
            if pending is None:
                pending = next(
                    (
                        item
                        for item in active_intervals
                        if item["kind"] == "start_to_terminal" and item["state"] == "open"
                    ),
                    None,
                )
            if row.get("currentness") != "current":
                pending = None
                base = {**base, "cause": "handoff_revalidation_required"}
            current.append(
                {
                    "id": row["id"],
                    "queue_row_id": json.dumps(
                        ["task_review", task["id"], attempt, round_number],
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                    **{
                        key: deepcopy(base[key])
                        for key in (
                            "task_id",
                            "attempt",
                            "round",
                            "handoff_id",
                            "canonical_phase",
                            "observation_phase",
                            "applicability",
                            "cause",
                            "resolver_id",
                            "next_trigger",
                        )
                    },
                    "waiting_since": pending["start"] if pending else None,
                    "age": pending["age"] if pending else None,
                    "interval_ids": [item["interval_id"] for item in row_intervals],
                    "reasons": deepcopy(row.get("reasons", [])),
                }
            )
    return intervals, current, covered


def _legacy_rows(state: Record, covered: set[tuple]) -> tuple[list[Record], list[Record]]:
    intervals, current = [], []
    events = _records(state.get("events", []))
    for task in _records(state.get("tasks", [])):
        attempts = _records(task.get("attempts", []))
        for attempt in attempts:
            rounds = _records(attempt.get("rounds", []))
            for round_item in rounds:
                number, round_number = attempt.get("number"), round_item.get("round")
                if (task["id"], number, round_number) in covered:
                    continue
                identity = [
                    "legacy",
                    state["program_id"],
                    task["id"],
                    number,
                    round_number,
                    round_item.get("receipt"),
                    round_item.get("source_digest"),
                ]
                base = _base(task, attempt=number, round_number=round_number, subject_id=identity)
                latest = attempt is attempts[-1] and round_item is rounds[-1]
                base.update(
                    {
                        "cause": "legacy_history_unavailable",
                        "applicability": "current" if latest else "historical",
                    }
                )
                terminal = round_item.get("review") is not None or task.get("status") == "done"
                rows = [
                    _interval(base, kind, None, None, segment=first, label=label, terminal=terminal)
                    for kind, first, _, label in REVIEW_METRICS
                ]
                intervals.extend(rows)
                starts = [
                    event
                    for event in events
                    if event.get("action") == "submit"
                    and event.get("id") == task["id"]
                    and event.get("receipt") == round_item.get("receipt")
                    and event.get("source_digest") == round_item.get("source_digest")
                ]
                ends = [
                    event
                    for event in events
                    if event.get("action") in {"complete", "rework"}
                    and event.get("id") == task["id"]
                    and round_item.get("review") is not None
                    and event.get("review") == round_item["review"]
                    and event.get("source_digest") == round_item.get("source_digest")
                ]
                if len(starts) == len(ends) == 1:

                    def recorded(event: Record) -> Record:
                        return {
                            "id": _digest(event),
                            "kind": event["action"],
                            "observed_at": event.get("timestamp"),
                            "clock_id": "canonical_recorded_history",
                            "timestamp_source": "recorded_history",
                            "boundary": event["action"],
                            "reference": _reference(event),
                        }

                    intervals.append(
                        _interval(
                            {**base, "outcome": _outcome(ends[0]["action"])},
                            "recorded_history_span",
                            recorded(starts[0]),
                            recorded(ends[0]),
                            segment="recorded",
                            label="recorded_submit_to_verdict_history",
                        )
                    )
                if latest:
                    current.append(
                        {
                            "id": "timing-" + _digest(identity),
                            "queue_row_id": json.dumps(
                                ["task_review", task["id"], number, round_number],
                                separators=(",", ":"),
                            ),
                            **{
                                key: deepcopy(base[key])
                                for key in (
                                    "task_id",
                                    "attempt",
                                    "round",
                                    "handoff_id",
                                    "canonical_phase",
                                    "observation_phase",
                                    "applicability",
                                    "cause",
                                    "resolver_id",
                                    "next_trigger",
                                )
                            },
                            "waiting_since": None,
                            "age": None,
                            "interval_ids": [item["interval_id"] for item in rows],
                            "reasons": ["legacy_history_unavailable"],
                        }
                    )
    return intervals, current


def _recorded_block_rows(state: Record) -> list[Record]:
    """Retain uniquely bound canonical block history without calling it actual wait."""
    result = []
    events = _records(state.get("events", []))
    for task in _records(state.get("tasks", [])):
        for attempt in _records(task.get("attempts", [])):
            blocks = _records(attempt.get("blocks", []))
            resolutions = _records(attempt.get("resolutions", []))
            origins = sorted({int(item["origin_event_index"]) for item in blocks})
            for origin in origins:
                _require(0 <= origin < len(events), "Unavailable recorded block identity")
                first = events[origin]
                members = {
                    item["event_index"] for item in blocks if item["origin_event_index"] == origin
                }
                endings = [
                    item
                    for item in resolutions
                    if _record(_record(item.get("binding", {})).get("block", {})).get("event_index")
                    in members
                ]
                end = endings[0] if len(endings) == 1 else None
                base = _base(
                    task,
                    attempt=attempt.get("number"),
                    round_number=None,
                    subject_id=[
                        "recorded_block",
                        state["program_id"],
                        task["id"],
                        attempt.get("number"),
                        origin,
                    ],
                )
                base.update(
                    {
                        "cause": "blocked",
                        "resolver_id": (end or {}).get("actor"),
                        "next_trigger": first.get("next_action"),
                    }
                )

                def point(value: Record, time_key: str, boundary: str) -> Record:
                    return {
                        "observed_at": value.get(time_key),
                        "clock_id": "canonical_recorded_history",
                        "timestamp_source": "recorded_history",
                        "boundary": boundary,
                        "reference": _reference(value),
                    }

                result.append(
                    _interval(
                        base,
                        "recorded_history_span",
                        point(first, "timestamp", "recorded_block"),
                        point(end, "recorded_at", "recorded_resolution") if end else None,
                        segment=origin,
                        label="recorded_block_to_resolution_history",
                    )
                )
    return result


def _missing_capture_rows(state: Record, facts: Record, captures: list[Record]) -> list[Record]:
    """Keep known referenced-but-unavailable captures in the coverage denominator."""
    available = {_record(item["reference"]).get("sha256") for item in captures}
    seen = set()
    result = []
    tasks = _task_map(state)
    for item in _records(facts.get("records", [])):
        value = _record(item["input"])
        if value.get("run_capture_ref") is None:
            continue
        reference = _record(value["run_capture_ref"])
        checksum = reference.get("sha256")
        if checksum in available or checksum in seen:
            continue
        seen.add(checksum)
        task = tasks[str(value["task_id"])]
        context = _record(item.get("context", {}))
        base = _base(
            task,
            attempt=context.get("attempt"),
            round_number=None,
            subject_id=["missing_capture", checksum],
            capture_id=checksum,
            request_id=item.get("request_id"),
        )
        measured = _interval(
            base,
            "command_process",
            None,
            None,
            segment="process",
            label="sealed_process_monotonic_span",
        )
        measured.update({"reasons": ["capture_unavailable"], "references": [deepcopy(reference)]})
        result.append(measured)
    return result


def _verification_rows(state: Record, facts: Record) -> list[Record]:
    records = _records(facts.get("records", []))
    tasks = _task_map(state)
    grouped: dict[str, list[Record]] = defaultdict(list)
    newest = {}
    for item in records:
        value = _record(item.get("input"))
        grouped[str(item["request_id"])].append(item)
        if value.get("kind") == "request":
            newest[(value["task_id"], value.get("lane"))] = item["request_id"]
    result: list[Record] = []
    for request_id, items in grouped.items():
        request_items = [item for item in items if _record(item["input"]).get("kind") == "request"]
        _require(len(request_items) == 1, "Timing requires one exact verification request")
        request = request_items[0]
        authored = _record(request["input"])
        task = tasks.get(str(authored["task_id"]))
        _require(task is not None, "Timing verification task unavailable")
        context = _record(request.get("context", {}))
        submission = _record(context.get("submission") or {})
        base = _base(
            task,
            attempt=context.get("attempt"),
            round_number=submission.get("round"),
            subject_id=["verification", request_id, request.get("request_version")],
            request_id=request_id,
        )
        assessed = _record(facts.get("applicability", {})).get(request_id, "unavailable")
        _require(
            assessed in {"current", "stale", "unavailable"}, "Invalid assessed timing applicability"
        )
        attempts = _records(task.get("attempts", []))
        current_attempt = attempts[-1].get("number") if attempts else None
        historical = (
            newest.get((task["id"], authored.get("lane"))) != request_id
            or context.get("attempt") != current_attempt
        )
        base.update(
            {
                "applicability": "historical" if historical else assessed,
                "resolver_id": authored.get("verifier_id"),
                "next_trigger": authored.get("next_trigger"),
            }
        )
        waiting: Record | None = None
        blocked: Record | None = None
        running: list[Record] = []
        blocked_as_of: Record | None = None
        phase = "requested"
        for item in items:
            value = {**_record(item["input"]), "reference": _reference(item)}
            kind = value["kind"]
            match kind:
                case "request":
                    waiting = value
                case "blocked":
                    if blocked is None:
                        blocked = value
                    blocked_as_of = _as_of(value)
                case "started" | "finished" | "canceled" | "resolution":
                    terminal = kind in {"finished", "canceled"}
                    if waiting is not None and kind != "resolution":
                        result.append(
                            _interval(
                                {
                                    **base,
                                    "observation_phase": kind,
                                    "cause": "verification_pending",
                                },
                                "verification_wait",
                                waiting,
                                value,
                                segment=waiting["id"],
                                label="verification_requested_to_progress_observed",
                                terminal=terminal,
                            )
                        )
                        waiting = None
                    if blocked is not None:
                        result.append(
                            _interval(
                                {
                                    **base,
                                    "observation_phase": kind,
                                    "cause": "blocked",
                                    "resolver_id": _record(blocked.get("blocker") or {}).get(
                                        "resolver_id"
                                    )
                                    or base["resolver_id"],
                                    "next_trigger": _record(blocked.get("blocker") or {}).get(
                                        "next_trigger"
                                    )
                                    or base["next_trigger"],
                                },
                                "blocked_span",
                                blocked,
                                value,
                                segment=blocked["id"],
                                label="blocked_to_resolution_observed",
                            )
                        )
                        blocked = None
                    if kind == "started":
                        running.append(value)
                    if terminal:
                        for started in running:
                            measured = _interval(
                                {
                                    **base,
                                    "observation_phase": kind,
                                    "outcome": _outcome(value.get("result") or kind),
                                    "cause": "verification_running",
                                },
                                "verification_observed_span",
                                started,
                                value if len(running) == 1 else None,
                                segment=started["id"],
                                label="verification_started_to_terminal_observed",
                                terminal=True,
                            )
                            if len(running) > 1:
                                measured["reasons"] = ["ambiguous_terminal_association"]
                            result.append(measured)
                        running = []
                case "finding":
                    continue
                case invalid:
                    raise TimingError(f"Unsupported verification timing kind: {invalid}")
            phase = str(kind)
        for opened, kind, cause, label in (
            (
                waiting,
                "verification_wait",
                "verification_pending",
                "verification_requested_to_progress_observed",
            ),
            (blocked, "blocked_span", "blocked", "blocked_to_resolution_observed"),
        ):
            if opened is not None:
                result.append(
                    _interval(
                        {**base, "observation_phase": phase, "cause": cause},
                        kind,
                        opened,
                        None,
                        segment=opened["id"],
                        label=label,
                        as_of=blocked_as_of if kind == "blocked_span" else _as_of(opened),
                    )
                )
        for started in running:
            result.append(
                _interval(
                    {**base, "observation_phase": phase, "cause": "verification_running"},
                    "verification_observed_span",
                    started,
                    None,
                    segment=started["id"],
                    label="verification_started_to_terminal_observed",
                )
            )
    return result


def _capture_rows(state: Record, facts: list[Record]) -> list[Record]:
    result: list[Record] = []
    seen: dict[str, str] = {}
    tasks = _task_map(state)
    for item in facts:
        capture = _record(item.get("capture"))
        reference = _record(item.get("reference"))
        identity = _digest(["original_capture", reference["sha256"]])
        content = _digest(_stable(capture))
        if identity in seen:
            _require(seen[identity] == content, "Conflicting original timing capture")
            continue
        seen[identity] = content
        task = tasks.get(str(capture.get("task_id")))
        _require(task is not None, "Timing capture task unavailable")
        process = _record(capture.get("process"))
        start = {
            "observed_at": process.get("started_at"),
            "clock_id": process.get("clock_id"),
            "timestamp_source": "sealed_runner",
            "boundary": "process_launch_observed",
            "reference": reference,
        }
        end = {**start, "observed_at": process.get("ended_at"), "boundary": "process_end_observed"}
        base = _base(
            task,
            attempt=capture.get("attempt"),
            round_number=None,
            subject_id=identity,
            request_id=capture.get("request_id"),
            capture_id=capture.get("run_id"),
        )
        base.update(
            {
                "observation_phase": "finished",
                "cause": "captured_process",
                "outcome": _outcome(
                    ("passed" if process.get("exit_code") == 0 else "failed")
                    if process.get("outcome") == "exited"
                    else process.get("outcome")
                ),
            }
        )
        measured = _interval(
            base,
            "command_process",
            start,
            end,
            segment="process",
            label="sealed_process_monotonic_span",
            terminal=True,
        )
        elapsed = process.get("elapsed_seconds")
        if (
            type(elapsed) in {int, float}
            and math.isfinite(elapsed)
            and elapsed >= 0
            and _instant(start)[0] is not None
            and _instant(end)[0] is not None
            and process.get("clock_id")
            and measured["closed_seconds"] is not None
        ):
            measured.update({"closed_seconds": elapsed, "state": "closed"})
        else:
            measured.update(
                {
                    "closed_seconds": None,
                    "state": "unknown",
                    "reasons": [*measured["reasons"], "process_duration_unavailable"],
                }
            )
        result.append(measured)
    return result


def _summaries(rows: list[Record]) -> list[Json]:
    groups: dict[tuple[str, str, str], list[Record]] = defaultdict(list)
    for row in rows:
        groups[(str(row["kind"]), str(row["boundary_label"]), "all_observed")].append(row)
        groups[(str(row["kind"]), str(row["boundary_label"]), str(row["outcome"]))].append(row)
    result: list[Json] = []
    for (kind, boundary, outcome), values in sorted(groups.items()):
        closed = [value["closed_seconds"] for value in values if value["state"] == "closed"]
        result.append(
            {
                "kind": kind,
                "boundary_label": boundary,
                "outcome": outcome,
                "relevant_total": len(values),
                "closed_valid": len(closed),
                "open": sum(value["state"] == "open" for value in values),
                "unknown": sum(
                    value["state"] in {"unknown", "canonical_terminal_timing_unknown"}
                    for value in values
                ),
                "excluded": 0,
                "min_seconds": min(closed) if closed else None,
                "max_seconds": max(closed) if closed else None,
                "median_seconds": median(closed) if closed else None,
            }
        )
    return result


def _collection(rows: list[Record], limit: int) -> Record:
    return {
        "items": deepcopy(rows[:limit]),
        "total": len(rows),
        "shown": min(len(rows), limit),
        "omitted": max(0, len(rows) - limit),
    }


def project(
    state: Record,
    *,
    handoff_facts: Record,
    verification_facts: Record,
    capture_facts: list[Record],
    source_binding: Record,
    max_rows: int = 50,
) -> Record:
    """Project complete retained facts before bounding, with no clock, IO or mutation."""
    _require(type(max_rows) is int and 0 <= max_rows <= 10000, "Invalid timing row bound")
    _require(
        source_binding.get("program_id") == state.get("program_id"),
        "Timing program identity mismatch",
    )
    for facts in (handoff_facts, verification_facts):
        _require(
            type(facts.get("schema_version")) is int and facts["schema_version"] == 1,
            "Invalid timing assessment schema version",
        )
    collection = _record(handoff_facts.get("rows"))
    handoffs = _records(collection.get("items"))
    _require(
        collection.get("omitted", 0) == 0
        and collection.get("total", len(handoffs)) == len(handoffs)
        and collection.get("shown", len(handoffs)) == len(handoffs),
        "Timing requires complete handoff facts before bounding",
    )
    intervals, current, covered = _review_rows(state, handoffs)
    legacy, legacy_current = _legacy_rows(state, covered)
    intervals.extend(legacy)
    current.extend(legacy_current)
    intervals.extend(_recorded_block_rows(state))
    intervals.extend(_verification_rows(state, verification_facts))
    intervals.extend(_capture_rows(state, capture_facts))
    intervals.extend(_missing_capture_rows(state, verification_facts, capture_facts))
    intervals.sort(key=lambda row: str(row["interval_id"]))
    current.sort(key=lambda row: str(row["id"]))
    _require(
        len({row["interval_id"] for row in intervals}) == len(intervals),
        "Duplicate logical timing interval",
    )
    tasks = _task_map(state)
    objective = _record(state.get("objective") or {})
    members = set(objective.get("task_ids", []))
    _require(members <= tasks.keys(), "Timing objective contains unknown member")
    for key, expected in (
        ("objective_id", objective.get("id")),
        ("membership_revision", objective.get("membership_revision")),
    ):
        if key in source_binding:
            _require(source_binding[key] == expected, "Timing objective identity mismatch")
    for row in intervals:
        row["cohort_ids"] = ["all_retained"] + (
            ["current_objective"] if row["task_id"] in members else []
        )
    cohorts: list[Json] = [
        {
            "id": "all_retained",
            "objective_id": None,
            "membership_revision": None,
            "task_total": len(tasks),
            "interval_total": len(intervals),
            "summaries": _summaries(intervals),
        }
    ]
    if objective:
        selected = [row for row in intervals if row["task_id"] in members]
        cohorts.append(
            {
                "id": "current_objective",
                "objective_id": objective.get("id"),
                "membership_revision": objective.get("membership_revision"),
                "task_total": len(members),
                "interval_total": len(selected),
                "summaries": _summaries(selected),
            }
        )
    binding = {
        key: deepcopy(source_binding[key])
        for key in ("program_id", "objective_id", "membership_revision")
        if key in source_binding
    }
    binding["input_digest"] = _digest(
        {"binding": binding, "intervals": intervals, "current": current, "cohorts": cohorts}
    )
    unknown = sum(
        row["state"] in {"unknown", "canonical_terminal_timing_unknown"} for row in intervals
    )
    return {
        "schema_version": 1,
        "source_binding": binding,
        "cohorts": cohorts,
        "current": _collection(current, max_rows),
        "intervals": _collection(intervals, max_rows),
        "coverage": {
            "status": "complete",
            "relevant_total": len(intervals),
            "represented_total": len(intervals),
            "unknown_total": unknown,
            "open_total": sum(row["state"] == "open" for row in intervals),
            "excluded_total": 0,
            "reasons": ["unobserved_or_incompatible_intervals"] if unknown else [],
        },
    }
