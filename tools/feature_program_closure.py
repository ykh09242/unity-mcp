"""Bounded closure assembly and record-only recovery using ordinary program transitions.

The host owns authoritative preparation, fresh publication and supported render recovery.
Plans and attachments are inert immutable evidence, never commands or an approval store.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from types import ModuleType
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from tools.feature_program import PreparedChange, Program

if __package__:
    from tools import feature_program_coordination as coordination
else:
    import feature_program_coordination as coordination

Json = coordination.Json
Record = coordination.Record
LIMIT: Final = 8 * 1024 * 1024
READ_LIMIT: Final = 64 * 1024 * 1024
REFERENCE_LIMIT: Final = 2000
OPERATIONS: Final = frozenset(
    {
        "verification-record",
        "submit",
        "complete",
        "rework",
        "delivery",
        "objective-complete",
        "note",
        "checkpoint",
        "resume",
    }
)


class ClosureError(coordination.GovernanceError):
    """A closure input, immutable binding or publication outcome is unusable."""


class ClosureHost(Protocol):
    """Narrow normal-transition capabilities; no subprocess or provider transport."""

    def revision(self, state: Record) -> str: ...
    def identity(self) -> Record: ...
    def prepare(
        self,
        state: Record,
        operation: str,
        payload: Record,
        observed_at: str,
        *,
        owner: str | None = None,
    ) -> PreparedChange: ...
    def preflight(self, prepared: PreparedChange) -> None: ...
    def inspect_stage(self, state: Record, stage: Record) -> Record: ...
    def publish(self, stage: Record, expected_revision: str, owner: str) -> Record: ...
    def load(self) -> Record: ...


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ClosureError(message)


def _record(value: Json, name: str) -> Record:
    match value:
        case dict():
            return value
        case _:
            raise ClosureError(f"{name} must be an object")


def _items(value: Json, name: str) -> list[Json]:
    match value:
        case list():
            return value
        case _:
            raise ClosureError(f"{name} must be an array")


def _text(value: Json, name: str) -> str:
    match value:
        case str() if value.strip():
            return value
        case _:
            raise ClosureError(f"{name} must be nonempty text")


def _bounded(value: Record) -> Record:
    _require(len(_core().canonical(value)) <= LIMIT, "Closure output exceeds 8 MiB")
    return value


def _reference(value: Json) -> Record:
    ref = _record(value, "evidence reference")
    _require(set(ref) == {"path", "sha256"}, "Evidence reference requires exact path/hash")
    _core().safe_relative(_text(ref.get("path"), "reference path"))
    _require(
        bool(re.fullmatch(r"[a-f0-9]{64}", _text(ref.get("sha256"), "reference hash"))),
        "Invalid evidence hash",
    )
    return deepcopy(ref)


def _read_ref(program: Program, value: Json, remaining: int = READ_LIMIT) -> tuple[Record, bytes]:
    ref = _reference(value)
    raw = _core().bounded_bytes(program.path(str(ref["path"])), min(LIMIT, remaining))
    _require(_core().sha(raw) == ref["sha256"], "Immutable evidence changed")
    return ref, raw


def _exact_write(path: Path, raw: bytes) -> None:
    """Materialize inert evidence without overwriting an existing immutable file."""
    if path.exists():
        _require(
            path.is_file() and _core().bounded_bytes(path, LIMIT) == raw,
            "Attachment destination conflicts",
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        _require(
            path.is_file() and _core().bounded_bytes(path, LIMIT) == raw,
            "Attachment destination conflicts",
        )


def prepare_attachments(program: Program, request: Record, *, materialize: bool = False) -> Record:
    """Precheck the entire exact-byte copy set; preserve task/attempt prefix guards."""
    core = _core()
    rows = _items(request.get("attachments", []), "attachments")
    _require(len(rows) <= REFERENCE_LIMIT, "Too many attachments")
    checked: list[tuple[Path, bytes]] = []
    output: list[Json] = []
    seen: set[str] = set()
    remaining = READ_LIMIT
    for value in rows:
        row = _record(value, "attachment")
        _require(
            set(row) == {"origin_ref", "destination", "task_id", "attempt"},
            "Attachment requires origin, destination, task and attempt",
        )
        task_id = _text(row.get("task_id"), "attachment task")
        _require(
            core.portable_task_id(task_id) or task_id == "_coordination", "Invalid attachment task"
        )
        number = row.get("attempt")
        _require(type(number) is int and number > 0, "Invalid attachment attempt")
        destination = core.safe_relative(_text(row.get("destination"), "attachment destination"))
        _require(
            destination.startswith(f"evidence/{task_id}/attempt-{number}/"),
            "Attachment must bind individual task attempt",
        )
        _require(destination.casefold() not in seen, "Duplicate attachment destination")
        seen.add(destination.casefold())
        origin, raw = _read_ref(program, row.get("origin_ref"), remaining)
        remaining -= len(raw)
        path = program.path(destination)
        _require(
            not path.exists() or (path.is_file() and core.bounded_bytes(path, LIMIT) == raw),
            "Attachment destination conflicts",
        )
        checked.append((path, raw))
        output.append(
            {
                "origin_ref": origin,
                "attachment_ref": {"path": destination, "sha256": core.sha(raw)},
                "byte_count": len(raw),
                "task_id": task_id,
                "attempt": number,
            }
        )
    result = _bounded(
        {"schema_version": 1, "attachments": output, "materialized": materialize, "accepted": False}
    )
    # Rehash all origins after preparation, before any output materialization.
    program.verify_refs([_record(row, "attachment")["origin_ref"] for row in output])
    if materialize:
        program.require_current_location()
        for path, raw in checked:
            _exact_write(path, raw)
    return result


def rebind_policy_paths(value: Record, mapping: dict[str, str]) -> Record:
    """Change only schema-declared policy locators in derived provider/context JSON."""
    result = deepcopy(value)
    containers = [result]
    if result.get("execution") is not None:
        containers.append(_record(result["execution"], "provider execution"))
    for container in containers:
        for stage in _items(container.get("stages", []), "provider stages"):
            for item in _items(
                _record(stage, "provider stage").get("checks", []), "provider checks"
            ):
                check = _record(item, "provider check")
                if "policy_evidence" in check:
                    check["policy_evidence"] = [
                        mapping.get(_text(path, "policy path"), str(path))
                        for path in _items(check["policy_evidence"], "policy evidence")
                    ]
    return result


def _errors() -> tuple[type[Exception], ...]:
    core = _core()
    return (
        ClosureError,
        core.ProgramError,
        coordination.GovernanceError,
        core.questions.QuestionError,
        core.phases.PhaseError,
        core.planning.PlanningError,
    )


def readiness(
    program: Program,
    state: Record,
    limit: int = 50,
    *,
    prevalidated: bool = False,
    verification_facts: Record | None = None,
) -> Record:
    """Bound ordinary closure obligations once, without Git/provider calls or writes."""
    _require(type(limit) is int and 0 < limit <= REFERENCE_LIMIT, "Invalid closure row limit")
    core = _core()
    rows: list[Json] = []
    tasks: list[Json] = []
    director: str | None = None

    def gap(kind: str, reason: str, task_id: str | None = None, actor: str | None = None) -> None:
        rows.append(
            {
                "kind": kind,
                "task_id": task_id,
                "status": "required",
                "reason": reason,
                "next_actor_id": actor or director,
                "next_action": kind,
            }
        )

    try:
        if not prevalidated:
            program.require_current_location()
            program.validate(state)
        director = coordination.current_director_id(state)
        goal = _record(state.get("objective"), "current objective")
        members = core.strings(goal.get("task_ids"), "objective members")
        for task_id in members:
            task = program.task(state, task_id)
            attempts = _items(task["attempts"], "attempts")
            attempt = _record(attempts[-1], "attempt") if attempts else {}
            delivery = (
                program.current_delivery(task)
                if task["status"] == "done"
                else {"status": "unrecorded"}
            )
            metadata = _record(delivery.get("metadata", {}), "delivery metadata")
            cleanup = _record(metadata.get("cleanup", {}), "cleanup")
            tasks.append(
                {
                    "task_id": task_id,
                    "attempt": attempt.get("number"),
                    "review_status": task["status"],
                    "delivery_status": delivery.get("status"),
                    "cleanup_status": cleanup.get("status", "unknown"),
                }
            )
            if task["status"] != "done":
                gap(
                    "task_review",
                    "Current required task is not reviewed complete",
                    task_id,
                    task.get("owner") or director,
                )
                continue
            try:
                if not prevalidated:
                    program.verify_terminal(task, attempt)
                    core.verification.validate_completion(program, state, task_id)
            except _errors() as exc:
                gap("current_verification", str(exc), task_id)
            if delivery.get("status") != "ci_recorded_success":
                gap("delivery", str(delivery.get("status")), task_id)
        for question in program.pending_questions(state, str(goal["id"]), members):
            gap("question", str(question))
        if verification_facts is not None:
            for fact_value in _record(
                verification_facts.get("tasks", {}), "verification tasks"
            ).values():
                fact = _record(fact_value, "verification task")
                task_id = fact.get("task_id")
                if task_id not in members:
                    continue
                lanes = _record(fact.get("lanes", {}), "verification lanes")
                for lane_value in _items(lanes.get("items", []), "verification lane items"):
                    lane = _record(lane_value, "verification lane")
                    if lane.get("applicability") != "current" or lane.get("result") not in {
                        "passed",
                        "not_applicable",
                    }:
                        gap(
                            "current_verification",
                            str(lane.get("request_id")),
                            str(task_id),
                            lane.get("verifier_id"),
                        )
        scopes = [
            path
            for task_id in members
            for path in core.strings(program.task(state, task_id)["candidate_paths"], "task paths")
        ]
        for value in _items(state.get("handoffs", []), "handoffs"):
            transfer = _record(value, "handoff")
            if transfer.get("acceptance") is None:
                outgoing = _record(transfer.get("outgoing"), "outgoing")
                other = core.strings(outgoing.get("scope"), "handoff scope")
                if (
                    transfer.get("role") in {"director", "sub_director"}
                    or transfer.get("task_id") in members
                    or any(
                        a.casefold() == b.casefold()
                        or a.casefold().startswith(b.casefold() + "/")
                        or b.casefold().startswith(a.casefold() + "/")
                        for a in scopes
                        for b in other
                    )
                ):
                    gap("handoff", str(transfer.get("id")))
        accepted = goal.get("status") == "awaiting_next_goal"
        if not accepted:
            gap(
                "integrated_review",
                "Fresh independent aggregate review and explicit director decision remain required",
            )
            _record(rows[-1], "integrated requirement")["actionability"] = (
                "routine"
                if any(_record(task, "task")["review_status"] != "done" for task in tasks)
                else "actionable"
            )
        status = "accepted" if accepted else "incomplete"
        return _bounded(
            {
                "schema_version": 1,
                "status": status,
                "objective_id": goal["id"],
                "task_ids": members,
                "tasks": tasks[:limit],
                "task_counts": {
                    "total": len(tasks),
                    "shown": min(limit, len(tasks)),
                    "omitted": max(0, len(tasks) - limit),
                },
                "requirements": {
                    "total": len(rows),
                    "shown": min(limit, len(rows)),
                    "omitted": max(0, len(rows) - limit),
                    "items": rows[:limit],
                },
                "next_actor_id": "user"
                if accepted
                else _record(rows[0], "requirement").get("next_actor_id"),
                "next_action": "set_next_goal"
                if accepted
                else _record(rows[0], "requirement")["next_action"],
                "accepted": accepted,
                "remote_verified_by_tool": False,
            }
        )
    except _errors() as exc:
        return _bounded(
            {
                "schema_version": 1,
                "status": "unavailable",
                "accepted": False,
                "reason": str(exc),
                "task_ids": [],
                "tasks": [],
                "requirements": {
                    "total": 1,
                    "shown": 1,
                    "omitted": 0,
                    "items": [{"kind": "integrity", "status": "unknown", "reason": str(exc)}],
                },
                "next_actor_id": director,
                "next_action": "inspect_integrity",
                "remote_verified_by_tool": False,
            }
        )


def prepare_delivery_inputs(program: Program, state: Record, request: Record) -> Record:
    """Fill mechanical current individual bindings; required provider facts stay explicit."""
    core = _core()
    proposal = deepcopy(request)
    shared = _items(proposal.pop("shared_evidence", []), "shared delivery evidence")
    frozen_provider = _reference(proposal.get("provider"))
    rows: list[Json] = []
    task_ids: set[str] = set()
    for value in _items(request.get("tasks"), "delivery tasks"):
        binding = deepcopy(_record(value, "delivery binding"))
        task_id = _text(binding.get("task_id"), "task ID")
        _require(task_id not in task_ids, "Duplicate delivery task")
        task_ids.add(task_id)
        task = program.task(state, task_id)
        attempt = program.current_attempt(task)
        submission = _record(attempt.get("submission"), "submission")
        actual = {
            "attempt": attempt["number"],
            "submission_sha256": _record(submission.get("receipt"), "submission receipt")["sha256"],
            "reviewed_source_digest": submission["source_digest"],
        }
        for key, value in actual.items():
            _require(
                key not in binding or binding[key] == value,
                f"Explicit delivery {key} differs from current task",
            )
            binding[key] = value
        prefix_value = binding.pop("assembly_prefix", None)
        if prefix_value is not None:
            prefix = core.safe_relative(_text(prefix_value, "delivery assembly prefix"))
            task_id = _text(binding["task_id"], "task ID")
            number = attempt["number"]
            _require(
                prefix.startswith(f"evidence/{task_id}/attempt-{number}/"),
                "Delivery assembly must bind individual task attempt",
            )
            context = _record(binding.get("provider_context", {}), "provider context")
            originals = deepcopy(shared)
            policies = (
                core.authoring.provider_policy_paths(context)
                if "provider_context" in binding
                else []
            )
            policy_refs = [
                {"path": path, "sha256": core.sha(core.bounded_bytes(program.path(path), LIMIT))}
                for path in policies
            ]
            attachments = []
            for index, original in enumerate([*originals, *policy_refs], 1):
                origin = _reference(original)
                attachments.append(
                    {
                        "origin_ref": origin,
                        "destination": f"{prefix}/attachment-{index:04d}.txt",
                        "task_id": task_id,
                        "attempt": number,
                    }
                )
            attached = prepare_attachments(program, {"attachments": attachments}, materialize=True)
            attached_rows = [
                _record(item, "attachment")
                for item in _items(attached["attachments"], "attachments")
            ]
            mapping = {
                str(_record(item["origin_ref"], "origin")["path"]): str(
                    _record(item["attachment_ref"], "attachment")["path"]
                )
                for item in attached_rows
                if _record(item["origin_ref"], "origin")["path"] in policies
            }
            provider = core.authoring.provider_observation(program, frozen_provider)
            derived_provider = rebind_policy_paths(provider, mapping)
            derived_context = rebind_policy_paths(context, mapping)
            reverse = {destination: origin for origin, destination in mapping.items()}
            _require(
                rebind_policy_paths(derived_provider, reverse) == provider
                and rebind_policy_paths(derived_context, reverse) == context,
                "Policy attachment rebinding changed provider facts",
            )
            provider_path = f"{prefix}/provider.json"
            context_path = f"{prefix}/provider-context.json"
            provider_bytes = core.canonical(derived_provider)
            context_bytes = core.canonical(derived_context)
            _require(
                len(provider_bytes) <= LIMIT and len(context_bytes) <= LIMIT,
                "Derived provider document exceeds 8 MiB",
            )
            _exact_write(program.path(provider_path), provider_bytes)
            _exact_write(program.path(context_path), context_bytes)
            derived_ref = {"path": provider_path, "sha256": core.sha(provider_bytes)}
            provenance_path = f"{prefix}/origin.json"
            provenance: Record = {
                "schema_version": 1,
                "origin_ref": frozen_provider,
                "derived_provider_ref": derived_ref,
                "policy_attachments": attached_rows,
                "derived_context_ref": {"path": context_path, "sha256": core.sha(context_bytes)},
                "outcomes_changed": False,
                "new_execution_claimed": False,
            }
            _exact_write(program.path(provenance_path), core.canonical(provenance))
            if "provider_context" in binding:
                binding["provider_context"] = derived_context
            binding["evidence"] = [
                *_items(binding.get("evidence", []), "individual delivery evidence"),
                *[
                    str(_record(item["attachment_ref"], "attachment")["path"])
                    for item in attached_rows
                ],
                provider_path,
                context_path,
                provenance_path,
            ]
            # Each derived capture has distinct task policy paths, so assess individually.
            assembled = core.authoring.delivery_inputs(
                program, state, {"provider": derived_ref, "tasks": [binding]}
            )
            rows.extend(_items(assembled["inputs"], "prepared delivery inputs"))
            continue
        ordinary = core.authoring.delivery_inputs(
            program, state, {"provider": frozen_provider, "tasks": [binding]}
        )
        rows.extend(_items(ordinary["inputs"], "prepared delivery inputs"))
    _require(bool(rows), "Delivery preparation requires individual tasks")
    program.verify_refs([frozen_provider])
    return _bounded(
        {
            "status": "not_assessed",
            "inputs": rows,
            "provider": frozen_provider,
            "limitations": [
                "Caller-supplied frozen facts; no remote authentication, CI invocation or canonical delivery."
            ],
        }
    )


def prepare_completion_inputs(program: Program, state: Record, request: Record) -> Record:
    """Compose ordinary completion scaffold with explicit authored decision facts only."""
    import argparse

    core = _core()
    scope = core.strings(request.get("scope"), "aggregate scope")
    scaffold = core.authoring.scaffold(
        program,
        state,
        argparse.Namespace(operation="objective-complete", kind=None, task=None, scope=scope),
    )
    receipt = _record(scaffold["receipt"], "completion receipt")
    facts = _record(request.get("facts"), "explicit completion facts")
    allowed = {
        "actor_id",
        "summary",
        "criteria_results",
        "remote_state_checked",
        "required_ci",
        "ci",
        "integrated_review",
        "unresolved_findings",
        "evidence",
    }
    _require(set(facts) <= allowed, "Completion facts cannot override mechanical binding")
    receipt.update(deepcopy(facts))
    return _bounded(
        {
            "schema_version": 1,
            "receipt": receipt,
            "related_inputs": scaffold["related_inputs"],
            "missing_fields": core.authoring._missing(receipt),
            "accepted": False,
            "remote_verified_by_tool": False,
        }
    )


def prepare_cleanup_inputs(program: Program, request: Record) -> Record:
    """Assess explicit actual cleanup facts through the existing offline assessor."""
    if "resources" not in request:
        return _core().authoring.provider_preparation(program, deepcopy(request), "cleanup-inputs")
    resources = _items(request["resources"], "cleanup resources")
    _require(
        bool(resources) and len(resources) <= REFERENCE_LIMIT,
        "Cleanup resources must be bounded and nonempty",
    )
    results: list[Json] = []
    identifiers: set[str] = set()
    for value in resources:
        resource = _record(value, "cleanup resource")
        identifier = _text(resource.get("id"), "cleanup resource ID")
        _require(identifier not in identifiers, "Duplicate cleanup resource")
        identifiers.add(identifier)
        result = _core().authoring.provider_preparation(
            program,
            {
                "provider": resource.get("provider"),
                "provider_context": resource.get("provider_context"),
            },
            "cleanup-inputs",
        )
        results.append({"id": identifier, **result})
    return _bounded(
        {
            "schema_version": 1,
            "resources": results,
            "accepted": False,
            "remote_verified_by_tool": False,
        }
    )


def prepare_applicability(program: Program, state: Record, request: Record) -> Record:
    """Use the inputs owner's explicit per-target current binding assessment."""
    core = _core()
    targets = core.strings(request.get("task_ids"), "applicability target tasks")
    run_ref = _reference(request.get("run_capture_ref"))
    actor = _text(request.get("actor_id"), "actual applicability actor")
    result = core.verification.attachment_bindings(
        program, state, targets, run_ref, actor_id=actor, reporter_id=request.get("reporter_id")
    )
    selections = _items(request.get("observations", []), "target observation declarations")
    if not selections:
        return result
    declared = {
        str(_record(value, "observation declaration").get("task_id")): _record(
            value, "observation declaration"
        )
        for value in selections
    }
    _require(
        len(declared) == len(selections) and set(declared) == set(targets),
        "Observation declarations must bind exact selected tasks",
    )
    run = core.authoring.provider_observation(program, run_ref)
    summary = core.flow_module("execution").summarize_capture(run)
    _require(
        summary.get("reported_check_result") == "passed"
        and summary.get("comparison") == "unchanged"
        and summary.get("capture_status") == "complete",
        "Actual execution cannot supply complete compatible target evidence",
    )
    process = _record(run.get("process"), "actual process")
    _require(
        process.get("ended_at") is not None and process.get("clock_id") is not None,
        "Actual execution endpoint unavailable",
    )
    records: list[Json] = []
    for item_value in _items(result["items"], "attachment targets"):
        item = _record(item_value, "attachment target")
        if item["status"] != "attachment_candidate":
            continue
        task_id = _text(item["task_id"], "target task")
        declaration = declared[task_id]
        identifier = _text(declaration.get("id"), "target observation ID")
        _require(core.portable_task_id(identifier), "Invalid target observation ID")
        prefix = core.safe_relative(
            _text(declaration.get("attachment_prefix"), "target attachment prefix")
        )
        number = item["attempt"]
        _require(
            prefix.startswith(f"evidence/{task_id}/attempt-{number}/"),
            "Target attachment namespace differs",
        )
        origins = [
            run_ref,
            _reference(run.get("input_capture_before")),
            _reference(run.get("input_capture_after")),
        ]
        for value in _record(run.get("outputs"), "actual outputs").values():
            output = _record(value, "actual stream")
            if output.get("path") is not None:
                origins.append(_reference({"path": output["path"], "sha256": output["sha256"]}))
        for value in _items(
            _record(run.get("artifacts"), "actual artifacts").get("captured"), "captured artifacts"
        ):
            artifact = _record(value, "actual artifact")
            origins.append(_reference({"path": artifact["path"], "sha256": artifact["sha256"]}))
        attached = prepare_attachments(
            program,
            {
                "attachments": [
                    {
                        "origin_ref": ref,
                        "destination": f"{prefix}/capture-{index:04d}.txt",
                        "task_id": task_id,
                        "attempt": number,
                    }
                    for index, ref in enumerate(origins)
                ]
            },
            materialize=True,
        )
        copies = [
            _record(value, "attachment") for value in _items(attached["attachments"], "attachments")
        ]
        before_ref = _record(copies[1]["attachment_ref"], "before attachment")
        after_ref = _record(copies[2]["attachment_ref"], "after attachment")
        before = core.authoring.provider_observation(program, before_ref)
        after = core.authoring.provider_observation(program, after_ref)
        observation: Record = {
            "schema_version": 1,
            "operation": "verification-record",
            "id": identifier,
            "kind": "finished",
            "task_id": task_id,
            "actor_id": actor,
            "expected_revision": core.phases.revision(state),
            "reason": _text(declaration.get("reason"), "actual attachment reason"),
            "request_id": item["request_id"],
            "request_version": item["request_version"],
            "result": summary["reported_check_result"],
            "observed_at": process["ended_at"],
            "clock_id": process["clock_id"],
            "method": deepcopy(run["method"]),
            "environment": core.digest(run["environment"]),
            "input_before": before["input_digest"],
            "input_after": after["input_digest"],
            "input_capture_before": before_ref,
            "input_capture_after": after_ref,
            "run_capture_ref": run_ref,
            "artifacts": [
                str(_record(value["attachment_ref"], "attachment")["path"]) for value in copies
            ],
        }
        if "reporter_id" in request:
            observation["reporter_id"] = request["reporter_id"]
        if process.get("exit_code") is not None:
            observation["exit_code"] = process["exit_code"]
        records.append(
            {
                "task_id": task_id,
                "receipt": observation,
                "attachments": copies,
                "origin_ref": run_ref,
                "published": False,
            }
        )
    result["inputs"] = records
    result["execution_replayed"] = False
    result["accepted"] = False
    return _bounded(result)


