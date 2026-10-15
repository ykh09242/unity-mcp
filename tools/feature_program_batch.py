"""Bounded normal-record batches: one canonical publication, exact audit and safe retry."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol
import re

if __package__:
    from tools import feature_program_coordination as coordination
else:
    import feature_program_coordination as coordination

if TYPE_CHECKING:
    from tools.feature_program import PreparedChange, Program, ReadBudget

Json = coordination.Json
Record = coordination.Record
LIMIT: Final = 8 * 1024 * 1024
READ_LIMIT: Final = 64 * 1024 * 1024
MAX_OPERATIONS: Final = 2000
OPERATIONS: Final = frozenset(
    {
        "verification-record",
        "submit",
        "complete",
        "rework",
        "note",
        "checkpoint",
        "review-handoff-record",
    }
)
REQUEST_FIELDS: Final = frozenset(
    {"schema_version", "id", "owner_id", "expected_revision", "scope", "refs", "operations"}
)
SEMANTIC_FIELDS: Final = (
    "schema_version",
    "id",
    "program_id",
    "owner_id",
    "base_revision",
    "source_binding",
    "producer",
    "refs",
    "operations",
)
PLAN_FIELDS: Final = frozenset(
    {
        *SEMANTIC_FIELDS,
        "stages",
        "normal_events",
        "event_start",
        "event_count",
        "event_prefix_sha256",
        "event_span_sha256",
        "marker",
        "final_revision",
        "plan_sha256",
        "envelope_digest",
    }
)
MARKER_FIELDS: Final = frozenset(
    {
        "action",
        "id",
        "base_revision",
        "plan_sha256",
        "operation_ids",
        "operation_refs",
        "operation_event_counts",
        "evidence_refs",
        "event_start",
        "event_count",
        "event_span_sha256",
        "timestamp",
    }
)


class BatchError(coordination.GovernanceError):
    """An invalid plan or conflicted batch; never a signal to replay operations."""


@dataclass(frozen=True, slots=True)
class _CheckedPlan:
    """A detached plan admitted once; private consumers must not mutate its value."""

    value: Record


@dataclass(frozen=True, slots=True)
class _CheckedHistory:
    """Complete validated event facts for one immutable state observation only."""

    events: tuple[Json, ...]


def _check_plan(value: Record) -> _CheckedPlan:
    return _CheckedPlan(_validate_plan(value))


class BatchHost(Protocol):
    """Normal authoritative adapters; only publish_batch may publish canonical state."""

    def identity(self) -> Record: ...
    def revision(self, state: Record) -> str: ...
    def prepare(
        self, state: Record, operation: str, payload: Record, observed_at: str, *, owner: str
    ) -> PreparedChange: ...
    def preflight_batch(
        self, original_state: Record, final_state: Record, stages: list[Json]
    ) -> PreparedChange: ...
    def inspect_views(self, state: Record) -> bool: ...
    def publish_batch(self, plan: Record, owner: str, expected_revision: str) -> Record: ...


def _core():
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchError(message)


def _record(value: Json, name: str) -> Record:
    return coordination._record(value, name)


def _items(value: Json, name: str) -> list[Json]:
    return coordination._items(value, name)


def _text(value: Json, name: str) -> str:
    text = coordination._text(value, name)
    _require(len(text.encode()) <= 4096, f"{name} exceeds text budget")
    return text


def _hash(value: Json, name: str) -> str:
    text = _text(value, name)
    _require(re.fullmatch(r"[0-9a-f]{64}", text) is not None, f"Invalid {name}")
    return text


def _bounded(value: Record) -> Record:
    _require(len(_core().canonical(value)) <= LIMIT, "Batch document exceeds 8 MiB")
    return value


def _ref(value: Json) -> Record:
    ref = _record(value, "batch reference")
    _require(set(ref) == {"path", "sha256"}, "Batch reference requires exact path/hash")
    path = _core().safe_relative(_text(ref["path"], "reference path"))
    _require(
        path not in {"backlog.json", "BOARD.md", "CHECKPOINT.md"} and not path.startswith("tasks/"),
        "Mutable state/view is not immutable batch evidence",
    )
    _hash(ref["sha256"], "reference SHA256")
    return {"path": path, "sha256": ref["sha256"]}


def _refs(program: Program, values: list[Json], budget: ReadBudget | None = None) -> list[Json]:
    _require(len(values) <= MAX_OPERATIONS, "Batch reference count exceeds budget")
    reader = budget if budget is not None else _core().ReadBudget(READ_LIMIT)
    result: list[Json] = []
    seen: set[str] = set()
    for value in values:
        ref = _ref(value)
        path = str(ref["path"])
        raw = reader.read(program.path(path), LIMIT)
        _require(_core().sha(raw) == ref["sha256"], "Immutable batch reference changed")
        if path in seen:
            _require(ref in result, "Conflicting reference hashes for one batch path")
        else:
            seen.add(path)
            result.append(ref)
    return result


def _operations(value: Json) -> list[Json]:
    operations = _items(value, "batch operations")
    _require(0 < len(operations) <= MAX_OPERATIONS, "Invalid batch operation count")
    identifiers: set[str] = set()
    for value in operations:
        row = _record(value, "batch operation")
        _require(
            set(row) == {"id", "operation", "payload", "observed_at"},
            "Unknown or missing batch operation fields",
        )
        identifier = _text(row["id"], "operation ID")
        _require(identifier not in identifiers, "Duplicate operation ID")
        identifiers.add(identifier)
        _require(_text(row["operation"], "operation") in OPERATIONS, "Unsupported batch operation")
        payload = _record(row["payload"], "operation payload")
        _require(
            not ({"argv", "command", "repo", "program_dir"} & payload.keys()),
            "Stored execution or location override is prohibited",
        )
        coordination._timestamp(row["observed_at"], "operation observation time")
    return deepcopy(operations)


def _operation_refs(
    program: Program, operation: Record, budget: ReadBudget | None = None
) -> list[Json]:
    reader = budget if budget is not None else _core().ReadBudget(READ_LIMIT)
    payload = _record(operation["payload"], "operation payload")
    paths = [
        _core().safe_relative(_text(payload[key], key))
        for key in ("input", "receipt", "review", "revalidation")
        if key in payload
    ]
    if "evidence" in payload:
        paths.extend(_core().strings(payload["evidence"], "operation evidence"))
    return _refs(
        program,
        [
            {"path": path, "sha256": _core().sha(reader.read(program.path(path), LIMIT))}
            for path in dict.fromkeys(paths)
        ],
        reader,
    )


def _semantic(plan: Record) -> Record:
    return {key: deepcopy(plan[key]) for key in SEMANTIC_FIELDS}


def _marker(plan: Record) -> Record:
    stages = [_record(value, "batch stage") for value in _items(plan["stages"], "batch stages")]
    operations = [_record(value, "operation") for value in _items(plan["operations"], "operations")]
    return {
        "action": "record_batch",
        "id": plan["id"],
        "base_revision": plan["base_revision"],
        "plan_sha256": plan["plan_sha256"],
        "operation_ids": [row["id"] for row in operations],
        "operation_refs": [deepcopy(row["refs"]) for row in stages],
        "evidence_refs": deepcopy(plan["refs"]),
        "operation_event_counts": [row["event_count"] for row in stages],
        "event_start": plan["event_start"],
        "event_count": plan["event_count"],
        "event_span_sha256": plan["event_span_sha256"],
        "timestamp": operations[-1]["observed_at"],
    }


def _validate_plan(value: Record) -> Record:
    plan = _bounded(deepcopy(value))
    _require(
        set(plan) == PLAN_FIELDS
        and type(plan.get("schema_version")) is int
        and plan["schema_version"] == 1,
        "Invalid batch plan schema",
    )
    _text(plan["id"], "batch ID")
    _text(plan["program_id"], "program ID")
    _text(plan["owner_id"], "publisher")
    for key in (
        "base_revision",
        "final_revision",
        "plan_sha256",
        "envelope_digest",
        "event_prefix_sha256",
        "event_span_sha256",
    ):
        _hash(plan[key], key)
    _operations(plan["operations"])
    _require(_core().digest(_semantic(plan)) == plan["plan_sha256"], "Semantic batch plan changed")
    unsigned = {key: value for key, value in plan.items() if key != "envelope_digest"}
    _require(_core().digest(unsigned) == plan["envelope_digest"], "Batch envelope changed")
    stages = _items(plan["stages"], "batch stages")
    normal = _items(plan["normal_events"], "normal events")
    _require(len(stages) == len(plan["operations"]), "Batch stage count differs")
    _require(
        type(plan["event_start"]) is int
        and plan["event_start"] >= 0
        and type(plan["event_count"]) is int
        and plan["event_count"] == len(normal),
        "Invalid batch event span",
    )
    _require(_core().digest(normal) == plan["event_span_sha256"], "Batch normal events changed")
    binding = _record(plan["source_binding"], "batch source binding")
    _require(set(binding) == {"scope", "source_head", "source_digest"}, "Invalid source binding")
    _require(
        bool(_core().strings(binding["scope"], "batch source scope")), "Empty batch source scope"
    )
    _text(binding["source_head"], "batch source HEAD")
    _hash(binding["source_digest"], "batch source digest")
    _record(plan["producer"], "batch producer")
    refs = [_ref(ref) for ref in _items(plan["refs"], "plan references")]
    _require(refs == plan["refs"], "Stored plan references must use canonical relative paths")
    _require(
        len(refs) <= MAX_OPERATIONS and len({str(ref["path"]) for ref in refs}) == len(refs),
        "Invalid or repeated plan references",
    )
    offset = 0
    predecessor = plan["base_revision"]
    for operation, value in zip(plan["operations"], stages, strict=True):
        row = _record(operation, "batch operation")
        stage = _record(value, "batch stage")
        _require(
            set(stage)
            == {
                *row,
                "before_revision",
                "after_revision",
                "event_start",
                "event_count",
                "event_span_sha256",
                "refs",
            },
            "Invalid batch stage schema",
        )
        _require(
            all(stage[key] == item for key, item in row.items()),
            "Stage operation differs from semantic request",
        )
        _hash(stage["before_revision"], "stage predecessor revision")
        _hash(stage["after_revision"], "stage resulting revision")
        count = stage["event_count"]
        _require(
            stage["before_revision"] == predecessor
            and type(count) is int
            and count > 0
            and type(stage["event_start"]) is int
            and stage["event_start"] == plan["event_start"] + offset,
            "Stage predecessor or event interval differs",
        )
        span = normal[offset : offset + count]
        _require(
            len(span) == count
            and _core().digest(span) == stage["event_span_sha256"]
            and _record(span[0], "normal event").get("action") == row["operation"],
            "Stage normal audit span differs",
        )
        _require(
            all(_record(event, "normal event").get("action") in OPERATIONS for event in span),
            "Stage contains unsupported audit action",
        )
        for ref in _items(stage["refs"], "stage references"):
            _require(_ref(ref) in refs, "Stage reference not retained in plan")
        predecessor = stage["after_revision"]
        offset += count
    _require(offset == len(normal), "Stage spans do not cover all normal events")
    _require(plan["marker"] == _marker(plan), "Batch marker differs from exact plan")
    return plan


def _compose(
    program: Program,
    state: Record,
    plan: Record,
    host: BatchHost,
    budget: ReadBudget | None = None,
) -> tuple[Record, list[Json]]:
    """Keep only the current projected predecessor; the host retains required guard facts."""
    proposal = deepcopy(state)
    reader = budget if budget is not None else _core().ReadBudget(READ_LIMIT)
    stages: list[Json] = []
    before = host.revision(proposal)
    for value in _items(plan["operations"], "operations"):
        row = _record(value, "operation")
        refs = _operation_refs(program, row, reader)
        events = deepcopy(_items(proposal.get("events"), "predecessor events"))
        prepare_stage = getattr(host, "prepare_stage", None)
        arguments = (
            proposal,
            str(row["operation"]),
            deepcopy(_record(row["payload"], "payload")),
            str(row["observed_at"]),
        )
        if prepare_stage is not None:
            prepared, after_revision = prepare_stage(*arguments, owner=str(plan["owner_id"]))
            _hash(after_revision, "prepared stage revision")
        else:
            prepared = host.prepare(*arguments, owner=str(plan["owner_id"]))
            after_revision = host.revision(prepared.state)
        after = _items(prepared.state.get("events"), "projected events")
        _require(
            after[: len(events)] == events and len(after) > len(events),
            "Each batch operation must append ordinary events without rewriting history",
        )
        appended = deepcopy(after[len(events) :])
        stages.append(
            {
                **deepcopy(row),
                "before_revision": before,
                "after_revision": after_revision,
                "event_start": len(events),
                "event_count": len(appended),
                "event_span_sha256": _core().digest(appended),
                "refs": refs,
            }
        )
        proposal = prepared.state
        before = after_revision
    return proposal, stages


def prepare_batch(program: Program, state: Record, request: Record, host: BatchHost) -> Record:
    """Validate all projected ordinary operations before any canonical publication."""
    _bounded(request)
    _require(
        set(request) == REQUEST_FIELDS
        and type(request.get("schema_version")) is int
        and request["schema_version"] == 1,
        "Invalid batch request schema",
    )
    program.require_current_location()
    validate_history(state)
    identifier = _text(request["id"], "batch ID")
    _require(
        not any(
            _record(e, "event").get("action") == "record_batch"
            and _record(e, "event").get("id") == identifier
            for e in _items(state.get("events"), "events")
        ),
        "Batch ID already recorded",
    )
    base_revision = host.revision(state)
    _require(request["expected_revision"] == base_revision, "Stale original batch revision")
    scope = _core().strings(request["scope"], "batch source scope")
    _require(bool(scope), "Batch source scope cannot be empty")
    operations = _operations(request["operations"])
    reader = _core().ReadBudget(READ_LIMIT)
    refs = list(_items(request["refs"], "batch refs"))
    for value in operations:
        refs.extend(_operation_refs(program, _record(value, "operation"), reader))
    refs = _refs(program, refs, reader)
    plan: Record = {
        "schema_version": 1,
        "id": identifier,
        "program_id": state["program_id"],
        "owner_id": _text(request["owner_id"], "batch publisher"),
        "base_revision": base_revision,
        "source_binding": {
            "scope": scope,
            "source_head": program.current_head(),
            "source_digest": program.source_digest(scope),
        },
        "producer": deepcopy(host.identity()),
        "refs": refs,
        "operations": operations,
    }
    plan["plan_sha256"] = _core().digest(_semantic(plan))
    proposal, stages = _compose(program, state, plan, host, reader)
    original_events = _items(state.get("events"), "original events")
    normal = deepcopy(_items(proposal.get("events"), "projected events")[len(original_events) :])
    plan.update(
        {
            "stages": stages,
            "normal_events": normal,
            "event_start": len(original_events),
            "event_count": len(normal),
            "event_prefix_sha256": _core().digest(original_events),
            "event_span_sha256": _core().digest(normal),
        }
    )
    plan["marker"] = _marker(plan)
    _items(proposal["events"], "events").append(deepcopy(plan["marker"]))
    prepared = host.preflight_batch(state, proposal, stages)
    _require(prepared.state == proposal, "Host preflight changed the prepared batch")
    plan["final_revision"] = host.revision(proposal)
    _require(host.revision(state) == base_revision, "Preparation changed its input registry")
    _current(program, plan, host)
    plan["envelope_digest"] = _core().digest(plan)
    return _validate_plan(plan)


def _current(program: Program, plan: Record, host: BatchHost) -> None:
    _require(plan["producer"] == host.identity(), "Batch producer changed")
    binding = _record(plan["source_binding"], "batch source binding")
    _require(set(binding) == {"scope", "source_head", "source_digest"}, "Invalid source binding")
    scope = _core().strings(binding["scope"], "batch source scope")
    _require(
        bool(scope)
        and program.current_head() == binding["source_head"]
        and program.source_digest(scope) == binding["source_digest"],
        "Batch source changed",
    )
    _refs(program, _items(plan["refs"], "plan refs"))


def reconstruct(program: Program, state: Record, value: Record, host: BatchHost) -> PreparedChange:
    """Reprepare exact records under the host lock and retain its final freshness callback."""
    return _reconstruct_checked(program, state, _check_plan(value), host)


def _reconstruct_checked(
    program: Program, state: Record, checked: _CheckedPlan, host: BatchHost
) -> PreparedChange:
    """Reuse only plan parsing; all live predecessor/source/ref/callback guards remain."""
    plan = checked.value
    _require(plan["program_id"] == state.get("program_id"), "Batch program differs")
    _require(host.revision(state) == plan["base_revision"], "Original batch predecessor changed")
    program.require_current_location()
    validate_history(state)
    _current(program, plan, host)
    proposal, stages = _compose(program, state, plan, host)
    _require(stages == plan["stages"], "Ordinary batch stage preparation changed")
    events = _items(proposal["events"], "projected events")
    start = int(plan["event_start"])
    _require(
        _core().digest(events[:start]) == plan["event_prefix_sha256"]
        and events[start:] == plan["normal_events"],
        "Batch event identity changed",
    )
    events.append(deepcopy(plan["marker"]))
    _require(host.revision(proposal) == plan["final_revision"], "Final batch identity changed")
    prepared = host.preflight_batch(state, proposal, stages)
    _require(prepared.state == proposal, "Host preflight changed the exact final state")
    return prepared


def _validate_history(state: Record) -> None:
    """Validate all canonical marker identities/spans before selecting any historical result."""
    events = _items(state.get("events", []), "events")
    seen: set[str] = set()
    previous_marker = -1
    for index, value in enumerate(events):
        row = _record(value, "event")
        if row.get("action") != "record_batch":
            continue
        _require(set(row) == MARKER_FIELDS, "Invalid canonical batch marker shape")
        identifier = _text(row["id"], "canonical batch ID")
        _require(identifier not in seen, "Duplicate canonical batch ID")
        seen.add(identifier)
        _hash(row["base_revision"], "batch original revision")
        _hash(row["plan_sha256"], "stable semantic plan SHA256")
        _hash(row["event_span_sha256"], "normal event SHA256")
        coordination._timestamp(row["timestamp"], "batch marker timestamp")
        ids = _core().strings(row["operation_ids"], "operation IDs")
        refs = _items(row["operation_refs"], "operation refs")
        counts = _items(row["operation_event_counts"], "operation event counts")
        _require(
            0 < len(ids) <= MAX_OPERATIONS and len(ids) == len(refs) == len(counts),
            "Invalid canonical batch operation mapping",
        )
        _require(
            all(type(count) is int and count > 0 for count in counts),
            "Each batch operation needs positive normal audit count",
        )
        start, count = row["event_start"], row["event_count"]
        _require(
            type(start) is int
            and type(count) is int
            and count > 0
            and start > previous_marker
            and start + count == index
            and sum(counts) == count,
            "Invalid canonical batch event span",
        )
        normal = events[start:index]
        _require(
            all(_record(e, "normal event").get("action") in OPERATIONS for e in normal),
            "Batch span contains an unsupported or nested operation",
        )
        _require(_core().digest(normal) == row["event_span_sha256"], "Canonical batch span changed")
        evidence = [_ref(ref) for ref in _items(row["evidence_refs"], "retained batch evidence")]
        _require(evidence == row["evidence_refs"], "Canonical batch references are not normalized")
        _require(
            len(evidence) <= MAX_OPERATIONS
            and len({str(ref["path"]) for ref in evidence}) == len(evidence),
            "Invalid or repeated canonical batch evidence",
        )
        for group in refs:
            for ref in _items(group, "operation reference group"):
                _require(
                    _ref(ref) in evidence, "Operation reference missing from retained evidence"
                )
        previous_marker = index


def _check_history(state: Record) -> _CheckedHistory:
    _validate_history(state)
    return _CheckedHistory(tuple(deepcopy(_items(state.get("events", []), "events"))))


def validate_history(state: Record) -> None:
    _check_history(state)


def evidence_references(state: Record) -> list[Json]:
    """Expose exact marker control refs for the core's existing shared-budget byte verification."""
    return _history_references(_check_history(state))


