"""Read-only review obligations and inert inputs for existing verification commands.

Canonical task and verification transitions remain authoritative. Projection consumes
complete assessed lane facts; it never fingerprints source or contacts agents/providers.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
from types import ModuleType
from typing import Final, Protocol, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
MAX_ROWS: Final = 50
ACTION_ORDER: Final = {
    "resolve_blocker": 0,
    "recheck": 1,
    "assign": 2,
    "review": 3,
    "run": 4,
    "inspect": 5,
    "wait": 6,
    "none": 7,
}


class ProgramAccess(Protocol):
    """The existing context reader; preparation has no publication capability."""

    def require_current_location(self) -> None: ...
    def validate(self, state: Record) -> None: ...
    def current_head(self) -> str: ...


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _record(value: Json, label: str = "queue record") -> Record:
    return _core().record(value, label)


def _items(value: Json, label: str = "queue rows") -> list[Json]:
    return _core().sequence(value, label)


def _records(value: Json) -> list[Record]:
    return [_record(item) for item in _items(value)]


@dataclass(frozen=True, slots=True)
class _Context:
    """One captured registry identity and task index, reused for all derived references."""

    state: Record
    revision: str
    positions: dict[str, int]
    tasks: list[Record]
    actors: dict[str, Record]
    attempts: dict[str, Record]
    director: str | None


def _reference(context: _Context, pointer: str, task_id: str | None = None) -> Record:
    ref: Record = {
        "path": "backlog.json",
        "revision": context.revision,
        "pointer": pointer,
    }
    if task_id is not None:
        ref["task_id"] = task_id
    return ref


def _collection(rows: list[Json], reference: Record, limit: int) -> Record:
    return {
        "total": len(rows),
        "shown": min(len(rows), limit),
        "omitted": max(0, len(rows) - limit),
        "items": rows[:limit],
        "reference": reference,
    }


def _actor(state: Record, identifier: Json) -> Record:
    return next(
        (actor for actor in _records(state.get("actors", [])) if actor["id"] == identifier), {}
    )


def _attempt(task: Record) -> Record:
    attempts = _records(task["attempts"])
    return attempts[-1] if attempts else {}


def _row(context: _Context, task: Record, kind: str, identity: list[Json]) -> Record:
    attempt = context.attempts[str(task["id"])]
    owner = task.get("owner")
    director = context.director
    actor = context.actors.get(str(owner), {})
    identifier = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
    return {
        "id": identifier,
        "row_id": identifier,
        "kind": kind,
        "task_id": task["id"],
        "title": _record(task["source"])["title"],
        "attempt": attempt.get("number"),
        "request_id": None,
        "request_version": None,
        "lane": None,
        "check_ids": [],
        "owner_id": owner,
        "verifier_id": None,
        "resolver_id": None,
        "supervisor_id": actor.get("parent_id") or director,
        "next_actor_id": owner or director,
        "next_action": task["next_action"],
        "next_trigger": task["next_action"],
        "phase": task["status"],
        "result": "unknown",
        "applicability": "unknown",
        "action": "inspect",
        "readiness": {"status": "not_assessed", "reasons": []},
        "runtime": {"contact": "unknown", "liveness": "unknown", "capacity": "unknown"},
        "context": None,
        "request_ref": None,
        "observation_ref": None,
        "reference": _reference(
            context, "/tasks/" + str(context.positions[str(task["id"])]), str(task["id"])
        ),
    }


def _lane_row(context: _Context, task: Record, lane: Record) -> Record:
    row = _row(
        context,
        task,
        "verification_lane",
        ["verification_lane", task["id"], lane["request_id"], lane["request_version"]],
    )
    for key in (
        "request_id",
        "request_version",
        "lane",
        "check_ids",
        "phase",
        "result",
        "applicability",
        "context",
        "request_ref",
        "observation_ref",
        "verifier_id",
        "resolver_id",
        "next_trigger",
        "waiting",
        "input_complete",
        "environment",
        "resource",
        "evidence_destination",
        "input_summary",
        "observation_problems",
    ):
        row[key] = deepcopy(lane.get(key))
    row["reference"] = deepcopy(lane["request_reference"])
    row["attempt"] = _record(lane["context"]).get("attempt")
    reasons = list(_items(lane.get("reasons", [])))
    action, readiness = "inspect", "not_assessed"
    actor = lane["verifier_id"]
    if lane["applicability"] != "current":
        action = "recheck"
        actor = task.get("owner") or row["supervisor_id"]
    else:
        match lane["phase"]:
            case "requested":
                action = "run"
                if lane.get("input_complete") is True:
                    readiness = "ready"
                else:
                    reasons.append("input_incomplete")
            case "running":
                action, readiness = "wait", "blocked"
                reasons.append("recorded_running")
            case "blocked":
                action, readiness = "resolve_blocker", "blocked"
                actor = lane.get("resolver_id")
                reasons.append("recorded_blocker")
            case "canceled":
                action = "assign"
                actor = task.get("owner") or row["supervisor_id"]
                reasons.append("request_canceled")
            case "finished":
                match lane["result"]:
                    case "passed" | "not_applicable":
                        action, readiness = "none", "terminal"
                        actor = None
                    case "failed" | "not_run" | "pending" | "unknown":
                        action = "recheck"
                        actor = task.get("owner") or row["supervisor_id"]
                        reasons.append("verification_not_passing")
                    case invalid:
                        raise _core().ProgramError(f"Unknown queue result: {invalid}")
            case invalid:
                raise _core().ProgramError(f"Unknown queue phase: {invalid}")
    if actor is not None and context.actors.get(str(actor), {}).get("staffing_status") == "retired":
        actor, action, readiness = row["supervisor_id"], "assign", "blocked"
        reasons.append("participant_retired")
    if action != "none" and actor is None:
        reasons.append("next_actor_unrecorded")
        readiness = "not_assessed"
    provider = _record(lane.get("provider_execution") or {})
    if provider.get("adopted") is True and provider.get("status") != "passed":
        if action == "none":
            action, readiness, actor = "inspect", "not_assessed", lane["verifier_id"]
        reasons.append("provider_not_passing")
    if action == "none" and (
        lane.get("input_complete") is not True or lane.get("observation_problems")
    ):
        action, readiness, actor = "inspect", "not_assessed", lane["verifier_id"]
        reasons.append("observation_inputs_unassessed")
    row.update(
        {
            "action": action,
            "next_actor_id": actor,
            "readiness": {"status": readiness, "reasons": reasons},
        }
    )
    row["next_action"] = {
        "run": "Verify native contact and resources, then run the assigned lane",
        "wait": "Inspect actual progress; do not dispatch this recorded running lane again",
        "resolve_blocker": "Resolve the recorded blocker before restarting verification",
        "recheck": "Prepare current inputs and obtain affected verification",
        "assign": "Choose a verifier and prepare an explicit request",
        "inspect": "Inspect unavailable or incomplete verification facts",
        "none": "Retain the observation; task acceptance remains separate",
    }[action]
    return row


def _complete_lanes(projection: Record, task_id: str) -> list[Record]:
    tasks = _record(projection.get("tasks", {}))
    value = _record(tasks.get(task_id) or {})
    collection = _record(value.get("lanes") or {"items": [], "total": 0, "omitted": 0})
    rows = _records(collection["items"])
    _core().require(
        collection.get("omitted", 0) == 0 and collection.get("total", len(rows)) == len(rows),
        "Queue requires complete assessed lane facts before bounding",
    )
    return rows


def _remaining_rows(context: _Context, projection: Record) -> list[Record]:
    state = context.state
    rows: list[Record] = []
    objective = _record(state.get("objective") or {})
    members = set(_items(objective["task_ids"])) if objective else None
    task_facts = _record(projection.get("task_facts", {}))
    for task in context.tasks:
        attempt = context.attempts[str(task["id"])]
        if (
            task.get("disposition") in {"split", "retired"}
            or task["status"] == "done"
            or members is not None
            and task["id"] not in members
        ):
            continue
        lanes = _complete_lanes(projection, str(task["id"]))
        rows.extend(_lane_row(context, task, lane) for lane in lanes)
        facts = _record(task_facts.get(str(task["id"])) or {})
        submission = _record(facts.get("submission") or {})
        if task["status"] == "review":
            row = _row(
                context,
                task,
                "task_review",
                [
                    "task_review",
                    task["id"],
                    attempt.get("number"),
                    _record(attempt.get("submission") or {}).get("round"),
                ],
            )
            row["context"] = deepcopy(attempt.get("submission"))
            row["action"] = "review"
            row["next_actor_id"] = row["supervisor_id"]
            row["next_trigger"] = "Independent review of the exact current submission"
            row["applicability"] = submission.get("status", "not_assessed")
            row["readiness"] = {
                "status": "ready" if submission.get("status") == "current" else "not_assessed",
                "reasons": []
                if submission.get("status") == "current"
                else ["submission_freshness_unassessed"],
            }
            rows.append(row)
        covered = {check for lane in lanes for check in _items(lane["check_ids"])}
        if submission.get("status") == "current":
            covered.update(
                check["id"]
                for check in _records(facts.get("checks", []))
                if check["status"] == "passed"
                or check["status"] == "not_applicable"
                and check.get("policy_evidence")
            )
        for check in _items(task["required_checks"]):
            if check in covered:
                continue
            row = _row(
                context,
                task,
                "required_check_gap",
                ["required_check_gap", task["id"], attempt.get("number"), check],
            )
            row.update(
                {
                    "check_ids": [check],
                    "action": "assign",
                    "next_action": "Assess existing evidence or explicitly assign this required check",
                    "next_trigger": "Named verifier and complete declared verification inputs",
                    "readiness": {"status": "not_assessed", "reasons": ["assignment_unrecorded"]},
                    "legacy_optional": "verification" not in state,
                }
            )
            rows.append(row)
    return rows


def _recorded_use(context: _Context) -> list[Record]:
    """Retain actual running observations even when a newer request supersedes them."""
    state = context.state
    extension = _record(state.get("verification") or {})
    records = _records(extension.get("records", []))
    latest: dict[str, tuple[int, Record]] = {}
    requests: dict[str, Record] = {}
    newest: dict[tuple[str, str], str] = {}
    for position, item in enumerate(records):
        value = _record(item["input"])
        request_id = str(item["request_id"])
        match value["kind"]:
            case "request":
                requests[request_id] = item
                newest[(str(value["task_id"]), str(value["lane"]))] = request_id
                latest[request_id] = (position, item)
            case "started" | "blocked" | "finished" | "canceled":
                latest[request_id] = (position, item)
            case "finding" | "resolution":
                continue
            case invalid:
                raise _core().ProgramError(f"Unknown queue event: {invalid}")
    uses = []
    for identifier, (position, event) in latest.items():
        if _record(event["input"])["kind"] != "started":
            continue
        request = _record(requests[identifier]["input"])
        uses.append(
            {
                "request_id": identifier,
                "request_version": requests[identifier]["request_version"],
                "task_id": request["task_id"],
                "verifier_id": request["verifier_id"],
                "resource": deepcopy(request.get("resource")),
                "superseded": newest[(str(request["task_id"]), str(request["lane"]))] != identifier,
                "observation_ref": deepcopy(event["receipt"]),
                "request_ref": deepcopy(requests[identifier]["receipt"]),
                "reference": _reference(
                    context,
                    "/verification/records/" + str(position),
                    str(request["task_id"]),
                ),
                "actual_occupancy": "unknown",
            }
        )
    return uses


def _workload(context: _Context, rows: list[Record], limit: int) -> Record:
    uses = _recorded_use(context)
    diagnostics: list[Json] = []
    groups: dict[str, list[Record]] = defaultdict(list)
    resources: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for use in uses:
        groups[str(use["verifier_id"])].append(use)
        resource = _record(use.get("resource") or {})
        if resource:
            resources[(str(resource["name"]), str(resource["boundary"]))].append(use)
        if use["superseded"]:
            diagnostics.append({"kind": "superseded_running", **deepcopy(use)})
    for actor, entries in sorted(groups.items()):
        if len(entries) > 1:
            diagnostics.append(
                {
                    "kind": "actor_overlap",
                    "actor_id": actor,
                    "requests": deepcopy(entries),
                    "capacity": "unknown",
                }
            )
    for row in rows:
        resource = _record(row.get("resource") or {})
        if not resource:
            continue
        entries = [
            use
            for use in resources.get((str(resource["name"]), str(resource["boundary"])), [])
            if use["request_id"] != row["request_id"]
        ]
        if entries:
            diagnostics.append(
                {
                    "kind": "resource_overlap",
                    "row_id": row["id"],
                    "resource": deepcopy(resource),
                    "requests": deepcopy(entries),
                    "actual_occupancy": "unknown",
                    "reference": deepcopy(row["reference"]),
                }
            )
            if row["action"] == "run":
                row["action"], row["next_action"] = (
                    "inspect",
                    "Check actual resource release before dispatch",
                )
                row["readiness"] = {
                    "status": "not_assessed",
                    "reasons": ["recorded_resource_use_unresolved"],
                }
    counts = dict(Counter(str(_record(item)["kind"]) for item in diagnostics))
    return {
        **_collection(diagnostics, _reference(context, "/verification/records"), limit),
        "counts": counts,
        "runtime_capacity": "unknown",
        "resource_boundary_assessment": "exact_declared_identity_only",
    }


class _TaskLookup(Protocol):
    """The existing task index capability used by detached allocation packets."""

    def task(self, identifier: str) -> Record: ...
    def conflicts(self, identifier: str) -> list[Record]: ...


@dataclass(frozen=True, slots=True)
class _AllocationContext:
    """Indexes captured once for packets from the exact same immutable state object."""

    state: Record
    tasks: _TaskLookup
    actors: dict[str, Record]
    requests: dict[str, dict[str, Record]]
    events: dict[str, Record]
    director: str | None


def _allocation_context(state: Record) -> _AllocationContext:
    """Classify complete request history and task/actor responsibility once per capture."""
    task_index = _core()._TaskIndex.capture(_records(state["tasks"]))
    actors = {str(actor["id"]): actor for actor in reversed(_records(state.get("actors", [])))}
    latest_requests: dict[str, dict[str, Record]] = {}
    latest_events: dict[str, Record] = {}
    extension = _record(state.get("verification") or {})
    for item in _records(extension.get("records", [])):
        value = _record(item["input"])
        if value["kind"] == "request":
            latest_requests.setdefault(str(value["task_id"]), {})[str(value["lane"])] = item
        if value["kind"] in {"request", "started", "blocked", "finished", "canceled"}:
            latest_events[str(item["request_id"])] = item
    return _AllocationContext(
        state,
        task_index,
        actors,
        latest_requests,
        latest_events,
        _core().coordination.current_director_id(state),
    )


def allocation_packet(state: Record, task_id: str, context: Record | None = None) -> Record:
    """Assemble recorded allocation context; authored wording never becomes peer approval."""
    return _allocation_packet_from_context(state, task_id, context, _allocation_context(state))


def _allocation_packet_from_context(
    state: Record, task_id: str, context: Record | None, captured: _AllocationContext
) -> Record:
    """Reuse only the host's exact state-local capture, never a later registry image."""
    _core().require(captured.state is state, "Allocation context belongs to another state image")
    task_index = captured.tasks
    task = task_index.task(task_id)
    attempt = _attempt(task)
    actor = captured.actors.get(str(task.get("owner")), {})
    responsibilities: list[Json] = []
    for lane_name, item in sorted(captured.requests.get(task_id, {}).items()):
        value = _record(item["input"])
        event = _record(captured.events[str(item["request_id"])]["input"])
        blocker = _record(event.get("blocker") or {})
        responsibilities.append(
            {
                "lane": lane_name,
                "request_id": item["request_id"],
                "request_version": item["request_version"],
                "verifier_id": value["verifier_id"],
                "resolver_id": blocker.get("resolver_id"),
                "check_ids": deepcopy(value["check_ids"]),
                "input_scope": deepcopy(value["input_scope"]),
                "resource": deepcopy(value.get("resource")),
                "evidence_destination": value["evidence_destination"],
                "next_trigger": blocker.get("next_trigger", value["next_trigger"]),
                "assessment": "recorded_only",
                "request_ref": deepcopy(item["receipt"]),
            }
        )
    return {
        "task_id": task_id,
        "expected_result": task.get("expected_result") or _record(task["source"])["text"],
        "original_acceptance": deepcopy(task["source"]),
        "worker_id": task.get("owner"),
        "supervisor_id": actor.get("parent_id") or captured.director,
        "owned_write_scope": deepcopy(attempt.get("scope", task["candidate_paths"])),
        "required_checks": deepcopy(task["required_checks"]),
        "depends_on": deepcopy(task["depends_on"]),
        "readiness": {
            "unmet_dependencies": [
                identifier
                for identifier in _items(task["depends_on"])
                if task_index.task(str(identifier))["status"] != "done"
            ],
            "scope_conflicts": task_index.conflicts(task_id),
            "claim": "not_assessed",
        },
        "related_to": deepcopy(task["related_to"]),
        "coordination_notes": deepcopy(task["notes"]),
        "claim_assignment": deepcopy(attempt.get("assignment")),
        "verification_responsibilities": responsibilities,
        "missing_fields": ["native_contact", "runtime_capacity", "peer_agreement"],
        "runtime_observation": {
            key: deepcopy(actor.get(key)) for key in ("observed_status", "observed_at")
        },
        "native_contact": "unknown",
        "runtime_capacity": "unknown",
        "peer_agreement": {"status": "unknown", "basis": "authored_evidence_only"},
        "source_context": deepcopy(context),
        "next_trigger": task["next_action"],
        "final_acceptance_owner": captured.director,
    }


