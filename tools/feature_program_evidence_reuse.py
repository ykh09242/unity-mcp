"""Exact evidence selection and ordinary receipt preparation; never execute or publish.

Raw bytes, actual decisions and mechanical bindings remain separate. Canonical host
adapters own current authority, actual filesystem preflight and publication.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import json
import math
import re
from types import ModuleType
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from feature_program import Program, ReadBudget

Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record = dict[str, Json]
LIMIT: Final = 8 * 1024 * 1024
READ_LIMIT: Final = 64 * 1024 * 1024
MAX_ROWS: Final = 2000
ROLES: Final = frozenset({"artifact", "decision", "control_receipt"})
OPERATIONS: Final = frozenset(
    {"submission", "review", "integrated_review", "completion", "delivery", "observation"}
)


class ReceiptAdapter(Protocol):
    """Real inert-file preparation, ordinary validators and publication guards only."""

    def assess(
        self, program: Program, state: Record, operation: str, receipt: Record, *, owner: str | None
    ) -> Record: ...


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _closure() -> ModuleType:
    return _core().flow_module("closure")


def _require(condition: bool, reason: str) -> None:
    _core().require(condition, reason)


def _record(value: Json, label: str) -> Record:
    return _core().record(value, label)


def _rows(value: Json, label: str) -> list[Json]:
    rows = _core().sequence(value, label)
    _require(len(rows) <= MAX_ROWS, f"Too many {label}")
    return rows


def _text(value: Json, label: str) -> str:
    return _core().string(value, label)


def _shape(value: Record, required: set[str], optional: set[str], label: str) -> None:
    _require(
        required <= value.keys() and value.keys() <= required | optional, f"Invalid {label} fields"
    )


def _ref(value: Json) -> Record:
    ref = _record(value, "immutable reference")
    _shape(ref, {"path", "sha256"}, set(), "reference")
    _core().safe_relative(_text(ref["path"], "reference path"))
    _require(
        bool(re.fullmatch(r"[a-f0-9]{64}", _text(ref["sha256"], "reference hash"))),
        "Invalid reference hash",
    )
    return deepcopy(ref)


def _bounded(value: Record) -> Record:
    _require(len(_core().canonical(value)) <= LIMIT, "Evidence preparation exceeds 8 MiB")
    return value


def _read(program: Program, ref: Record, budget: ReadBudget) -> bytes:
    program.verify_refs([ref], budget)
    captured = budget.capture(program.directory, str(ref["path"]), LIMIT)
    _require(captured.sha256 == ref["sha256"], f"Frozen evidence changed: {ref['path']}")
    return captured.content


def _json(program: Program, ref: Record, budget: ReadBudget) -> Record:
    """Strictly decode checked bytes while preserving the consumer's own reference check."""
    _read(program, ref, budget)
    return budget.json(budget.capture(program.directory, str(ref["path"]), LIMIT))


def _version(value: Record, kind: str) -> None:
    _require(
        type(value.get("schema_version")) is int
        and value["schema_version"] == 1
        and value.get("kind") == kind,
        f"Unsupported {kind}",
    )


def _target_binding(
    program: Program,
    state: Record,
    task_id: str,
    attempt: int,
    *,
    sources: ReadBudget | None = None,
) -> Record:
    core = _core()
    _require(type(attempt) is int and attempt > 0, "Invalid target attempt")
    if task_id == "_coordination":
        _require(attempt == 1, "Coordination evidence requires attempt 1")
        return {
            "program_id": state["program_id"],
            "task_id": task_id,
            "attempt": attempt,
            "objective_binding": core.coverage.objective_binding(state)
            if state.get("objective") is not None
            else None,
        }
    _require(core.portable_task_id(task_id), "Invalid target task")
    task = program.task(state, task_id)
    current = program.current_attempt(task)
    _require(current["number"] == attempt, "Target attempt is no longer current")
    return {
        "program_id": state["program_id"],
        "task_id": task_id,
        "attempt": attempt,
        "owner": current["owner"],
        "definition_digest": core.digest(
            {k: v for k, v in task.items() if k not in core.phases.TASK_PROGRESS_FIELDS}
        ),
        "source_digest": core.digest(
            program.source_snapshot(core.strings(current["scope"], "owned scope"), sources)
        ),
        "required_checks": deepcopy(task["required_checks"]),
        "objective_binding": core.coverage.objective_binding(state)
        if state.get("objective") is not None
        else None,
    }


def _mappings(value: Json, keys: set[str]) -> list[Json]:
    result: list[Json] = []
    seen: set[tuple[str, str]] = set()
    for raw in _rows(value, "evidence mappings"):
        item = _record(raw, "evidence mapping")
        _shape(item, {"family", "identity", "keys"}, set(), "mapping")
        _require(item["family"] in {"check", "criterion"}, "Invalid mapping family")
        identity = _text(item["identity"], "mapping identity")
        selected = _core().strings(item["keys"], "mapping keys")
        _require(bool(selected) and set(selected) <= keys, "Mapping names unselected evidence")
        pair = (str(item["family"]), identity)
        _require(pair not in seen, "Duplicate evidence mapping")
        seen.add(pair)
        result.append({"family": item["family"], "identity": identity, "keys": sorted(selected)})
    return sorted(result, key=lambda item: (str(item["family"]), str(item["identity"])))