def _history_references(checked: _CheckedHistory) -> list[Json]:
    """Reuse full-history parsing, retaining differing hashes for ordinary ref rejection."""
    refs: list[Json] = []
    seen: set[tuple[str, str]] = set()
    for value in checked.events:
        row = _record(value, "event")
        if row.get("action") == "record_batch":
            for ref in _items(row["evidence_refs"], "retained batch evidence"):
                item = _record(ref, "batch reference")
                key = (str(item["path"]), str(item["sha256"]))
                if key not in seen:
                    seen.add(key)
                    refs.append(deepcopy(ref))
    return refs


def result_for(
    plan: Record,
    status: str,
    actual_revision: str | None,
    *,
    view_recovery_required: bool = False,
    reason_code: str = "validated_not_published",
    applicability: str = "current",
    limit: int = 50,
) -> Record:
    """One bounded safe DTO; counts never infer approval, native execution or actual IO cost."""
    _require(type(limit) is int and 0 < limit <= MAX_OPERATIONS, "Invalid batch result limit")
    _require(
        status
        in {
            "not_started",
            "not_committed",
            "committed",
            "committed_view_failed",
            "already_committed",
            "conflict",
            "unknown",
        },
        "Unknown batch publication status",
    )
    operations = [_record(value, "operation") for value in _items(plan["operations"], "operations")]
    committed = status in {"committed", "committed_view_failed", "already_committed"}
    items: list[Json] = []
    for row in operations[:limit]:
        payload = _record(row["payload"], "payload")
        items.append(
            {
                "id": row["id"],
                "operation": row["operation"],
                "task_id": payload.get("id"),
                "status": "committed"
                if committed
                else "conflict"
                if status == "conflict"
                else "validated",
            }
        )
    return _bounded(
        {
            "schema_version": 1,
            "id": plan["id"],
            "status": status,
            "preparation": {"status": "validated"},
            "publication": {
                "status": status,
                "view_recovery_required": view_recovery_required,
                "reason_code": reason_code,
            },
            "applicability": applicability,
            "reason_code": reason_code,
            "view_recovery_required": view_recovery_required,
            "base_revision": plan.get("base_revision"),
            "final_revision": plan.get("final_revision"),
            "actual_revision": actual_revision,
            "requested_operation_count": len(operations),
            "validated_operation_count": len(operations),
            "committed_operation_count": len(operations) if committed else 0,
            "operations": {
                "items": items,
                "total": len(operations),
                "shown": len(items),
                "omitted": len(operations) - len(items),
            },
            "next_actor_id": None
            if committed and not view_recovery_required
            else plan.get("owner_id"),
            "next_action": "render"
            if view_recovery_required
            else None
            if committed
            else "inspect_batch"
            if status in {"conflict", "unknown"}
            else "publish_batch",
            "actual_write_count": None,
            "actual_render_count": None,
            "accepted": False,
            "execution_replayed": False,
            "remote_verified_by_tool": False,
        }
    )