def _capture_context(state: Record) -> _Context:
    tasks = _records(state["tasks"])
    return _Context(
        state,
        _core().phases.revision(state),
        {str(task["id"]): index for index, task in enumerate(tasks)},
        tasks,
        {str(actor["id"]): actor for actor in reversed(_records(state.get("actors", [])))},
        {str(task["id"]): _attempt(task) for task in tasks},
        _core().coordination.current_director_id(state),
    )


def project(
    state: Record,
    full_verification_projection: Record,
    *,
    limit: int = MAX_ROWS,
    handoff_facts: Record | None = None,
    integrated_review: Record | None = None,
) -> Record:
    """Count the complete obligation set, then bound one stable shared queue projection."""
    _core().require(type(limit) is int and 0 <= limit <= MAX_ROWS, "Invalid queue row limit")
    context = _capture_context(state)
    tasks = context.tasks
    rows = _remaining_rows(context, full_verification_projection)
    integrated = (
        integrated_review
        if integrated_review is not None
        else full_verification_projection.get("integrated_review")
    )
    if integrated is not None:
        for obligation in _records(_record(integrated).get("obligations", [])):
            if obligation.get("actionable") is not True:
                continue
            task = next((task for task in tasks if task["id"] == obligation["task_id"]), None)
            _core().require(task is not None, "Unknown integrated review obligation task")
            row = _row(
                context,
                task,
                "integrated_review_obligation",
                [
                    "integrated_review_obligation",
                    obligation["task_id"],
                    obligation.get("attempt"),
                    obligation.get("check_id"),
                    obligation.get("criterion"),
                    obligation.get("request_id"),
                    obligation.get("observation_id"),
                ],
            )
            row.update(
                {
                    "next_actor_id": obligation.get("next_actor_id"),
                    "next_action": obligation["next_action"],
                    "next_trigger": obligation["next_trigger"],
                    "action": "resolve_blocker"
                    if obligation["status"] in {"unavailable", "adverse"}
                    else "recheck",
                    "readiness": {
                        "status": "blocked",
                        "reasons": deepcopy(obligation.get("reason_codes", [])),
                    },
                    "integrated_review_obligation": deepcopy(obligation),
                }
            )
            rows.append(row)
    if handoff_facts is not None:
        _full_rows(handoff_facts)
        latest: dict[str, Record] = {}
        for item in _records(_record(handoff_facts["rows"])["items"]):
            identifier = str(item["task_id"])
            if identifier not in latest or item.get("recorded_sequence", 0) > latest[
                identifier
            ].get("recorded_sequence", 0):
                latest[identifier] = item
        for row in rows:
            handoff = latest.get(str(row["task_id"]))
            if row["kind"] != "task_review" or handoff is None:
                continue
            row["review_handoff"] = deepcopy(handoff)
            if handoff.get("currentness") == "current":
                row["next_actor_id"] = handoff["next_actor_id"]
                row["next_action"] = handoff["next_action"]
                row["next_trigger"] = handoff["next_trigger"]
    workload = _workload(context, rows, limit)
    priorities = _record(state.get("execution_priorities", {}))
    rows.sort(
        key=lambda row: (
            ACTION_ORDER[str(row["action"])],
            {"unavailable": 0, "adverse": 1, "pending": 2, "requires_recheck": 3}.get(
                _record(row.get("integrated_review_obligation", {})).get("status"), 4
            ),
            priorities.get(str(row["task_id"]), 0),
            str(row["task_id"]),
            str(row["kind"]),
            str(row["lane"]),
            str(row["id"]),
        )
    )
    counts: Record = {
        "kind": dict(Counter(str(row["kind"]) for row in rows)),
        "action": dict(Counter(str(row["action"]) for row in rows)),
        "readiness": dict(Counter(str(_record(row["readiness"])["status"]) for row in rows)),
    }
    return {
        "schema_version": 1,
        "queue_version": 1,
        "status": "recorded",
        "observed_revision": context.revision,
        "rows": _collection(rows, _reference(context, "/tasks"), limit),
        "counts": counts,
        "recorded_workload": workload,
        "acceptance_complete": False,
        "runtime_capacity": "unknown",
        "authority": "advisory_only",
    }