def prepare_inputs(program: Program, state: Record, request: Record, host: ClosureHost) -> Record:
    """One supported preparation surface for ordinary receipts or an exact record plan."""
    kind = _text(request.get("kind", "plan"), "closure preparation kind")
    if kind == "plan":
        return prepare_plan(program, state, request, host)
    supplied = deepcopy(_record(request.get("input"), "closure preparation input"))
    match kind:
        case "attachments":
            return prepare_attachments(
                program, supplied, materialize=request.get("materialize") is True
            )
        case "delivery":
            result = prepare_delivery_inputs(program, state, supplied)
            if "receipt_destinations" in request:
                _materialize_receipts(
                    program,
                    result,
                    _record(request["receipt_destinations"], "receipt destinations"),
                )
            return _bounded(result)
        case "completion":
            result = prepare_completion_inputs(program, state, supplied)
            if "receipt_destination" in request:
                receipt = _record(result["receipt"], "completion receipt")
                path = _core().safe_relative(
                    _text(request["receipt_destination"], "completion receipt destination")
                )
                _require(
                    path.startswith("evidence/_coordination/attempt-"),
                    "Completion receipt must be coordination evidence",
                )
                raw = _core().canonical(receipt)
                _require(len(raw) <= LIMIT, "Completion receipt exceeds 8 MiB")
                program.require_current_location()
                _exact_write(program.path(path), raw)
                result["receipt_ref"] = {"path": path, "sha256": _core().sha(raw)}
            return _bounded(result)
        case "cleanup":
            return prepare_cleanup_inputs(program, supplied)
        case "applicability":
            result = prepare_applicability(program, state, supplied)
            if "receipt_destinations" in request:
                _materialize_receipts(
                    program,
                    result,
                    _record(request["receipt_destinations"], "receipt destinations"),
                )
            return _bounded(result)
        case "postconditions":
            return postconditions(program, state)
        case _:
            raise ClosureError("Unsupported closure preparation kind")