def inspect_batch(
    program: Program, state: Record, value: Record, host: BatchHost, *, limit: int = 50
) -> Record:
    """Recognize exact commitment, including later history, without replay or source rebinding."""
    return _inspect_checked(program, state, _check_plan(value), host, limit=limit)


def _inspect_checked(
    program: Program,
    state: Record,
    checked: _CheckedPlan,
    host: BatchHost,
    *,
    limit: int = 50,
    history: _CheckedHistory | None = None,
) -> Record:
    """Inspect sealed plan facts while freshly checking canonical/source/evidence state."""
    plan = checked.value
    _require(plan["program_id"] == state.get("program_id"), "Batch program differs")
    program.require_current_location()
    history = history if history is not None else _check_history(state)
    _require(
        history.events == tuple(_items(state.get("events", []), "events")),
        "Batch history observation differs",
    )
    _refs(program, _items(plan["refs"], "batch refs"))
    events = _items(state.get("events"), "events")
    markers = [
        _record(e, "event")
        for e in events
        if _record(e, "event").get("action") == "record_batch"
        and _record(e, "event").get("id") == plan["id"]
    ]
    actual = host.revision(state)
    if markers:
        start = int(plan["event_start"])
        end = start + int(plan["event_count"])
        exact = (
            markers == [plan["marker"]]
            and events[start:end] == plan["normal_events"]
            and _core().digest(events[:start]) == plan["event_prefix_sha256"]
        )
        if not exact:
            return result_for(
                plan,
                "conflict",
                actual,
                reason_code="batch_identity_conflict",
                applicability="stale",
                limit=limit,
            )
        recovery = not host.inspect_views(state)
        applicability = "historical"
        if actual == plan["final_revision"]:
            try:
                _current(program, plan, host)
                applicability = "current"
            except (BatchError, _core().ProgramError):
                applicability = "stale"
        return result_for(
            plan,
            "already_committed",
            actual,
            view_recovery_required=recovery,
            reason_code="derived_views_stale" if recovery else "exact_commit",
            applicability=applicability,
            limit=limit,
        )
    if actual != plan["base_revision"]:
        return result_for(
            plan,
            "conflict",
            actual,
            reason_code="base_revision_changed",
            applicability="stale",
            limit=limit,
        )
    _current(program, plan, host)
    return result_for(plan, "not_started", actual, limit=limit)