def prepare_assignment(program: ProgramAccess, state: Record, request: Record) -> Record:
    """Prepare an explicit request draft; ordinary verification-record owns publication."""
    core = _core()
    program.require_current_location()
    program.validate(state)
    task_id = core.string(request.get("task_id"), "assignment task")
    task = next((task for task in _records(state["tasks"]) if task["id"] == task_id), None)
    core.require(task is not None, "Unknown assignment task")
    allowed = {
        "schema_version",
        "task_id",
        "actor_id",
        "id",
        "reason",
        "lane",
        "verifier_id",
        "check_ids",
        "input_scope",
        "input_complete",
        "evidence_destination",
        "next_trigger",
        "criteria",
        "environment",
        "resource",
        "context_path",
        "provider_context_path",
        "provider_source_mapping_path",
        "expected_revision",
        "supersedes",
    }
    core.require(set(request) <= allowed, "Unknown assignment input fields")
    revision = core.phases.revision(state)
    core.require(
        request.get("expected_revision", revision) == revision, "Stale assignment revision"
    )
    core.require(request.get("schema_version", 1) == 1, "Invalid assignment schema")
    core.require(
        task.get("disposition") not in {"split", "retired"} and task["status"] != "done",
        "Terminal task cannot receive a new queue assignment",
    )
    objective = _record(state.get("objective") or {})
    core.require(
        not objective or task_id in _items(objective["task_ids"]),
        "Task excluded from current objective",
    )
    actor = request.get("actor_id")
    if actor is not None:
        core.coordination.require_task_authority(
            state, core.string(actor, "assignment actor"), task.get("owner")
        )
    verifier = request.get("verifier_id")
    core.require(
        _actor(state, verifier).get("staffing_status") != "retired",
        "Retired verification participant",
    )
    checks = core.strings(request.get("check_ids", []), "assignment checks")
    core.require(
        set(checks) <= set(_items(task["required_checks"])),
        "Request check outside current obligations",
    )
    lane = request.get("lane")
    extension = _record(state.get("verification") or {})
    previous = [
        entry
        for entry in _records(extension.get("records", []))
        if _record(entry["input"])["kind"] == "request"
        and _record(entry["input"])["task_id"] == task_id
        and _record(entry["input"])["lane"] == lane
    ]
    supersedes = previous[-1]["request_id"] if previous else None
    core.require(
        request.get("supersedes", supersedes) == supersedes,
        "Supersedes must identify exact latest lane request",
    )
    receipt = deepcopy(request)
    receipt.update(
        {
            "schema_version": 1,
            "operation": "verification-record",
            "kind": "request",
            "expected_revision": revision,
        }
    )
    if supersedes is not None:
        receipt["supersedes"] = supersedes
    required = (
        "id",
        "actor_id",
        "reason",
        "lane",
        "verifier_id",
        "check_ids",
        "input_scope",
        "input_complete",
        "evidence_destination",
        "next_trigger",
    )
    missing = [key for key in required if receipt.get(key) is None or receipt.get(key) in ("", [])]
    if not missing:
        core.verification._input_shape(receipt)
    if receipt.get("context_path") is None:
        missing.append("context_path")
    packet = core.verification.packet(program, state, task_id, observed_head=program.current_head())
    context = _record(packet["context"])
    return {
        "schema_version": 1,
        "status": "not_assessed",
        "advisory": True,
        "acceptance_complete": False,
        "observed_revision": revision,
        "receipt": receipt,
        "missing_fields": missing,
        "context": context,
        "allocation": allocation_packet(state, task_id, context),
        "related_inputs": {"context_packet": packet},
        "limitations": [
            "Draft only: actual preview and verification-record recheck authority, "
            "revision, source, evidence, resource use and request lineage. "
            "Native contact, peer agreement and runtime capacity require actual evidence."
        ],
    }