def _materialize_receipts(program: Program, result: Record, destinations: Record) -> None:
    """Write explicitly selected ordinary task receipts, never canonical records."""
    core = _core()
    items = [
        _record(value, "proposed task receipt")
        for value in _items(result.get("inputs"), "proposed receipts")
    ]
    _require(
        set(destinations) == {str(item["task_id"]) for item in items},
        "Receipt destinations must cover exact proposed tasks",
    )
    checked: list[tuple[Path, bytes, Record, str]] = []
    for item in items:
        receipt = _record(item.get("receipt"), "ordinary receipt")
        task_id = _text(receipt.get("task_id"), "receipt task")
        attempt = receipt.get("attempt", item.get("attempt"))
        if attempt is None:
            state = program.load()
            attempt = program.current_attempt(program.task(state, task_id))["number"]
        path = core.safe_relative(_text(destinations[task_id], "ordinary receipt destination"))
        _require(
            path.startswith(f"evidence/{task_id}/attempt-{attempt}/"),
            "Receipt destination differs from task attempt",
        )
        raw = core.canonical(receipt)
        _require(len(raw) <= LIMIT, "Ordinary receipt exceeds 8 MiB")
        destination = program.path(path)
        _require(
            not destination.exists()
            or (destination.is_file() and core.bounded_bytes(destination, LIMIT) == raw),
            "Ordinary receipt destination conflicts",
        )
        checked.append((destination, raw, item, path))
    program.require_current_location()
    for destination, raw, item, path in checked:
        _exact_write(destination, raw)
        item["receipt_ref"] = {"path": path, "sha256": core.sha(raw)}