def _plan_evidence(
    program: Program,
    state: Record,
    selection: Record,
    *,
    budget: ReadBudget | None = None,
    bindings: list[Record] | None = None,
) -> Record:
    """Precheck selected structure/bytes/conflicts; this is not ordinary FS semantic preflight."""
    core = _core()
    program.require_current_location()
    _shape(selection, {"schema_version", "origins", "targets"}, set(), "evidence selection")
    _require(
        type(selection["schema_version"]) is int and selection["schema_version"] == 1,
        "Unsupported evidence selection",
    )
    budget = budget if budget is not None else core.ReadBudget(READ_LIMIT)
    sources = core.ReadBudget(128 * 1024 * 1024)
    origins: dict[str, Record] = {}
    normalized_origins: list[Json] = []
    for raw in _rows(selection["origins"], "selected origins"):
        item = _record(raw, "selected origin")
        _shape(item, {"key", "origin_ref", "role"}, set(), "origin")
        key = _text(item["key"], "selection key")
        _require(
            bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key)) and key not in origins,
            "Invalid or duplicate local selection key",
        )
        _require(item["role"] in ROLES, "Invalid evidence role")
        ref = _ref(item["origin_ref"])
        raw_bytes = _read(program, ref, budget)
        if item["role"] != "artifact":
            _json(program, ref, budget)
        origin = {"key": key, "origin_ref": ref, "role": item["role"], "byte_count": len(raw_bytes)}
        origins[key] = origin
        normalized_origins.append({"key": key, "origin_ref": ref, "role": item["role"]})
    _require(bool(origins), "Select at least one evidence origin")
    normalized_targets: list[Json] = []
    targets: list[Json] = []
    destinations: set[str] = set()
    raw_targets = _rows(selection["targets"], "selected targets")
    _require(bindings is None or len(bindings) == len(raw_targets), "Invalid saved binding count")
    for index, raw in enumerate(raw_targets):
        target = _record(raw, "evidence target")
        _shape(target, {"task_id", "attempt", "prefix", "keys"}, {"mappings"}, "target")
        task_id = _text(target["task_id"], "target task")
        binding = (
            _target_binding(program, state, task_id, target["attempt"], sources=sources)
            if bindings is None
            else deepcopy(bindings[index])
        )
        _require(
            binding.get("program_id") == state["program_id"]
            and binding.get("task_id") == task_id
            and binding.get("attempt") == target["attempt"],
            "Saved binding identity differs",
        )
        _require(type(target["attempt"]) is int and target["attempt"] > 0, "Invalid target attempt")
        _require(
            core.portable_task_id(task_id) or task_id == "_coordination", "Invalid target task"
        )
        prefix = core.safe_relative(_text(target["prefix"], "target prefix"))
        required_prefix = f"evidence/{task_id}/attempt-{target['attempt']}/"
        _require(prefix.startswith(required_prefix), "Evidence target escaped task/attempt scope")
        keys = core.strings(target["keys"], "selected target keys")
        _require(
            bool(keys) and set(keys) <= origins.keys(), "Target names unknown selected origins"
        )
        mappings = _mappings(target.get("mappings", []), set(keys))
        copies: list[Json] = []
        for key in sorted(keys):
            origin = origins[key]
            ref = origin["origin_ref"]
            suffix = PurePosixPath(str(ref["path"])).suffix
            _require(len(suffix) <= 16, "Evidence extension exceeds bound")
            name = f"{prefix}/{key}-{ref['sha256'][:16]}{suffix}"
            folded = name.casefold()
            _require(folded not in destinations, "Duplicate evidence destination")
            destinations.add(folded)
            path = program.path(name)
            if path.exists():
                _require(
                    path.is_file() and budget.read(path, LIMIT) == _read(program, ref, budget),
                    "Evidence destination conflicts",
                )
            copies.append(
                {**deepcopy(origin), "attachment_ref": {"path": name, "sha256": ref["sha256"]}}
            )
        manifest_path = f"{prefix}/evidence-provenance.json"
        _require(manifest_path.casefold() not in destinations, "Manifest destination conflicts")
        destinations.add(manifest_path.casefold())
        normalized_targets.append(
            {
                "task_id": task_id,
                "attempt": target["attempt"],
                "prefix": prefix,
                "keys": sorted(keys),
                "mappings": mappings,
            }
        )
        targets.append(
            {
                "binding": binding,
                "manifest_path": manifest_path,
                "attachments": copies,
                "mappings": mappings,
            }
        )
    _require(bool(targets), "Select at least one evidence target")
    _require(
        sum(len(target["attachments"]) for target in targets) <= MAX_ROWS,
        "Attachment count exceeds bound",
    )
    normalized = {
        "schema_version": 1,
        "origins": sorted(normalized_origins, key=lambda item: str(item["key"])),
        "targets": normalized_targets,
    }
    program.verify_refs([origin["origin_ref"] for origin in origins.values()], budget)
    return _bounded(
        {
            "schema_version": 1,
            "kind": "evidence_plan",
            "program_id": state["program_id"],
            "selection": normalized,
            "selection_digest": core.digest(normalized),
            "targets": targets,
            "status": "ready",
            "accepted": False,
            "missing_fields": [],
            "reasons": [],
        }
    )


def plan_evidence(
    program: Program, state: Record, selection: Record, *, budget: ReadBudget | None = None
) -> Record:
    """Prepare exact current target bindings and inert copy destinations, without publishing."""
    return _plan_evidence(program, state, selection, budget=budget)


def validate_manifest(
    program: Program, manifest: Record, *, budget: ReadBudget | None = None
) -> list[Json]:
    """Verify the retained copy graph; host separately checks consuming subject and manifest hash."""
    core = _core()
    _shape(
        manifest,
        {
            "schema_version",
            "kind",
            "program_id",
            "task_id",
            "attempt",
            "selection_digest",
            "attachments",
            "mappings",
        },
        set(),
        "provenance manifest",
    )
    _version(manifest, "evidence_provenance")
    _text(manifest["program_id"], "manifest program")
    task_id = _text(manifest["task_id"], "manifest task")
    _require(core.portable_task_id(task_id) or task_id == "_coordination", "Invalid manifest task")
    number = manifest["attempt"]
    _require(type(number) is int and number > 0, "Invalid manifest attempt")
    _require(
        bool(re.fullmatch(r"[a-f0-9]{64}", str(manifest["selection_digest"]))),
        "Invalid selection digest",
    )
    prefix = f"evidence/{task_id}/attempt-{number}/"
    reader = budget if budget is not None else core.ReadBudget(READ_LIMIT)
    refs: list[Json] = []
    keys: set[str] = set()
    destinations: set[str] = set()
    rows = _rows(manifest["attachments"], "provenance attachments")
    _require(bool(rows), "Provenance requires attachments")
    for raw in rows:
        row = _record(raw, "provenance attachment")
        _shape(row, {"key", "origin_ref", "attachment_ref", "byte_count", "role"}, set(), "copy")
        key = _text(row["key"], "copy key")
        _require(key not in keys and row["role"] in ROLES, "Duplicate copy key or invalid role")
        keys.add(key)
        origin, target = _ref(row["origin_ref"]), _ref(row["attachment_ref"])
        folded = str(target["path"]).casefold()
        _require(
            str(target["path"]).startswith(prefix)
            and target["path"] != origin["path"]
            and folded not in destinations,
            "Invalid or duplicate scoped copy",
        )
        destinations.add(folded)
        _require(
            type(row["byte_count"]) is int and 0 <= row["byte_count"] <= LIMIT,
            "Invalid copied byte count",
        )
        original, copied = _read(program, origin, reader), _read(program, target, reader)
        _require(
            origin["sha256"] == target["sha256"]
            and original == copied
            and len(copied) == row["byte_count"],
            "Copied bytes differ from original",
        )
        if row["role"] != "artifact":
            _json(program, origin, reader)
        refs.extend([origin, target])
    _mappings(manifest["mappings"], keys)
    return refs