def _handoffs() -> ModuleType:
    if __package__:
        from tools import feature_program_review_handoffs
    else:
        import feature_program_review_handoffs
    return feature_program_review_handoffs


def _full_rows(facts: Record) -> list[Record]:
    rows = _record(facts.get("rows"))
    items = _records(rows.get("items", []))
    _core().require(
        rows.get("omitted") == 0 and rows.get("total") == rows.get("shown") == len(items),
        "Complete assessed handoff facts required",
    )
    return items


def prepare_handoff_action(
    state: Record, request: Record, *, assessed_packet: Record, runtime_contact: Record | None
) -> Record:
    """Describe one exact current assignee action; never perform a native call."""
    core = _core()
    kind = request.get("kind")
    core.require(
        kind in {"worker_assignment", "independent_review"}, "Invalid assignment action kind"
    )
    core.require(set(request) <= {"kind", "task_id", "packet_ref"}, "Unknown handoff action fields")
    task_id = core.string(request.get("task_id"), "handoff action task")
    task = next((item for item in _records(state["tasks"]) if item["id"] == task_id), None)
    core.require(task is not None, "Unknown handoff action task")
    context = _record(assessed_packet.get("context"))
    attempt = _attempt(task)
    core.require(
        context.get("task_id") == task_id
        and context.get("program_id") == state["program_id"]
        and context.get("attempt") == attempt.get("number"),
        "Action context task/attempt changed",
    )
    core.require(
        context.get("definition_digest") == core.digest(core.planning.task_definition(task)),
        "Action definition changed",
    )
    if kind == "independent_review":
        packet = _handoffs()._ref(assessed_packet.get("packet_ref"))
    else:
        packet = deepcopy(_record(assessed_packet.get("packet_ref")))
        core.require(set(packet) == {"path", "sha256"}, "Exact worker packet reference required")
        name = core.safe_relative(core.string(packet["path"], "worker packet path"))
        core.require(
            name == "tasks/" + task_id + ".md", "Worker packet is not the current task packet"
        )
        _handoffs()._hash(packet["sha256"], "worker packet hash")
    core.require(request.get("packet_ref", packet) == packet, "Action packet changed")
    reasons = []
    identifier, generation = None, None
    subject = deepcopy(context)
    recipient = task.get("owner")
    if kind == "independent_review":
        intents = [
            item
            for item in _handoffs()._validated_history(state).records
            if item["kind"] == "intent" and item["subject"]["task_id"] == task_id
        ]
        core.require(bool(intents), "Current review intent required")
        intent = intents[-1]
        subject = _handoffs()._subject(
            state, task_id, context, assessed_packet.get("review_inputs", {})
        )
        core.require(
            subject == intent["subject"]
            and packet == intent["packet_ref"]
            and task["status"] == "review",
            "Action review subject is stale",
        )
        identifier, generation, recipient = (
            intent["handoff_id"],
            intent["assignment_generation"],
            intent["reviewer_id"],
        )
        eligible = _handoffs()._eligibility(state, task, recipient, intent["eligibility"])
        if eligible["status"] != "eligible":
            reasons.append("reviewer_ineligible")
        if any(
            row["handoff_id"] == identifier and row["occupied"]
            for row in _handoffs().dispatch_processes(state)
        ):
            reasons.append("prior_dispatch_unresolved")
    else:
        core.require(
            task["status"] == "active" and bool(recipient) and bool(attempt),
            "Worker action requires current claimed owner",
        )
        core.require(
            context.get("required_checks") == task["required_checks"], "Action obligations changed"
        )
    if kind == "independent_review":
        ceiling = _actor(state, recipient).get("review_wip_limit")
        if ceiling is not None:
            core.require(
                type(ceiling) is int and ceiling >= 0, "Invalid recorded review WIP policy"
            )
            occupied = sum(
                row["reviewer_id"] == recipient and row["occupied"]
                for row in _handoffs().dispatch_processes(state)
            )
            occupied += sum(
                use["verifier_id"] == recipient for use in _recorded_use(_capture_context(state))
            )
            if occupied >= ceiling:
                reasons.append("wip_limit_reached")
    contact = runtime_contact or {}
    verified = (
        contact.get("actor_id") == recipient
        and contact.get("recipient_status") in {"running", "idle", "completed"}
        and bool(contact.get("contact"))
        and bool(contact.get("evidence_refs"))
        and bool(contact.get("observed_at"))
        and bool(contact.get("clock_id"))
    )
    if verified:
        details = {
            **deepcopy(contact),
            "dispatch_attempt_id": "contact-validation",
            "transport": "send_message"
            if contact["recipient_status"] == "running"
            else "followup_task",
        }
        details["evidence_refs"] = _handoffs()._refs(contact["evidence_refs"])
        details["timestamp_source"] = "native_tool_observation"
        details["boundary"] = "runtime_contact_observed"
        _handoffs()._milestone(details, "dispatch_claimed")
    else:
        reasons.append("contact_unverified")
    actor = core.coordination.current_director_id(state) or "leader"
    identity = {
        "assignment_kind": kind,
        "task_id": task_id,
        "subject": subject,
        "packet_ref": packet,
        "recipient_actor_id": recipient,
        "handoff_id": identifier,
        "assignment_generation": generation,
    }
    action = (
        "reconcile_delivery"
        if "prior_dispatch_unresolved" in reasons
        else "choose_reviewer"
        if "reviewer_ineligible" in reasons
        else "await_capacity"
        if "wip_limit_reached" in reasons
        else "observe_runtime"
        if reasons
        else "dispatch_exact"
    )
    return {
        "schema_version": 1,
        "kind": "handoff_action",
        **identity,
        "action_id": "ha-" + core.digest(identity),
        "actor_id": actor,
        "status": "incomplete" if reasons else "ready",
        "action_code": action,
        "reason_codes": reasons,
        "transport": (
            "send_message" if contact["recipient_status"] == "running" else "followup_task"
        )
        if verified
        else None,
        "contact": contact.get("contact") if verified else None,
        "recipient_status": contact.get("recipient_status") if verified else None,
        "runtime_evidence_refs": deepcopy(contact.get("evidence_refs", [])) if verified else [],
        "acceptance_complete": False,
    }