def _refs(program: Program, values: list[Json]) -> list[Json]:
    _require(len(values) <= REFERENCE_LIMIT, "Too many closure references")
    refs: list[Json] = []
    seen: set[str] = set()
    remaining = READ_LIMIT
    for value in values:
        ref, raw = _read_ref(program, value, remaining)
        _require(
            ref["path"] not in {"backlog.json", "BOARD.md", "CHECKPOINT.md"}
            and not str(ref["path"]).startswith("tasks/"),
            "Mutable registry/view observations cannot be immutable closure evidence",
        )
        remaining -= len(raw)
        key = _core().digest(ref)
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs


def prepare_plan(program: Program, state: Record, request: Record, host: ClosureHost) -> Record:
    """Materialize inert inputs then preflight every projected predecessor before commit."""
    core = _core()
    program.require_current_location()
    program.validate(state)
    producer = deepcopy(host.identity())
    owner = _text(request.get("owner_id"), "plan owner")
    stages = _items(request.get("stages"), "plan stages")
    _require(
        bool(stages) and len(stages) <= REFERENCE_LIMIT, "Plan stages must be nonempty and bounded"
    )
    source_scope = core.strings(request.get("scope"), "plan source scope")
    _require(bool(source_scope), "Plan requires explicit source scope")
    binding: Record = {
        "scope": source_scope,
        "source_digest": program.source_digest(source_scope),
        "source_head": program.current_head(),
    }
    references = deepcopy(_items(request.get("refs", []), "plan references"))
    attachments = prepare_attachments(
        program, {"attachments": request.get("attachments", [])}, materialize=True
    )
    for value in _items(attachments["attachments"], "attachments"):
        row = _record(value, "attachment")
        references.extend([row["origin_ref"], row["attachment_ref"]])
    prepared_stages: list[Json] = []
    proposal = deepcopy(state)
    stage_ids: set[str] = set()
    for value in stages:
        stage = _record(value, "stage")
        _require(
            set(stage) <= {"id", "operation", "payload", "observed_at", "documents"},
            "Unknown stage fields",
        )
        identifier = _text(stage.get("id"), "stage ID")
        _require(identifier not in stage_ids, "Duplicate stage ID")
        stage_ids.add(identifier)
        operation = _text(stage.get("operation"), "stage operation")
        _require(operation in OPERATIONS, "Unsupported closure operation")
        payload = deepcopy(_record(stage.get("payload"), "stage payload"))
        _require(
            not ({"argv", "command", "repo", "program_dir"} & set(payload)),
            "Stored command execution or location override is prohibited",
        )
        observed_at = _text(stage.get("observed_at"), "stage observation time")
        before_revision = host.revision(proposal)
        for document_value in _items(stage.get("documents", []), "stage documents"):
            document = _record(document_value, "stage document")
            _require(
                set(document) <= {"path", "value", "bind_expected_revision"},
                "Unknown stage document fields",
            )
            path = core.safe_relative(_text(document.get("path"), "stage document path"))
            _require(path.startswith("evidence/"), "Stage documents must be private evidence")
            content = deepcopy(_record(document.get("value"), "ordinary receipt"))
            if document.get("bind_expected_revision") is True:
                _require(
                    content.get("expected_revision") in {None, before_revision},
                    "Explicit receipt revision differs",
                )
                content["expected_revision"] = before_revision
            raw = core.canonical(content)
            _require(len(raw) <= LIMIT, "Stage document exceeds 8 MiB")
            _exact_write(program.path(path), raw)
            references.append({"path": path, "sha256": core.sha(raw)})
        for field in ("input", "receipt", "review", "revalidation"):
            if field in payload:
                path = core.safe_relative(_text(payload[field], f"stage {field}"))
                references.append(
                    {
                        "path": path,
                        "sha256": core.sha(core.bounded_bytes(program.path(path), LIMIT)),
                    }
                )
        events = deepcopy(_items(proposal.get("events"), "predecessor events"))
        prepared = host.prepare(proposal, operation, payload, observed_at, owner=owner)
        host.preflight(prepared)
        after_events = _items(prepared.state.get("events"), "prepared events")
        _require(
            after_events[: len(events)] == events and len(after_events) > len(events),
            "Closure stage must append ordinary immutable events",
        )
        appended = deepcopy(after_events[len(events) :])
        prepared_stages.append(
            {
                "id": identifier,
                "operation": operation,
                "payload": payload,
                "observed_at": observed_at,
                "before_revision": before_revision,
                "after_revision": host.revision(prepared.state),
                "event_prefix_length": len(events),
                "event_prefix_sha256": core.digest(events),
                "appended_events": appended,
                "appended_events_sha256": core.digest(appended),
            }
        )
        proposal = deepcopy(prepared.state)
    refs = _refs(program, references)
    for value in prepared_stages:
        _record(value, "stage")["refs"] = deepcopy(refs)
    _require(
        program.source_digest(source_scope) == binding["source_digest"]
        and program.current_head() == binding["source_head"],
        "Source changed during closure preflight",
    )
    plan: Record = {
        "schema_version": 1,
        "producer": producer,
        "program_id": state["program_id"],
        "owner_id": owner,
        "initial_revision": host.revision(state),
        "source_binding": binding,
        "refs": refs,
        "stages": prepared_stages,
        "preflight": {
            "status": "validated",
            "initial_revision": host.revision(state),
            "stage_count": len(prepared_stages),
            "publication": False,
        },
    }
    plan["plan_digest"] = core.digest(plan)
    _require(host.identity() == producer, "Closure producer changed during preflight")
    _require(
        host.revision(host.load()) == host.revision(state),
        "Registry changed during closure preflight",
    )
    return _bounded(plan)