def materialize_evidence(program: Program, state: Record, plan: Record) -> Record:
    """Delegate exact inert copies; never overwrite originals, invoke checks or mutate state."""
    core = _core()
    _version(plan, "evidence_plan")
    fresh = plan_evidence(program, state, _record(plan["selection"], "planned selection"))
    _require(fresh == plan, "Evidence selection, bytes or target binding changed")
    rows: list[Json] = []
    for value in plan["targets"]:
        target = _record(value, "planned target")
        binding = target["binding"]
        for raw in target["attachments"]:
            row = _record(raw, "planned copy")
            rows.append(
                {
                    "origin_ref": row["origin_ref"],
                    "destination": row["attachment_ref"]["path"],
                    "task_id": binding["task_id"],
                    "attempt": binding["attempt"],
                }
            )
        # A conflicting manifest is diagnosed before any copy is materialized.
        manifest = {
            "schema_version": 1,
            "kind": "evidence_provenance",
            "program_id": plan["program_id"],
            "task_id": binding["task_id"],
            "attempt": binding["attempt"],
            "selection_digest": plan["selection_digest"],
            "attachments": deepcopy(target["attachments"]),
            "mappings": deepcopy(target["mappings"]),
        }
        encoded = core.canonical(manifest)
        path = program.path(str(target["manifest_path"]))
        _require(
            not path.exists() or core.bounded_bytes(path, LIMIT) == encoded,
            "Provenance manifest destination conflicts",
        )
    _closure().prepare_attachments(program, {"attachments": rows}, materialize=True)
    targets: list[Json] = []
    for value in plan["targets"]:
        target = _record(value, "planned target")
        binding = target["binding"]
        manifest = {
            "schema_version": 1,
            "kind": "evidence_provenance",
            "program_id": plan["program_id"],
            "task_id": binding["task_id"],
            "attempt": binding["attempt"],
            "selection_digest": plan["selection_digest"],
            "attachments": deepcopy(target["attachments"]),
            "mappings": deepcopy(target["mappings"]),
        }
        validate_manifest(program, manifest)
        encoded = core.canonical(manifest)
        path = str(target["manifest_path"])
        _closure()._exact_write(program.path(path), encoded)
        ref = {"path": path, "sha256": core.sha(encoded)}
        targets.append(
            {
                "binding": deepcopy(binding),
                "manifest_ref": ref,
                "attachments": deepcopy(target["attachments"]),
                "mappings": deepcopy(target["mappings"]),
                "evidence": [
                    path,
                    *[
                        str(row["attachment_ref"]["path"])
                        for row in target["attachments"]
                        if row["byte_count"] > 0
                    ],
                ],
            }
        )
    # Recheck originals and source/attempt bindings after all inert materialization.
    _require(
        plan_evidence(program, state, plan["selection"]) == plan,
        "Evidence changed during materialization",
    )
    return _bounded(
        {
            "schema_version": 1,
            "kind": "evidence_materialization",
            "program_id": plan["program_id"],
            "selection_digest": plan["selection_digest"],
            "targets": targets,
            "status": "ready",
            "accepted": False,
        }
    )


def _verification_binding(program: Program, state: Record, task_ids: set[str]) -> str:
    """Use existing check applicability, including declared broader inputs and conservative unknowns."""
    core = _core()
    reads = core.verification._reads()
    reads.index(state)
    facts: list[Json] = []
    for (task_id, lane), request in sorted(reads.latest_lane.items()):
        if task_id not in task_ids:
            continue
        latest = reads.latest_event[str(request["request_id"])]
        try:
            applicable, reasons = core.verification._applicable(program, state, latest, reads)
            problems = (
                core.verification._observation_problems(latest, request)
                if latest["input"]["kind"] == "finished"
                else []
            )
        except (
            OSError,
            core.ProgramError,
            core.planning.PlanningError,
            core.coverage.CoverageError,
        ) as exc:
            applicable, reasons, problems = False, [str(exc)], ["unavailable"]
        facts.append(
            {
                "task_id": task_id,
                "lane": lane,
                "request_id": request["request_id"],
                "observation_id": latest["input"]["id"],
                "applicable": applicable,
                "reasons": reasons,
                "problems": problems,
            }
        )
    reads.recheck(program)
    return core.digest(facts)


def _subject(
    program: Program, state: Record, operation: str, task_id: str | None, scope: list[str]
) -> Record:
    core = _core()
    if operation in {"integrated_review", "completion"}:
        goal = _record(state.get("objective"), "current objective")
        _require(bool(scope), "Aggregate preparation requires explicit complete scope")
        return {
            "program_id": state["program_id"],
            "objective_id": goal["id"],
            "membership_revision": goal.get("membership_revision"),
            "scope": sorted(set(scope)),
            "source_head": program.current_head(),
            "source_digest": program.source_digest(scope),
            "criteria": deepcopy(goal["criteria"]),
            "verification_applicability_digest": _verification_binding(
                program, state, set(goal["task_ids"])
            ),
            "task_acceptance_digest": core.digest(
                [
                    {
                        key: deepcopy(task.get(key))
                        for key in ("id", "status", "attempts", "deliveries")
                    }
                    for task in state["tasks"]
                    if task["id"] in goal["task_ids"]
                ]
            ),
        }
    _require(task_id is not None, "Task preparation requires task_id")
    task = program.task(state, task_id)
    attempt = program.current_attempt(task)
    binding = _target_binding(program, state, task_id, attempt["number"])
    submission = attempt.get("submission")
    binding["verification_applicability_digest"] = _verification_binding(program, state, {task_id})
    binding["verification_history_digest"] = core.digest(
        [
            row
            for row in state.get("verification", {}).get("records", [])
            if row["input"]["task_id"] == task_id
        ]
    )
    binding["acceptance_state_digest"] = core.digest(
        {
            "status": task["status"],
            "review": attempt.get("review"),
            "deliveries": task.get("deliveries", []),
        }
    )
    binding["submission"] = None
    if submission is not None:
        binding["submission"] = {
            key: deepcopy(submission[key]) for key in ("receipt", "source_digest", "round")
        }
    return binding


def _decisions(program: Program, refs: Json, subject: Record) -> tuple[list[Record], list[Json]]:
    core = _core()
    budget = core.ReadBudget(READ_LIMIT)
    decisions: list[Record] = []
    selected: list[Json] = []
    for raw in _rows(refs, "actual decision references"):
        ref = _ref(raw)
        _read(program, ref, budget)
        decision = _json(program, ref, budget)
        _shape(
            decision,
            {"schema_version", "kind", "subject_binding", "actor_id", "facts"},
            set(),
            "actual decision",
        )
        _version(decision, "evidence_decision")
        _require(decision["subject_binding"] == subject, "Actual decision subject changed")
        actor = _text(decision["actor_id"], "actual decision actor")
        facts = _record(decision["facts"], "actual decision facts")
        decisions.append({"actor_id": actor, "facts": deepcopy(facts)})
        selected.append(ref)
    return decisions, selected


def _materialized_target(
    program: Program, state: Record, value: Json, task_id: str, index: Json
) -> Record:
    materialized = _record(value, "selected materialization")
    _version(materialized, "evidence_materialization")
    _require(materialized["program_id"] == state["program_id"], "Evidence program changed")
    _require(
        type(index) is int and 0 <= index < len(materialized["targets"]), "Invalid target index"
    )
    target = _record(materialized["targets"][index], "selected evidence target")
    binding = _record(target["binding"], "target binding")
    _require(binding["task_id"] == task_id, "Evidence task changed")
    _require(
        _target_binding(program, state, task_id, binding["attempt"]) == binding,
        "Materialized source, attempt or obligations changed",
    )
    ref = _ref(target["manifest_ref"])
    budget = _core().ReadBudget(READ_LIMIT)
    _read(program, ref, budget)
    manifest = _json(program, ref, budget)
    _require(
        manifest["program_id"] == state["program_id"]
        and manifest["task_id"] == task_id
        and manifest["attempt"] == binding["attempt"],
        "Manifest target binding changed",
    )
    validate_manifest(program, manifest, budget=budget)
    _require(
        manifest["attachments"] == target["attachments"]
        and manifest["mappings"] == target["mappings"],
        "Selected copy graph changed",
    )
    evidence = [
        ref["path"],
        *[
            row["attachment_ref"]["path"]
            for row in manifest["attachments"]
            if row["byte_count"] > 0
        ],
    ]
    return {**deepcopy(target), "evidence": evidence}