def project_capacity(
    state: Record,
    full_handoff_facts: Record,
    full_verification_facts: Record | None = None,
    *,
    policy: Record | None = None,
    runtime_facts: Record | None = None,
    limit: int | None = 50,
) -> Record:
    """Count the full recorded process set; policy headroom is never live capacity."""
    core = _core()
    core.require(
        limit is None or type(limit) is int and 0 <= limit <= MAX_ROWS, "Invalid capacity row limit"
    )
    facts = _full_rows(full_handoff_facts)
    revision = core.phases.revision(state)
    core.require(
        _record(full_handoff_facts.get("source_binding")).get("revision") == revision,
        "Capacity handoff binding changed",
    )
    actors = {str(actor["id"]): actor for actor in _records(state.get("actors", []))}
    reviewer_ids = {str(row["reviewer_id"]) for row in facts if row.get("reviewer_id") is not None}
    reviewer_ids.update(key for key, actor in actors.items() if actor.get("role") == "reviewer")
    if full_verification_facts is not None:
        for value in _record(full_verification_facts.get("tasks", {})).values():
            lanes = _record(_record(value).get("lanes", {}))
            items = _records(lanes.get("items", []))
            core.require(
                lanes.get("omitted", 0) == 0 and lanes.get("total", len(items)) == len(items),
                "Complete verification workload facts required",
            )
            reviewer_ids.update(
                str(item["verifier_id"]) for item in items if item.get("verifier_id") is not None
            )
    verification_uses = _recorded_use(_capture_context(state))
    reviewer_ids.update(str(use["verifier_id"]) for use in verification_uses)
    declared = {key: actor.get("review_wip_limit") for key, actor in actors.items()}
    core.require(
        policy is None or policy == declared,
        "Capacity policy must match recorded actor declarations",
    )
    runtime = runtime_facts or {}
    observations = _record(runtime.get("actors", {}))
    complete = (
        runtime.get("complete") is True
        and runtime.get("observed_revision") == revision
        and bool(reviewer_ids)
    )
    for key in reviewer_ids:
        observation = _record(observations.get(key, {}))
        slots = observation.get("available_slots")
        valid = (
            observation.get("complete") is True
            and observation.get("status") in {"running", "idle", "completed", "unavailable"}
            and type(slots) is int
            and slots >= 0
            and bool(observation.get("evidence_refs"))
            and bool(observation.get("observed_at"))
            and bool(observation.get("clock_id"))
        )
        complete = complete and valid
        if valid:
            _handoffs()._refs(observation["evidence_refs"])
            _handoffs()._milestone(
                {
                    **observation,
                    "timestamp_source": "native_tool_observation",
                    "boundary": "runtime_capacity_observed",
                },
                "submitted",
            )
    rows = []
    totals = Counter(
        {
            key: 0
            for key in (
                "recorded_occupied",
                "pending_dispatch",
                "sent_unstarted",
                "started",
                "ambiguous",
                "superseded_nonterminal",
                "native_occupied",
                "verification_occupied",
            )
        }
    )
    for key in sorted(reviewer_ids):
        actor = actors.get(key, {})
        processes = [
            (row, process)
            for row in facts
            if row.get("reviewer_id") == key
            for process in _records(row.get("dispatches", []))
        ]
        counts = {
            "recorded_occupied": sum(bool(process["occupied"]) for _, process in processes),
            "pending_dispatch": sum(process["status"] == "claimed" for _, process in processes),
            "sent_unstarted": sum(
                process["status"] in {"sent", "acknowledged"} for _, process in processes
            ),
            "started": sum(process["status"] == "started" for _, process in processes),
            "ambiguous": sum(bool(process["ambiguous"]) for _, process in processes),
            "superseded_nonterminal": sum(
                row["currentness"] != "current" and process["occupied"]
                for row, process in processes
            ),
        }
        verification_occupied = sum(use["verifier_id"] == key for use in verification_uses)
        counts["native_occupied"] = counts["recorded_occupied"]
        counts["verification_occupied"] = verification_occupied
        counts["recorded_occupied"] += verification_occupied
        counts["superseded_nonterminal"] += sum(
            use["verifier_id"] == key and use["superseded"] for use in verification_uses
        )
        totals.update(counts)
        ceiling = actor.get("review_wip_limit")
        core.require(
            ceiling is None or type(ceiling) is int and ceiling >= 0,
            "Invalid recorded review WIP policy",
        )
        headroom = None if ceiling is None else max(0, ceiling - counts["recorded_occupied"])
        observed = _record(observations.get(key, {}))
        reasons = ([] if complete else ["runtime_capacity_unknown"]) + (
            ["policy_missing"]
            if ceiling is None
            else ["wip_limit_reached"]
            if headroom == 0
            else []
        )
        runtime_blocked = complete and (
            observed["status"] == "unavailable" or observed["available_slots"] == 0
        )
        if runtime_blocked:
            reasons.append("runtime_capacity_unavailable")
        rows.append(
            {
                "actor_id": key,
                **counts,
                "policy_limit": ceiling,
                "policy_headroom": headroom,
                "available_slots": observed.get("available_slots") if complete else None,
                "runtime_status": observed["status"] if complete else "unknown",
                "runtime_observation": {
                    "observed_at": observed["observed_at"],
                    "clock_id": observed["clock_id"],
                    "evidence_refs": deepcopy(observed["evidence_refs"]),
                    "observed_revision": revision,
                    "complete": True,
                }
                if complete
                else None,
                "next_action_code": "await_capacity"
                if headroom == 0 or runtime_blocked
                else "observe_runtime"
                if not complete
                else "dispatch_exact",
                "reason_codes": reasons,
                "reference": {"path": "backlog.json", "pointer": "/actors", "revision": revision},
            }
        )
    bound = len(rows) if limit is None else limit
    return {
        "schema_version": 1,
        "status": "recorded",
        "source_binding": {"revision": revision},
        "rows": {
            "total": len(rows),
            "shown": min(bound, len(rows)),
            "omitted": max(0, len(rows) - bound),
            "items": rows[:bound],
            "reference": {"path": "backlog.json", "pointer": "/actors", "revision": revision},
        },
        "counts": dict(totals),
        "runtime_capacity": "observed" if complete else "unknown",
        "authority": "advisory_only",
    }