def publish_batch(
    program: Program, plan: Record, host: BatchHost, owner: str, expected_revision: str
) -> Record:
    """Delegate one locked publication; never loop over individual canonical mutations."""
    checked = _check_plan(plan)
    _require(owner == checked.value["owner_id"], "Batch publisher differs")
    _hash(expected_revision, "current transaction revision")
    publish_checked = getattr(host, "_publish_checked_batch", None)
    raw = (
        publish_checked(checked, owner, expected_revision)
        if publish_checked is not None
        else host.publish_batch(checked.value, owner, expected_revision)
    )
    status = _text(raw.get("status"), "batch publication outcome")
    revision = raw.get("actual_revision", raw.get("revision"))
    _require(revision is None or isinstance(revision, str), "Invalid actual publication revision")
    if status in {"committed", "committed_view_failed"}:
        _require(
            revision == checked.value["final_revision"],
            "Host did not confirm exact canonical batch",
        )
    recovery = raw.get("view_recovery_required", status == "committed_view_failed")
    _require(type(recovery) is bool, "Invalid view recovery outcome")
    return result_for(
        checked.value,
        status,
        revision,
        view_recovery_required=recovery,
        reason_code=str(
            raw.get("reason_code", "derived_views_stale" if recovery else "exact_commit")
        ),
        applicability=str(raw.get("applicability", "current")),
    )