def _merge_decisions(decisions: list[Record], allowed: set[str], actor_field: str | None) -> Record:
    result: Record = {}
    actors = {str(item["actor_id"]) for item in decisions}
    _require(len(actors) <= 1, "Decisions disagree on responsible actor")
    for decision in decisions:
        facts = decision["facts"]
        _require(set(facts) <= allowed, "Decision cannot override mechanical binding")
        for key, value in facts.items():
            _require(key not in result or result[key] == value, "Conflicting actual decision facts")
            result[key] = deepcopy(value)
    if decisions and actor_field is not None:
        actor = decisions[0]["actor_id"]
        _require(
            actor_field not in result or result[actor_field] == actor, "Decision actor changed"
        )
        result[actor_field] = actor
    return result


def _mapped_paths(value: Json, mapping: dict[str, str]) -> Json:
    """Adapt only schema-declared evidence locators; never rewrite prose or execution fields."""
    match value:
        case dict() as record:
            result = deepcopy(record)
            for key, item in record.items():
                if key == "evidence":
                    result[key] = [
                        mapping.get(name, name) for name in _core().strings(item, "evidence")
                    ]
                elif key == "integrated_review" and item is not None:
                    result[key] = mapping.get(str(item), item)
                elif key == "cleanup" and isinstance(item, dict):
                    result[key] = _mapped_paths(item, mapping)
                elif key in {"acceptance_evidence", "criteria_results"}:
                    result[key] = [_mapped_paths(row, mapping) for row in _rows(item, key)]
            return result
        case _:
            return deepcopy(value)