def prepare_reviewer_allocation(
    state: Record,
    full_handoffs: Record,
    full_verification: Record,
    *,
    policy: Record | None = None,
    runtime_facts: Record | None = None,
    max_actions: int = 50,
) -> Record:
    """Recommend deterministically from the full set, without assigning or launching."""
    core = _core()
    core.require(
        type(max_actions) is int and 0 <= max_actions <= MAX_ROWS, "Invalid allocation action limit"
    )
    capacity = project_capacity(
        state,
        full_handoffs,
        full_verification,
        policy=policy,
        runtime_facts=runtime_facts,
        limit=None,
    )
    # Full actors are required internally even when the public capacity list is bounded.
    loads = {str(row["actor_id"]): deepcopy(row) for row in _records(capacity["rows"]["items"])}
    actions = []
    latest = {}
    for row in _full_rows(full_handoffs):
        key = str(row["task_id"])
        if key not in latest or row["recorded_sequence"] > latest[key]["recorded_sequence"]:
            latest[key] = row
    for task in _records(state["tasks"]):
        key = str(task["id"])
        if task["status"] == "review" and (
            key not in latest or latest[key].get("canonical_terminal")
        ):
            latest[key] = {
                "handoff_id": None,
                "assignment_generation": None,
                "currentness": "unavailable",
                "canonical_terminal": False,
            }
    for key in sorted(
        latest, key=lambda key: (_record(state.get("execution_priorities", {})).get(key, 0), key)
    ):
        row = latest[key]
        if row.get("canonical_terminal"):
            continue
        if row.get("currentness") != "current":
            actions.append(
                {
                    "task_id": key,
                    "handoff_id": row["handoff_id"],
                    "assignment_generation": row["assignment_generation"],
                    "reviewer_id": None,
                    "action_code": "revalidate_subject",
                    "reason_codes": ["subject_stale"],
                    "requires_explicit_assignment": True,
                }
            )
            continue
        task = next(task for task in _records(state["tasks"]) if task["id"] == key)
        options = []
        for actor_id, load in loads.items():
            if (
                _handoffs()._eligibility(state, task, actor_id, {"status": "eligible"})["status"]
                != "eligible"
            ):
                continue
            if (
                load["policy_headroom"] == 0
                or load["available_slots"] == 0
                or load["runtime_status"] == "unavailable"
            ):
                continue
            options.append(actor_id)
        options.sort(key=lambda actor_id: (loads[actor_id]["recorded_occupied"], actor_id))
        occupied = any(
            process["occupied"]
            for prior in _full_rows(full_handoffs)
            if prior["handoff_id"] == row["handoff_id"]
            for process in _records(prior.get("dispatches", []))
        )
        chosen = options[0] if options and not occupied else None
        action = (
            "reconcile_delivery" if occupied else "choose_reviewer" if chosen else "await_capacity"
        )
        if chosen:
            loads[chosen]["recorded_occupied"] += 1
            if loads[chosen]["policy_headroom"] is not None:
                loads[chosen]["policy_headroom"] -= 1
            if loads[chosen]["available_slots"] is not None:
                loads[chosen]["available_slots"] -= 1
        actions.append(
            {
                "task_id": key,
                "handoff_id": row["handoff_id"],
                "assignment_generation": row["assignment_generation"],
                "reviewer_id": chosen,
                "action_code": action,
                "reason_codes": ["prior_dispatch_unresolved"]
                if occupied
                else ["runtime_capacity_unknown"]
                if capacity["runtime_capacity"] == "unknown"
                else [],
                "requires_explicit_assignment": True,
            }
        )
    return {
        "schema_version": 1,
        "kind": "reviewer_allocation",
        "source_binding": deepcopy(capacity["source_binding"]),
        "rows": {
            "total": len(actions),
            "shown": min(max_actions, len(actions)),
            "omitted": max(0, len(actions) - max_actions),
            "items": actions[:max_actions],
        },
        "runtime_capacity": capacity["runtime_capacity"],
        "authority": "advisory_only",
        "acceptance_complete": False,
    }