def project_plan(
    program: Program, state: Record, plan: Record, host: ClosureHost, limit: int = 50
) -> Record:
    """Read a selected immutable plan's actual stages without storing new status."""
    _require(type(limit) is int and 0 < limit <= REFERENCE_LIMIT, "Invalid plan projection limit")
    owner = plan.get("owner_id")
    try:
        if plan.get("kind") == "closure_preparation_failure":
            _require(
                plan.get("schema_version") == 1
                and plan.get("status") == "failed"
                and plan.get("accepted") is False,
                "Invalid recorded preparation failure",
            )
            _require(plan.get("producer") == host.identity(), "Failure producer identity changed")
            program.require_current_location()
            program.validate(state)
            _refs(program, _items(plan.get("refs"), "failed preparation refs"))
            failure = _record(plan.get("failure"), "actual preparation failure")
            reason = _text(failure.get("reason"), "actual failure reason")
            current = False
            if plan.get("source_binding") is not None:
                source = _record(plan["source_binding"], "failed preparation source")
                current = (
                    plan.get("initial_revision") == host.revision(state)
                    and source.get("source_head") == program.current_head()
                    and source.get("source_digest")
                    == program.source_digest(
                        _core().strings(source.get("scope"), "failed source scope")
                    )
                )
            return _bounded(
                {
                    "schema_version": 1,
                    "preparation": {"status": "failed"},
                    "preflight": {
                        "status": "failed",
                        "current": current,
                        "historical_registry_revision": plan.get("initial_revision"),
                    },
                    "publication": {
                        "status": "blocked",
                        "stages": {"total": 0, "shown": 0, "omitted": 0, "items": []},
                    },
                    "next_actor_id": owner,
                    "next_action": "correct_preparation_input",
                    "reasons": [reason],
                    "accepted": False,
                    "remote_verified_by_tool": False,
                }
            )
        inspection = inspect_plan(program, plan, state, host)
        preflight = _record(plan.get("preflight"), "actual plan preflight")
        _require(
            preflight.get("status") == "validated"
            and preflight.get("initial_revision") == plan.get("initial_revision")
            and preflight.get("stage_count") == len(_items(plan.get("stages"), "plan stages")),
            "Plan preflight provenance incomplete",
        )
        rows: list[Json] = []
        next_actor = owner
        next_action = "inspect_postconditions"
        publication = inspection["status"]
        for value, observed in zip(
            _items(plan["stages"], "plan stages"),
            _items(inspection["stages"], "inspected stages"),
            strict=True,
        ):
            stage = _record(value, "stage")
            actual = _record(observed, "stage observation")
            payload = _record(stage["payload"], "payload")
            actor = payload.get("owner", owner)
            status = actual["status"]
            action = (
                "inspect_postconditions" if status == "committed" else "publish_remaining_records"
            )
            if actual.get("view_recovery_required") is True:
                status = "committed_view_failed"
                action = "render"
                publication = "view_recovery_required"
            if next_action == "inspect_postconditions" and (status != "committed"):
                next_actor = actor
                next_action = action
            rows.append(
                {
                    "id": stage["id"],
                    "operation": stage["operation"],
                    "task_id": payload.get("id"),
                    "status": status,
                    "next_actor_id": actor,
                    "next_action": action,
                    "view_recovery_required": actual.get("view_recovery_required", False),
                    "observed_at": stage["observed_at"],
                }
            )
        return _bounded(
            {
                "schema_version": 1,
                "plan_digest": plan["plan_digest"],
                "preparation": {"status": "prepared"},
                "preflight": {
                    "status": "validated",
                    "current": True,
                    "historical_registry_revision": plan["initial_revision"],
                },
                "publication": {
                    "status": publication,
                    "stages": {
                        "total": len(rows),
                        "shown": min(len(rows), limit),
                        "omitted": max(0, len(rows) - limit),
                        "items": rows[:limit],
                    },
                    "committed": inspection["committed"],
                    "remaining": inspection["remaining"],
                },
                "next_actor_id": next_actor,
                "next_action": next_action,
                "reasons": [],
                "accepted": False,
                "remote_verified_by_tool": False,
            }
        )
    except _errors() as exc:
        return _bounded(
            {
                "schema_version": 1,
                "plan_digest": plan.get("plan_digest"),
                "preparation": {"status": "stale"},
                "preflight": {
                    "status": "not_assessed",
                    "current": False,
                    "historical_registry_revision": plan.get("initial_revision"),
                },
                "publication": {
                    "status": "blocked",
                    "stages": {"total": 0, "shown": 0, "omitted": 0, "items": []},
                },
                "next_actor_id": owner,
                "next_action": "inspect_plan_binding",
                "reasons": [str(exc)],
                "accepted": False,
                "remote_verified_by_tool": False,
            }
        )