def project_latest_batch(state: Record, *, limit: int = 50) -> Record | None:
    """Project actual latest canonical marker only; source/current preflight and IO remain unknown."""
    return _project_latest_checked(_check_history(state), limit=limit)


def _project_latest_checked(checked: _CheckedHistory, *, limit: int = 50) -> Record | None:
    events = checked.events
    markers = [
        _record(e, "event") for e in events if _record(e, "event").get("action") == "record_batch"
    ]
    if not markers:
        return None
    marker = markers[-1]
    normal = events[
        int(marker["event_start"]) : int(marker["event_start"]) + int(marker["event_count"])
    ]
    operations: list[Json] = []
    offset = 0
    for identifier, count in zip(
        marker["operation_ids"], marker["operation_event_counts"], strict=True
    ):
        event = _record(normal[offset], "ordinary batch event")
        operations.append(
            {
                "id": identifier,
                "operation": event["action"],
                "payload": {"id": event.get("id", event.get("task_id"))},
            }
        )
        offset += count
    result = result_for(
        {"id": marker["id"], "base_revision": marker["base_revision"], "operations": operations},
        "committed",
        None,
        reason_code="recorded_marker_only",
        applicability="unknown",
        limit=limit,
    )
    result["preparation"] = {"status": "recorded"}
    result["history"] = {"total": len(markers), "shown": 1, "omitted": len(markers) - 1}
    return result