def guide_handoffs(
    state: Record,
    full_handoffs: Record,
    full_verification: Record,
    *,
    reviewer_load: Record | None = None,
) -> Record:
    """Join full assessed next-action facts before any task/history/queue bounds.

    Readiness is a retained milestone, never permission to send. This ordinary
    snapshot has no exact runtime-contact action; the director must observe it.
    """
    core = _core()
    rows = _full_rows(full_handoffs)
    revision = core.phases.revision(state)
    core.require(
        _record(full_handoffs.get("source_binding")).get("revision") == revision,
        "Handoff guidance source binding changed",
    )
    capacity = (
        reviewer_load
        if reviewer_load is not None
        else project_capacity(state, full_handoffs, full_verification, limit=None)
    )
    core.require(
        _record(capacity.get("source_binding")).get("revision") == revision,
        "Handoff guidance capacity binding changed",
    )
    reviewers = {str(row["actor_id"]): row for row in _full_rows(capacity)}
    occupied = {
        str(row["handoff_id"])
        for row in rows
        if any(process["occupied"] for process in _records(row.get("dispatches", [])))
    }
    result = deepcopy(full_handoffs)
    for row in _records(_record(result["rows"])["items"]):
        if row.get("phase") != "ready" or row.get("currentness") != "current":
            continue
        reviewer = reviewers.get(str(row.get("reviewer_id")), {})
        reasons = list(_items(row.get("reasons", [])))
        if str(row["handoff_id"]) in occupied:
            action = "Reconcile actual native delivery before any resend"
            trigger = "Exact prior dispatch outcome or process terminal is observed"
            reasons.append("prior_dispatch_unresolved")
        elif (
            reviewer.get("policy_headroom") == 0
            or reviewer.get("available_slots") == 0
            or reviewer.get("runtime_status") == "unavailable"
        ):
            action = "Wait for recorded reviewer capacity before preparing dispatch"
            trigger = "Recorded workload or explicit runtime capacity changes"
            reasons.append(
                "wip_limit_reached"
                if reviewer.get("policy_headroom") == 0
                else "runtime_capacity_unavailable"
            )
        else:
            action = "Observe actual native contact before preparing the exact handoff action"
            trigger = "Exact recipient contact, status and evidence are observed"
            reasons.append("contact_unverified")
            if capacity.get("runtime_capacity") == "unknown":
                reasons.append("runtime_capacity_unknown")
        row.update(
            {
                "next_actor_id": core.coordination.current_director_id(state) or "leader",
                "next_action": action,
                "next_trigger": trigger,
                "reasons": list(dict.fromkeys(reasons)),
            }
        )
    core.require(
        len(core.canonical(result)) <= 8 * 1024 * 1024,
        "Guided handoff projection exceeds output budget",
    )
    return result