def inspect_plan(program: Program, plan: Record, state: Record, host: ClosureHost) -> Record:
    """Recognize exact canonical committed events; never infer an older green outcome."""
    core = _core()
    _require(plan.get("schema_version") == 1, "Unsupported closure plan schema")
    unsigned = {key: deepcopy(value) for key, value in plan.items() if key != "plan_digest"}
    _require(core.digest(unsigned) == plan.get("plan_digest"), "Closure plan changed")
    _require(plan.get("producer") == host.identity(), "Closure producer identity changed")
    _require(plan.get("program_id") == state.get("program_id"), "Closure program identity differs")
    program.require_current_location()
    program.validate(state)
    refs = _refs(program, _items(plan.get("refs"), "plan refs"))
    binding = _record(plan.get("source_binding"), "source binding")
    scope = core.strings(binding.get("scope"), "source scope")
    _require(
        program.source_digest(scope) == binding.get("source_digest")
        and program.current_head() == binding.get("source_head"),
        "Closure source changed",
    )
    events = _items(state.get("events"), "canonical events")
    results: list[Json] = []
    seen_pending = False
    previous_revision = plan.get("initial_revision")
    for value in _items(plan.get("stages"), "plan stages"):
        stage = _record(value, "stage")
        _require(stage.get("operation") in OPERATIONS, "Unsupported closure operation")
        _require(
            stage.get("before_revision") == previous_revision, "Closure predecessor chain changed"
        )
        previous_revision = stage.get("after_revision")
        prefix = stage.get("event_prefix_length")
        _require(type(prefix) is int and prefix >= 0, "Invalid stage event position")
        appended = _items(stage.get("appended_events"), "stage events")
        _require(
            core.digest(appended) == stage.get("appended_events_sha256"),
            "Stage event identity changed",
        )
        exact = (
            len(events) >= prefix + len(appended)
            and core.digest(events[:prefix]) == stage.get("event_prefix_sha256")
            and events[prefix : prefix + len(appended)] == appended
        )
        status = "committed" if exact else "not_started"
        was_pending = seen_pending
        if exact:
            _require(not seen_pending, "Closure commit order differs")
        else:
            seen_pending = True
        host_result = (
            host.inspect_stage(state, stage)
            if exact or not was_pending
            else {"status": "not_started"}
        )
        _require(
            host_result.get("status") == status,
            "Canonical closure stage conflicts with host assessment",
        )
        results.append(
            {
                "id": stage["id"],
                "status": status,
                "view_recovery_required": host_result.get("view_recovery_required", False),
            }
        )
    committed = sum(_record(row, "stage")["status"] == "committed" for row in results)
    return _bounded(
        {
            "schema_version": 1,
            "status": "published"
            if committed == len(results)
            else "partial"
            if committed
            else "not_started",
            "stages": results,
            "committed": committed,
            "remaining": len(results) - committed,
            "refs": refs,
            "accepted": False,
            "execution_replayed": False,
        }
    )