def project_selection(
    program: Program, state: Record, selection: Record, host: BatchHost, *, limit: int = 50
) -> Record:
    """Inspect a frozen plan or actual retained result/failure, never a synthetic commit claim."""
    _bounded(selection)
    kind = selection.get("kind")
    if kind is None:
        return inspect_batch(program, state, selection, host, limit=limit)
    if kind == "batch_publication_result":
        _require(
            set(selection) == {"schema_version", "kind", "plan_ref", "outcome"}
            and type(selection["schema_version"]) is int
            and selection["schema_version"] == 1,
            "Invalid batch publication result wrapper",
        )
        ref = _refs(program, [selection["plan_ref"]])[0]
        plan, captured = _core().read_json_frozen(program.path(str(ref["path"])), limit=LIMIT)
        _require(captured == ref["sha256"], "Selected batch plan changed")
        outcome = _record(selection["outcome"], "recorded publication outcome")
        status = _text(outcome.get("status"), "recorded publication status")
        _require(
            status
            in {
                "not_started",
                "not_committed",
                "committed",
                "committed_view_failed",
                "already_committed",
                "conflict",
                "unknown",
            },
            "Invalid recorded batch outcome",
        )
        current = inspect_batch(program, state, plan, host, limit=limit)
        claimed_commit = status in {"committed", "committed_view_failed", "already_committed"}
        if claimed_commit and current["status"] != "already_committed":
            current["status"] = "conflict"
            current["publication"] = {
                "status": "conflict",
                "view_recovery_required": False,
                "reason_code": "recorded_commit_missing",
            }
            current["reason_code"] = "recorded_commit_missing"
            current["next_action"] = "inspect_batch"
        elif current["status"] == "not_started" and status in {
            "not_committed",
            "conflict",
            "unknown",
        }:
            current = result_for(
                plan,
                status,
                host.revision(state),
                reason_code=str(outcome.get("reason_code", "recorded_publication")),
                applicability="current",
                limit=limit,
            )
        current["recorded_publication"] = {"status": status}
        program.verify_refs([ref])
        return _bounded(current)
    _require(kind == "batch_preparation_failure", "Unsupported batch selection kind")
    _require(
        set(selection)
        == {
            "schema_version",
            "kind",
            "request_ref",
            "program_id",
            "base_revision",
            "failure",
            "preparation",
            "publication",
            "accepted",
        }
        and type(selection["schema_version"]) is int
        and selection["schema_version"] == 1,
        "Invalid batch preparation failure wrapper",
    )
    _require(
        selection["program_id"] == state.get("program_id") and selection["accepted"] is False,
        "Preparation failure program or acceptance differs",
    )
    _hash(selection["base_revision"], "failure original revision")
    _require(selection["preparation"] == {"status": "failed"}, "Invalid failed preparation status")
    publication = _record(selection["publication"], "failed preparation publication")
    _require(
        set(publication) == {"status"} and publication["status"] in {"not_committed", "unknown"},
        "Failed preparation cannot claim canonical commitment",
    )
    failure = _record(selection["failure"], "preparation failure")
    _require(set(failure) == {"reason", "failure_type"}, "Invalid preparation failure facts")
    _text(failure["reason"], "actual failure reason")
    _text(failure["failure_type"], "actual failure type")
    reader = _core().ReadBudget(READ_LIMIT)
    ref = _refs(program, [selection["request_ref"]], reader)[0]
    request, captured = _core().read_json_frozen(
        program.path(str(ref["path"])),
        limit=LIMIT,
        budget=reader,
    )
    _require(captured == ref["sha256"], "Failed batch request changed")
    raw_operations = request.get("operations")
    requested = len(raw_operations) if isinstance(raw_operations, list) else None
    display_operations: list[Json] = []
    if isinstance(raw_operations, list) and len(raw_operations) <= MAX_OPERATIONS:
        try:
            display_operations = _operations(raw_operations)
        except (coordination.GovernanceError, _core().ProgramError):
            # A failed schema is retained as failed, never relaxed into executable rows.
            pass
    identity = {
        key: value
        if isinstance(value, str) and value.strip() and len(value.encode()) <= 4096
        else None
        for key in ("id", "owner_id")
        for value in [request.get(key)]
    }
    result = result_for(
        {**identity, "base_revision": selection["base_revision"], "operations": []},
        str(publication["status"]),
        host.revision(state),
        reason_code="preparation_failed",
        applicability="unknown",
        limit=limit,
    )
    items: list[Json] = []
    for value in display_operations[:limit]:
        row = _record(value, "requested operation")
        task_id = _record(row["payload"], "requested payload").get("id")
        items.append(
            {
                "id": row["id"],
                "operation": row["operation"],
                "task_id": task_id
                if isinstance(task_id, str) and len(task_id.encode()) <= 4096
                else None,
                "status": "not_assessed",
            }
        )
    result["requested_operation_count"] = requested
    result["validated_operation_count"] = None
    result["committed_operation_count"] = 0 if publication["status"] == "not_committed" else None
    result["operations"] = {
        "items": items,
        "total": requested,
        "shown": len(items),
        "omitted": requested - len(items) if requested is not None else None,
    }
    result["preparation"] = {"status": "failed"}
    result["next_action"] = "prepare_batch"
    program.verify_refs([ref], reader)
    return _bounded(result)