def _retained_observation(
    program: Program, state: Record, inputs: Record, target: Record | None
) -> tuple[Record, Record, list[Json]]:
    """Interpret only explicitly selected structured facts; missing input closure never passes."""
    core = _core()
    ref = _ref(inputs["outcome_ref"])
    reader = core.ReadBudget(READ_LIMIT)
    raw = _read(program, ref, reader)

    def unique(pairs: list[tuple[str, Json]]) -> Record:
        result: Record = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate retained JSON key")
            result[key] = value
        return result

    def finite_constant(value: str) -> None:
        raise core.ProgramError(f"Nonfinite retained JSON value: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        _require(math.isfinite(number), "Nonfinite retained JSON number")
        return number

    try:
        original = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=finite_constant,
            parse_float=finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise core.ProgramError("Invalid retained structured outcome") from exc
    index = inputs.get("outcome_index", 0)
    _require(type(index) is int and index >= 0, "Invalid retained outcome index")
    match original:
        case list() as values:
            _require(
                len(values) <= MAX_ROWS and index < len(values),
                "Retained outcome index unavailable",
            )
            actual = _record(values[index], "retained actual outcome")
        case dict() as actual:
            _require(index == 0, "Object outcome has no indexed successor")
        case _:
            raise core.ProgramError("Retained outcome requires explicit structured facts")
    related = {
        "origin_ref": ref,
        "index": index,
        "actual": deepcopy(actual),
        "input_completeness": "unknown",
        "actual_executor_id": actual.get("actor_id"),
    }
    missing: list[Json] = []
    binding = _record(inputs.get("request_binding"), "retained record binding")
    required = {
        "observation_id",
        "actor_id",
        "request_id",
        "request_version",
        "reason",
        "execution_kind",
    }
    _require(required <= binding.keys(), "Retained record requires explicit actual attribution")
    _require(
        binding["execution_kind"] in {"native", "test", "static", "manual", "compile"},
        "Invalid retained method interpretation",
    )
    if not {"argv", "cwd", "exit_code"} <= actual.keys() or any(
        actual.get(key) is None for key in ("argv", "cwd", "exit_code")
    ):
        return {}, {"retained_outcome": related}, ["retained_method_or_outcome"]
    argv = _rows(actual["argv"], "actual retained argv")
    _require(
        bool(argv) and all(type(arg) is str for arg in argv) and bool(argv[0].strip()),
        "Invalid original retained argv",
    )
    _text(actual["cwd"], "original retained cwd")
    _require(type(actual["exit_code"]) is int, "Original exit code is not known")
    selected = [row["origin_ref"] for row in target["attachments"]] if target else []
    if ref not in selected:
        missing.append("outcome_attachment")
    streams = _record(inputs.get("outcome_streams", {}), "selected retained streams")
    _require(set(streams) <= {"stdout", "stderr"}, "Unknown retained output stream")
    for name, value in streams.items():
        stream_ref = _ref(value)
        _read(program, stream_ref, reader)
        _require(
            actual.get(name + "_sha256") == stream_ref["sha256"], "Retained output hash differs"
        )
        if stream_ref not in selected:
            missing.append("outcome_stream_attachment")
    related["streams"] = deepcopy(streams)
    receipt: Record = {
        "schema_version": 1,
        "operation": "verification-record",
        "kind": "finished",
        "id": binding["observation_id"],
        "task_id": inputs["task_id"],
        "actor_id": binding["actor_id"],
        "expected_revision": core.phases.revision(state),
        "reason": binding["reason"],
        "request_id": binding["request_id"],
        "request_version": binding["request_version"],
        "result": "failed" if actual["exit_code"] != 0 else "unknown",
        "method": {
            "argv": deepcopy(argv),
            "cwd": actual["cwd"],
            "execution_kind": binding["execution_kind"],
        },
        "exit_code": actual["exit_code"],
        "artifacts": deepcopy(target["evidence"]) if target else [],
        "classification": "Retained outcome; original input closure is unknown",
    }
    if actual.get("ended_at") is not None and actual.get("clock_id") is not None:
        receipt.update(observed_at=actual["ended_at"], clock_id=actual["clock_id"])
    return receipt, {"retained_outcome": related}, missing


def _aggregate_provider_ci(
    program: Program, provider: Record, context_value: Json, head: Json
) -> Record:
    """Adapt only explicitly assessed receiving-beta jobs, preserving actual rows and outcomes."""
    if context_value is None or provider.get("execution") is None:
        return {
            "ci": [],
            "required_ci": [],
            "missing_fields": ["assessed_receiving_beta_context"],
            "assessment": None,
        }
    core = _core()
    context = _record(context_value, "actual receiving-beta context")
    execution = _record(provider["execution"], "actual provider execution")
    assessment = core.authoring.provider_delivery.assess_execution(execution, context)
    _require(context["beta_sha"] == head, "Provider beta context is not the current receiving head")
    plans = [
        _record(row, "provider stage")
        for row in _rows(context["stages"], "provider stages")
        if _record(row, "provider stage")["kind"] == "beta_ci"
    ]
    _require(bool(plans), "PR-only observation cannot supply objective receiving-beta CI")
    expected_jobs: dict[str, Record] = {}
    captured = {row["id"]: row for row in execution["stages"]}
    projected = {row["id"]: row for row in assessment["stages"]}
    missing: list[Json] = []
    for plan in plans:
        _require(plan["expected_sha"] == head, "Beta CI tests another receiving head")
        actual = projected[plan["id"]]
        if actual["current"] is not True or actual["status"] != "passed":
            missing.append("actual_receiving_beta_execution")
        observations = captured[plan["id"]]["observations"]
        run = observations[-1] if observations else None
        _require(run is not None, "Receiving-beta run is missing")
        _require(
            run["event"] == "push" and run["head_sha"] == head and run["checkout_sha"] == head,
            "Receiving-beta run has another source/event identity",
        )
        jobs = {row["name"]: row for row in run["jobs"]}
        for check in plan["checks"]:
            name = _text(check["job"], "required beta job")
            _require(
                check["mode"] == "required",
                "Objective CI requires actual executed jobs, not exclusions",
            )
            _require(
                name not in expected_jobs and name in jobs,
                "Required beta job is duplicate or missing",
            )
            expected_jobs[name] = jobs[name]
    ci = _rows(provider.get("ci"), "actual aggregate CI rows")
    names: set[str] = set()
    for raw in ci:
        row = _record(raw, "actual aggregate CI row")
        _shape(row, {"name", "url", "commit", "conclusion"}, set(), "aggregate CI row")
        name = _text(row["name"], "actual aggregate CI name")
        _text(row["url"], "actual aggregate CI URL")
        _require(name not in names and name in expected_jobs, "Actual aggregate CI job set differs")
        names.add(name)
        _require(
            row["commit"] == head and row["conclusion"] == expected_jobs[name]["conclusion"],
            "Actual aggregate CI row differs from receiving-beta execution",
        )
    _require(
        names == set(expected_jobs), "Actual aggregate CI is missing required receiving-beta jobs"
    )
    return {
        "ci": deepcopy(ci),
        "required_ci": sorted(names),
        "missing_fields": list(dict.fromkeys(missing)),
        "assessment": assessment,
    }


def prepare_receipt(
    program: Program,
    state: Record,
    operation: str,
    explicit_inputs: Record,
    *,
    adapter: ReceiptAdapter | None = None,
) -> Record:
    """Compose a normal draft from exact decisions; only an actual host assessment can ready it."""
    import argparse

    core = _core()
    _require(operation in OPERATIONS, "Unsupported receipt preparation operation")
    _shape(
        explicit_inputs,
        set(),
        {
            "task_id",
            "scope",
            "owner",
            "materialization",
            "target_index",
            "decision_refs",
            "observation_ids",
            "provider_ref",
            "provider_context",
            "capture_ref",
            "request_binding",
            "outcome_ref",
            "outcome_index",
            "outcome_streams",
        },
        "receipt preparation",
    )
    task_id = explicit_inputs.get("task_id")
    if task_id is not None:
        task_id = _text(task_id, "receipt task")
    owner = explicit_inputs.get("owner")
    if owner is not None:
        owner = _text(owner, "explicit acting owner")
    scope = core.strings(explicit_inputs.get("scope", []), "declared review scope")
    subject = _subject(program, state, operation, task_id, scope)
    if task_id is not None and scope:
        subject["review_scope"] = sorted(set(scope))
        subject["review_source_digest"] = program.source_digest(scope)
    decisions, decision_refs = _decisions(
        program, explicit_inputs.get("decision_refs", []), subject
    )
    missing: list[Json] = []
    target: Record | None = None
    aggregate = operation in {"integrated_review", "completion"}
    target_id = "_coordination" if aggregate else str(task_id)
    if explicit_inputs.get("materialization") is not None:
        target = _materialized_target(
            program,
            state,
            explicit_inputs["materialization"],
            target_id,
            explicit_inputs.get("target_index", 0),
        )
    else:
        missing.append("materialization")
    mapping = (
        {
            str(row["origin_ref"]["path"]): str(row["attachment_ref"]["path"])
            for row in target["attachments"]
        }
        if target
        else {}
    )
    if target is not None:
        selected_refs = [row["origin_ref"] for row in target["attachments"]]
        if not all(ref in selected_refs for ref in decision_refs):
            missing.append("decision_attachments")
    receipt: Record = {}
    related: Record = {}
    match operation:
        case "submission":
            facts = _merge_decisions(
                decisions,
                {
                    "summary",
                    "next_action",
                    "limitations",
                    "acceptance_complete",
                    "acceptance_evidence",
                },
                None,
            )
            if not decisions:
                missing.append("actual_acceptance_decision")
            request = {
                "task_id": task_id,
                "owner": owner,
                "observation_ids": explicit_inputs.get("observation_ids", []),
                **_mapped_paths(facts, mapping),
            }
            if owner is None:
                missing.append("acting_owner")
                request["owner"] = subject["owner"]
            prepared = core.verification.submission_inputs(program, state, request)
            receipt = prepared["receipt"]
            missing.extend(prepared["missing_fields"])
            if decisions:
                _require(
                    decisions[0]["actor_id"] == owner, "Acceptance decision is not acting owner"
                )
        case "review":
            receipt = program.prepare_template(
                state, argparse.Namespace(id=task_id, kind="review", scope=[])
            )
            facts = _merge_decisions(
                decisions,
                {
                    "verdict",
                    "reviewer",
                    "reviewer_role",
                    "reason",
                    "acceptance_complete",
                    "unresolved_findings",
                    "next_action",
                    "evidence",
                },
                "reviewer",
            )
            receipt.update(_mapped_paths(facts, mapping))
            if not decisions:
                receipt["reviewer"] = None
                missing.append("actual_reviewer_decision")
            if not scope:
                missing.append("declared_review_scope")
        case "integrated_review":
            scaffold = core.authoring.scaffold(
                program,
                state,
                argparse.Namespace(
                    operation="objective-complete", kind=None, task=None, scope=scope
                ),
            )
            receipt = deepcopy(scaffold["related_inputs"]["integrated_review"])
            facts = _merge_decisions(
                decisions,
                {
                    "reviewer",
                    "reviewer_role",
                    "verdict",
                    "acceptance_complete",
                    "unresolved_findings",
                    "criteria",
                    "evidence",
                },
                "reviewer",
            )
            receipt.update(_mapped_paths(facts, mapping))
            if not decisions:
                missing.append("actual_independent_aggregate_decision")
        case "completion":
            facts = _merge_decisions(
                decisions,
                {
                    "actor_id",
                    "summary",
                    "criteria_results",
                    "remote_state_checked",
                    "required_ci",
                    "ci",
                    "integrated_review",
                    "unresolved_findings",
                    "evidence",
                },
                "actor_id",
            )
            prepared = _closure().prepare_completion_inputs(
                program, state, {"scope": scope, "facts": _mapped_paths(facts, mapping)}
            )
            receipt, related = prepared["receipt"], prepared["related_inputs"]
            if not decisions:
                missing.append("actual_director_decision")
            if explicit_inputs.get("provider_ref") is not None:
                provider_ref = _ref(explicit_inputs["provider_ref"])
                if target is None or provider_ref not in [
                    row["origin_ref"] for row in target["attachments"]
                ]:
                    missing.append("provider_attachment")
                provider = core.authoring.provider_observation(program, provider_ref)
                actual = _aggregate_provider_ci(
                    program,
                    provider,
                    explicit_inputs.get("provider_context"),
                    subject["source_head"],
                )
                ci = actual["ci"]
                missing.extend(actual["missing_fields"])
                related["provider_assessment"] = actual["assessment"]
                if not actual["missing_fields"] or ci:
                    _require(
                        "ci" not in facts or facts["ci"] == ci,
                        "Director CI differs from actual observation",
                    )
                    _require(
                        "required_ci" not in facts
                        or set(facts["required_ci"]) == set(actual["required_ci"]),
                        "Director required CI differs from assessed receiving-beta jobs",
                    )
                    receipt["ci"] = deepcopy(ci)
                    receipt["required_ci"] = deepcopy(actual["required_ci"])
        case "delivery":
            facts = _merge_decisions(decisions, {"required_ci", "cleanup", "evidence"}, None)
            if explicit_inputs.get("provider_ref") is None:
                missing.append("actual_provider_observation")
            if not decisions:
                missing.append("actual_delivery_decision")
            if explicit_inputs.get("provider_ref") is not None and target is not None:
                if _ref(explicit_inputs["provider_ref"]) not in [
                    row["origin_ref"] for row in target["attachments"]
                ]:
                    missing.append("provider_attachment")
                binding = {
                    "task_id": task_id,
                    **_mapped_paths(facts, mapping),
                    "evidence": target["evidence"],
                }
                if explicit_inputs.get("provider_context") is not None:
                    binding["provider_context"] = explicit_inputs["provider_context"]
                prepared = _closure().prepare_delivery_inputs(
                    program,
                    state,
                    {"provider": _ref(explicit_inputs["provider_ref"]), "tasks": [binding]},
                )
                receipt = prepared["inputs"][0]["receipt"]
        case "observation":
            _require(
                not (
                    explicit_inputs.get("capture_ref") is not None
                    and explicit_inputs.get("outcome_ref") is not None
                ),
                "Select one original outcome producer",
            )
            if explicit_inputs.get("outcome_ref") is not None:
                receipt, related, problems = _retained_observation(
                    program, state, explicit_inputs, target
                )
                missing.extend(problems)
            elif (
                explicit_inputs.get("capture_ref") is None
                or explicit_inputs.get("request_binding") is None
            ):
                missing.append("actual_capture_and_request_binding")
            else:
                receipt = core.flow_module("execution").prepare_observation(
                    program,
                    state,
                    _ref(explicit_inputs["capture_ref"]),
                    _record(explicit_inputs["request_binding"], "actual request binding"),
                )
        case _:
            raise core.ProgramError("Unsupported receipt preparation operation")
    if target is not None and receipt:
        receipt["evidence_provenance_ref"] = deepcopy(target["manifest_ref"])
        if operation != "submission" and operation != "observation":
            receipt["evidence"] = sorted(
                set(
                    core.strings(receipt.get("evidence", []), "receipt evidence")
                    + core.strings(target["evidence"], "selected evidence")
                )
            )
        for field in ("evidence",):
            for name in core.strings(receipt.get(field, []), field):
                _require(
                    name.startswith(
                        f"evidence/{target_id}/attempt-{target['binding']['attempt']}/"
                    ),
                    "Decision evidence was not attached to the consuming target",
                )
    status = "incomplete"
    reasons: list[Json] = ["missing_decision"] if missing else ["normal_validation_required"]
    assessment: Record | None = None
    if receipt and not missing and adapter is not None:
        assessment = adapter.assess(program, state, operation, deepcopy(receipt), owner=owner)
        _shape(assessment, {"status", "missing_fields", "reasons"}, set(), "normal assessment")
        _require(
            assessment["status"] in {"ready", "incomplete", "stale", "unavailable"},
            "Invalid normal assessment status",
        )
        missing.extend(_rows(assessment["missing_fields"], "normal missing fields"))
        status = str(assessment["status"])
        _require(
            status != "ready" or not missing, "Normal validation returned contradictory readiness"
        )
        reasons = (
            ["stale_binding"]
            if status == "stale"
            else ["unavailable_evidence"]
            if status == "unavailable"
            else ["normal_validation_required"]
            if status == "incomplete"
            else []
        )
    elif not missing:
        missing.append("normal_validation")
    return _bounded(
        {
            "schema_version": 1,
            "kind": "receipt_preparation",
            "operation": operation,
            "task_id": task_id,
            "binding": subject,
            "receipt": receipt,
            "related_inputs": related,
            "decision_refs": decision_refs,
            "materialization": deepcopy(explicit_inputs.get("materialization")),
            "status": status,
            "missing_fields": missing,
            "reasons": reasons,
            "assessment": assessment,
            "next_actor_id": owner,
            "accepted": False,
            "executed": False,
            "remote_verified_by_tool": False,
        }
    )


def summarize_preparation(full: Record) -> Record:
    """Return finite safe preparation facts, never raw paths, invocation or claimed new outcomes."""
    status = str(full.get("status", "unavailable"))
    _require(
        status in {"ready", "incomplete", "stale", "unavailable"}, "Invalid preparation status"
    )
    materialized = full.get("kind") == "evidence_materialization"
    source = full.get("materialization") or full
    targets = _rows(_record(source, "preparation source").get("targets", []), "preparation targets")
    copies = [row for target in targets for row in target["attachments"]]
    origins = {_core().digest(row["origin_ref"]) for row in copies}
    checks = sorted(
        {
            str(item["identity"])
            for target in targets
            for item in target["mappings"]
            if item["family"] == "check"
        }
    )
    missing = _rows(full.get("missing_fields", []), "missing preparation fields")
    ready_copies = len(copies) if materialized or full.get("materialization") is not None else 0
    codes = {
        "missing_decision",
        "missing_materialization",
        "normal_validation_required",
        "stale_binding",
        "unavailable_evidence",
        "invalid_preparation",
    }
    reasons = [reason for reason in full.get("reasons", []) if reason in codes]
    return {
        "schema_version": 1,
        "kind": full.get("kind"),
        "task_id": full.get("task_id"),
        "operation": full.get("operation"),
        "status": status,
        "counts": {
            "selected": len(origins),
            "retained": ready_copies,
            "attachments_planned": len(copies),
            "attachments_ready": ready_copies,
            "unresolved": len(missing) or int(status == "incomplete"),
        },
        "affected_checks": checks,
        "next_actor_id": full.get("next_actor_id"),
        "next_action": {
            "ready": "Inspect ordinary receipt preparation",
            "incomplete": "Supply missing decisions"
            if missing
            else "Inspect ordinary receipt preparation",
            "stale": "Refresh changed bindings",
            "unavailable": "Inspect unavailable evidence",
        }[status],
        "reasons": reasons,
    }


def project_retained(state: Record, manifests: list[Record]) -> Record | None:
    """Summarize complete host-validated graph facts; legacy absence creates no obligation."""
    if not manifests:
        return None
    _require(len(manifests) <= MAX_ROWS, "Too many retained manifests")
    targets = []
    currentness = []
    tasks = set()
    seen = set()
    for row in manifests:
        _shape(
            row,
            {"manifest_ref", "manifest", "control_ref", "task_id", "attempt", "current"},
            set(),
            "assessed retained evidence",
        )
        _ref(row["manifest_ref"])
        _ref(row["control_ref"])
        _require(
            type(row["current"]) is bool or row["current"] is None, "Invalid retained currentness"
        )
        manifest = _record(row["manifest"], "validated retained manifest")
        _version(manifest, "evidence_provenance")
        _require(
            manifest["program_id"] == state["program_id"]
            and manifest["task_id"] == row["task_id"]
            and manifest["attempt"] == row["attempt"],
            "Assessed retained subject changed",
        )
        currentness.append(row["current"])
        tasks.add(str(row["task_id"]))
        key = _core().digest(row["manifest_ref"])
        if key not in seen:
            targets.append(manifest)
            seen.add(key)
    status = "unavailable" if None in currentness else "stale" if False in currentness else "ready"
    summary = summarize_preparation(
        {
            "schema_version": 1,
            "kind": "evidence_materialization",
            "targets": targets,
            "status": status,
            "reasons": [],
        }
    )
    summary.update(
        kind="retained_evidence",
        operation="retained_evidence",
        task_id=next(iter(tasks)) if len(tasks) == 1 else None,
        next_action="Inspect selected evidence",
    )
    summary["reasons"] = (
        ["unavailable_evidence"]
        if status == "unavailable"
        else ["stale_binding"]
        if status == "stale"
        else []
    )
    return summary


def _selection_materialization(
    program: Program, state: Record, value: Record, reader: ReadBudget
) -> tuple[list[Json], bool]:
    _shape(
        value,
        {
            "schema_version",
            "kind",
            "program_id",
            "selection_digest",
            "targets",
            "status",
            "accepted",
        },
        set(),
        "materialization result",
    )
    _version(value, "evidence_materialization")
    _require(
        value["program_id"] == state["program_id"], "Selected evidence belongs to another program"
    )
    _require(
        value["status"] == "ready" and value["accepted"] is False,
        "Invalid materialization disposition",
    )
    refs: list[Json] = []
    current = True
    targets = _rows(value["targets"], "materialized targets")
    _require(bool(targets), "Materialization requires targets")
    for raw in targets:
        target = _record(raw, "materialized target")
        _shape(
            target,
            {"binding", "manifest_ref", "attachments", "mappings", "evidence"},
            set(),
            "materialized target",
        )
        binding = _record(target["binding"], "materialized binding")
        _require(
            {"program_id", "task_id", "attempt"} <= binding.keys(), "Incomplete selected binding"
        )
        ref = _ref(target["manifest_ref"])
        _read(program, ref, reader)
        manifest = _json(program, ref, reader)
        graph_refs = validate_manifest(program, manifest, budget=reader)
        _require(
            manifest["program_id"] == state["program_id"]
            and manifest["task_id"] == binding["task_id"]
            and manifest["attempt"] == binding["attempt"]
            and manifest["selection_digest"] == value["selection_digest"],
            "Selected manifest subject differs",
        )
        refs.extend([ref, *graph_refs])
        _require(
            target["attachments"] == manifest["attachments"]
            and target["mappings"] == manifest["mappings"],
            "Selected graph differs from manifest",
        )
        expected_evidence = [
            ref["path"],
            *[
                row["attachment_ref"]["path"]
                for row in manifest["attachments"]
                if row["byte_count"] > 0
            ],
        ]
        _require(target["evidence"] == expected_evidence, "Selected target evidence differs")
        try:
            current = (
                _target_binding(program, state, str(binding["task_id"]), binding["attempt"])
                == binding
            ) and current
        except _core().ProgramError:
            current = False
    return refs, current


def _current_receipt_authority(
    program: Program, state: Record, operation: str, receipt: Record, owner: Json
) -> bool:
    """Recheck only current normal authority; saved assessment is not present permission.

    This read path must not call the ordinary assessor, which materializes inert
    files. Existing authority helpers remain the source of participant/retirement
    rules; ordinary review independence uses the same recorded director/owners.
    """
    core = _core()
    try:
        if operation == "completion":
            actor = _text(receipt.get("actor_id"), "original completion actor")
            _require(
                actor == core.coordination.current_director_id(state), "Final director changed"
            )
            core.coordination.require_task_authority(state, actor)
        elif operation == "integrated_review":
            reviewer = _text(receipt.get("reviewer"), "original integrated reviewer")
            implementers = {
                str(attempt["owner"]) for task in state["tasks"] for attempt in task["attempts"]
            }
            _require(
                reviewer != core.coordination.current_director_id(state)
                and reviewer not in implementers,
                "Integrated reviewer is no longer independent",
            )
        elif operation in {"submission", "review", "observation"}:
            task = program.task(state, _text(receipt.get("task_id"), "original receipt task"))
            attempt = program.current_attempt(task)
            if operation == "submission":
                core.coordination.require_task_authority(
                    state,
                    _text(owner, "original acting owner"),
                    _text(task.get("owner"), "current task owner"),
                )
            elif operation == "review":
                reviewer = _text(receipt.get("reviewer"), "original reviewer")
                if receipt.get("reviewer_role") == "leader" or reviewer == attempt["owner"]:
                    core.coordination.require_task_authority(state, reviewer)
            else:
                request = core.verification._index(state).get(str(receipt.get("request_id")))
                _require(request is not None, "Original request unavailable")
                core.verification._authority(state, task, receipt, request)
        return True
    except (core.ProgramError, core.coordination.GovernanceError):
        return False


def validate_selection(
    program: Program, state: Record, value: Record, *, budget: ReadBudget | None = None
) -> Record:
    """Assess one explicit saved producer envelope; never interpret arbitrary evidence as controls.

    Currentness is separate from original result bytes. The safe preparation status
    grants no check result, actor decision, canonical acceptance or publication.
    The caller freezes the envelope reference and rechecks these exact typed refs.
    """
    core = _core()
    _bounded(value)
    reader = budget if budget is not None else core.ReadBudget(READ_LIMIT)
    kind = value.get("kind")
    refs: list[Json] = []
    current = True
    if kind == "evidence_plan":
        _shape(
            value,
            {
                "schema_version",
                "kind",
                "program_id",
                "selection",
                "selection_digest",
                "targets",
                "status",
                "accepted",
                "missing_fields",
                "reasons",
            },
            set(),
            "plan result",
        )
        _version(value, "evidence_plan")
        _require(
            value["program_id"] == state["program_id"]
            and value["accepted"] is False
            and value["status"] == "ready"
            and value["missing_fields"] == []
            and value["reasons"] == [],
            "Invalid selected plan disposition",
        )
        selection = _record(value["selection"], "selected plan input")
        # Validate the original graph even when its task attempt is no longer current.
        old_targets = _rows(value["targets"], "saved plan targets")
        bindings = [
            _record(_record(row, "saved target").get("binding"), "saved target binding")
            for row in old_targets
        ]
        fresh = _plan_evidence(program, state, selection, budget=reader, bindings=bindings)
        refs.extend(_ref(row["origin_ref"]) for row in selection["origins"])
        comparable = deepcopy(value)
        old_targets = _rows(value["targets"], "saved plan targets")
        _require(len(old_targets) == len(fresh["targets"]), "Malformed saved plan targets")
        for old, new in zip(old_targets, fresh["targets"], strict=True):
            _shape(
                _record(old, "saved target"),
                {"binding", "manifest_path", "attachments", "mappings"},
                set(),
                "saved target",
            )
            _record(old["binding"], "saved target binding")
            _require({**old, "binding": new["binding"]} == new, "Saved plan graph differs")
        comparable["targets"] = fresh["targets"]
        _require(comparable == fresh, "Malformed saved plan")
        for binding in bindings:
            try:
                current = (
                    _target_binding(program, state, str(binding["task_id"]), binding["attempt"])
                    == binding
                ) and current
            except core.ProgramError:
                current = False
    elif kind == "evidence_materialization":
        refs, current = _selection_materialization(program, state, value, reader)
    elif kind == "receipt_preparation":
        _shape(
            value,
            {
                "schema_version",
                "kind",
                "operation",
                "task_id",
                "binding",
                "receipt",
                "related_inputs",
                "decision_refs",
                "materialization",
                "status",
                "missing_fields",
                "reasons",
                "assessment",
                "next_actor_id",
                "accepted",
                "executed",
                "remote_verified_by_tool",
            },
            set(),
            "receipt preparation result",
        )
        _version(value, "receipt_preparation")
        operation = value["operation"]
        _require(operation in OPERATIONS, "Invalid selected receipt operation")
        _require(
            all(value[key] is False for key in ("accepted", "executed", "remote_verified_by_tool")),
            "Preparation cannot claim acceptance or execution",
        )
        _require(
            value["status"] in {"ready", "incomplete", "stale", "unavailable"},
            "Invalid selected receipt status",
        )
        missing = _rows(value["missing_fields"], "selected missing fields")
        for item in missing:
            if type(item) is str:
                continue
            problem = _record(item, "selected missing diagnostic")
            _require(
                set(problem) in ({"check_id", "status"}, {"acceptance_evidence", "reason"}),
                "Invalid selected missing diagnostic",
            )
            _require(
                all(type(text) is str for text in problem.values()),
                "Invalid selected missing diagnostic value",
            )
        _core().strings(value["reasons"], "preparation reasons")
        if value["next_actor_id"] is not None:
            _text(value["next_actor_id"], "selected next actor")
        if value["status"] == "ready":
            assessment = _record(value["assessment"], "ordinary assessment")
            _shape(
                assessment, {"status", "missing_fields", "reasons"}, set(), "ordinary assessment"
            )
            _require(
                not missing
                and assessment["status"] == "ready"
                and assessment["missing_fields"] == [],
                "Contradictory ordinary readiness",
            )
        materialized = value["materialization"]
        if materialized is not None:
            actual_refs, current = _selection_materialization(
                program, state, _record(materialized, "selected materialization"), reader
            )
            refs.extend(actual_refs)
        else:
            _require(value["status"] != "ready", "Ready preparation has no materialization")
        binding = _record(value["binding"], "selected receipt binding")
        _require(
            binding.get("program_id") == state["program_id"],
            "Selected receipt belongs to another program",
        )
        scope = core.strings(
            binding.get("scope", binding.get("review_scope", [])), "bound review scope"
        )
        try:
            subject = _subject(program, state, str(operation), value["task_id"], scope)
            if value["task_id"] is not None and scope:
                subject.update(
                    review_scope=sorted(set(scope)),
                    review_source_digest=program.source_digest(scope),
                )
            current = (subject == binding) and current
        except core.ProgramError:
            current = False
        selected_decisions = _rows(value["decision_refs"], "selected actual decisions")
        for raw in selected_decisions:
            ref = _ref(raw)
            _read(program, ref, reader)
            decision = _json(program, ref, reader)
            _shape(
                decision,
                {"schema_version", "kind", "subject_binding", "actor_id", "facts"},
                set(),
                "selected actual decision",
            )
            _version(decision, "evidence_decision")
            _require(
                decision["subject_binding"] == binding,
                "Original decision does not bind saved preparation",
            )
            _text(decision["actor_id"], "selected actual actor")
            _record(decision["facts"], "selected actual decision facts")
            refs.append(ref)
        receipt = _record(value["receipt"], "selected ordinary draft")
        if value["status"] == "ready":
            current = (
                _current_receipt_authority(
                    program, state, str(operation), receipt, value["next_actor_id"]
                )
                and current
            )
        related = _record(value["related_inputs"], "selected related inputs")
        for raw in _rows(receipt.get("verification_refs", []), "selected verification reliance"):
            reliance = _record(raw, "selected observation reliance")
            _shape(reliance, {"id", "receipt"}, set(), "observation reliance")
            _text(reliance["id"], "relied observation id")
            refs.append(_ref(reliance["receipt"]))
        if receipt.get("run_capture_ref") is not None:
            ref = _ref(receipt["run_capture_ref"])
            capture = core.flow_module("execution")._read_capture(program, ref, reader)
            refs.extend([ref, _ref(capture["start_ref"])])
            for key in ("input_capture_before", "input_capture_after", "request_receipt"):
                if capture.get(key) is not None:
                    refs.append(_ref(capture[key]))
            for stream in capture["outputs"].values():
                if stream.get("path") is not None:
                    refs.append(_ref({"path": stream["path"], "sha256": stream["sha256"]}))
            refs.extend(
                _ref({"path": row["path"], "sha256": row["sha256"]})
                for row in capture["artifacts"]["captured"]
            )
        if related.get("retained_outcome") is not None:
            retained = _record(related["retained_outcome"], "selected retained outcome")
            refs.append(_ref(retained["origin_ref"]))
            refs.extend(
                _ref(ref)
                for ref in _record(
                    retained.get("streams", {}), "selected retained streams"
                ).values()
            )
    else:
        raise core.ProgramError("Unsupported explicit evidence preparation envelope")
    unique = {core.digest(_ref(ref)): _ref(ref) for ref in refs}
    core.require(len(unique) <= MAX_ROWS * 4, "Selected reference graph exceeds bound")
    program.verify_refs(list(unique.values()), reader)
    summary = summarize_preparation(value)
    if not current:
        summary.update(
            status="stale", reasons=["stale_binding"], next_action="Refresh changed bindings"
        )
    return {"summary": summary, "refs": list(unique.values())}