def resume_plan(
    program: Program, plan: Record, host: ClosureHost, owner: str, expected_revision: str
) -> Record:
    """Publish only exact uncommitted stages through fresh ordinary host guards."""
    core = _core()
    _require(owner == plan.get("owner_id"), "Closure owner differs")
    state = host.load()
    _require(
        host.revision(state) == expected_revision, "Closure mutation revision changed; refresh"
    )
    inspection = inspect_plan(program, plan, state, host)
    stages = _items(plan.get("stages"), "stages")
    results = deepcopy(_items(inspection["stages"], "stage results"))
    for index, value in enumerate(stages):
        stage = _record(value, "stage")
        row = _record(results[index], "stage result")
        if row.get("view_recovery_required") is True:
            return _bounded(
                {
                    **inspection,
                    "status": "view_recovery_required",
                    "next_action": "render",
                    "stages": results,
                }
            )
        if row["status"] == "committed":
            continue
        state = host.load()
        inspect_plan(program, plan, state, host)
        _require(
            host.revision(state) == stage["before_revision"],
            "Uncommitted predecessor changed; reprepare remaining records without replay",
        )
        outcome = host.publish(stage, str(stage["before_revision"]), owner)
        _require(
            outcome.get("status") in {"committed", "committed_view_failed"},
            "Host did not confirm canonical publication",
        )
        actual = host.load()
        _require(
            host.revision(actual) == stage["after_revision"],
            "Published state differs from exact prepared stage",
        )
        verified = inspect_plan(program, plan, actual, host)
        _require(
            _record(_items(verified["stages"], "verified stages")[index], "verified stage")[
                "status"
            ]
            == "committed",
            "Host publication lacks exact canonical event",
        )
        results[index] = {
            "id": stage["id"],
            "status": outcome["status"],
            "view_recovery_required": outcome["status"] == "committed_view_failed",
        }
        if outcome["status"] == "committed_view_failed":
            return _bounded(
                {
                    "schema_version": 1,
                    "status": "view_recovery_required",
                    "stages": results,
                    "next_action": "render",
                    "accepted": False,
                    "execution_replayed": False,
                }
            )
    return _bounded(
        {
            "schema_version": 1,
            "status": "published",
            "stages": results,
            "accepted": False,
            "execution_replayed": False,
        }
    )


def postconditions(program: Program, state: Record) -> Record:
    """Report actual canonical acceptance and refs; external/native observations stay separate."""
    result = readiness(program, state)
    goal = state.get("objective")
    if goal is not None and result["status"] != "unavailable":
        objective = _record(goal, "objective")
        result["completion"] = deepcopy(objective.get("completion"))
        result["integrated_review_ref"] = deepcopy(objective.get("integrated_review_ref"))
        result["native_final_observation"] = "not_assessed"
        if objective.get("completion") is not None:
            completion = _record(objective["completion"], "completed receipt")
            result["current_source_matches_completion"] = program.current_head() == completion.get(
                "source_head"
            ) and program.source_digest(
                _core().strings(completion.get("scope"), "completed scope")
            ) == completion.get("source_digest")
    return _bounded(result)
