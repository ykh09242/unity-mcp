"""Optional explicit criterion links; annotations never supply acceptance or authorization."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, field
import re
from types import ModuleType
from typing import TYPE_CHECKING, Callable

if __package__:
    from tools import feature_program_coordination as coordination
    from tools import feature_program_phases as phases
    from tools import feature_program_planning as planning
else:
    import feature_program_coordination as coordination
    import feature_program_phases as phases
    import feature_program_planning as planning

if TYPE_CHECKING:
    from tools.feature_program import Program, ReadBudget

Json = coordination.Json
Record = coordination.Record


@dataclass(frozen=True, slots=True)
class Reads:
    """Per-observation bounded reads; never a persistent freshness cache."""

    artifacts: ReadBudget
    sources: ReadBudget
    snapshots: Record = field(default_factory=dict)


def reads(budget: ReadBudget | None = None) -> Reads:
    core = _core()
    return Reads(
        budget if budget is not None else core.ReadBudget(64 * 1024 * 1024),
        core.ReadBudget(128 * 1024 * 1024),
    )


class CoverageError(coordination.GovernanceError):
    """Invalid explicit linkage or stale declared reliance."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CoverageError(reason)


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def reference(value: Json) -> Record:
    ref = coordination._record(value, "coverage reference")
    require(set(ref) == {"path", "sha256"}, "Coverage references require exact path/hash")
    coordination._relative(ref.get("path"))
    require(
        bool(re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256")))),
        "Invalid coverage evidence hash",
    )
    return ref


def objective_binding(state: Record) -> Record:
    core = _core()
    goal = coordination._record(state.get("objective"), "coverage objective")
    criteria = coordination._texts(goal.get("criteria"), "objective criteria")
    revision = goal.get("membership_revision")
    require(
        revision is None or type(revision) is int and revision >= 0,
        "Invalid coverage membership revision",
    )
    return {
        "program_id": state["program_id"],
        "objective_id": goal["id"],
        "criteria": deepcopy(criteria),
        "criteria_sha256": core.digest(criteria),
        "membership_revision": revision,
        "member_ids": sorted(coordination._texts(goal.get("task_ids"), "objective members")),
    }


def criterion(value: Json, binding: Record) -> int:
    core = _core()
    ref = coordination._record(value, "criterion reference")
    ordinal = ref.get("ordinal")
    texts = coordination._texts(binding.get("criteria"), "bound criteria")
    require(
        set(ref) == {"ordinal", "text_sha256"}
        and type(ordinal) is int
        and 0 <= ordinal < len(texts),
        "Invalid original criterion ordinal",
    )
    require(
        ref["text_sha256"] == core.sha(texts[ordinal].encode("utf-8")),
        "Criterion exact text mismatch",
    )
    return ordinal


def _link_refs(link: Record) -> list[Json]:
    refs = coordination._items(link.get("evidence"), "link evidence")
    require(bool(refs), "Explicit link needs actual evidence references")
    for item in coordination._items(link.get("records", []), "linked records"):
        artifact = coordination._record(item, "linked record")
        coordination._choice(
            artifact.get("kind"),
            {"execution", "review", "finding", "delivery"},
            "linked record kind",
        )
        refs = [*refs, reference(artifact.get("receipt"))]
    for ref in refs:
        reference(ref)
    return refs


def task_binding(program: Program, state: Record, link: Record, reader: Reads) -> Record:
    core = _core()
    task_id = coordination._text(link.get("task_id"), "linked task")
    task = program.task(state, task_id)
    require(
        task_id in objective_binding(state)["member_ids"],
        "Linked task is outside current objective membership",
    )
    require(
        task.get("disposition") not in {"split", "retired"},
        "Disposed task cannot supply current coverage",
    )
    mode = coordination._choice(
        link.get("scope_kind"), {"candidate", "attempt"}, "coverage scope kind"
    )
    number = link.get("attempt")
    round_number = link.get("round")
    current_round: Json = None
    match mode:
        case "candidate":
            require(
                number is None and round_number is None,
                "Candidate linkage cannot claim execution attempt/round",
            )
            scope = coordination._texts(task.get("candidate_paths"), "candidate paths")
        case "attempt":
            attempt = program.current_attempt(task)
            require(type(number) is int and number == attempt["number"], "Stale linked attempt")
            rounds = coordination._items(attempt.get("rounds"), "linked rounds")
            if round_number is not None:
                require(
                    type(round_number) is int and round_number > 0 and round_number == len(rounds),
                    "Stale linked round",
                )
                current_round = deepcopy(rounds[-1])
            scope = coordination._texts(attempt.get("scope"), "owned scope")
        case _:
            raise CoverageError("Invalid coverage scope kind")
    paths = coordination._texts(link.get("paths"), "linked paths")
    require(
        bool(paths) and len(paths) == len(set(path.casefold() for path in paths)),
        "Linked paths must be explicit and unique",
    )
    for path in paths:
        core.safe_path(program.repo, path)
        require(
            any(path == owned or path.startswith(owned.rstrip("/") + "/") for owned in scope),
            "Linked path outside applicable task scope",
        )
    source = planning.source_for_task(state, task)
    source_path = core.safe_path(program.repo, coordination._relative(source.get("path")))
    require(
        core.sha(reader.artifacts.read(source_path, 8 * 1024 * 1024)) == source["sha256"],
        "Coverage frozen source drifted",
    )
    sections = core.extract_sections(reader.artifacts.read(source_path, 8 * 1024 * 1024))
    require(
        core.canonical(sections.get(task_id)) == core.canonical(task["source"]),
        "Coverage source section mismatch",
    )
    refs = _link_refs(link)
    for value in refs:
        ref = reference(value)
        prefix = f"evidence/{task_id}/attempt-{number}/" if mode == "attempt" else "evidence/"
        require(str(ref["path"]).startswith(prefix), "Link evidence is outside applicable attempt")
    program.verify_refs(refs, reader.artifacts)
    for value in coordination._items(link.get("records", []), "linked records"):
        record = coordination._record(value, "linked record")
        ref = reference(record["receipt"])
        metadata = core.read_json(program.path(str(ref["path"])), budget=reader.artifacts)
        require(metadata.get("task_id") == task_id, "Linked artifact task mismatch")
        if mode == "attempt":
            require(
                type(metadata.get("attempt")) is int and metadata["attempt"] == number,
                "Linked artifact attempt mismatch",
            )
    scope_key = core.digest(scope)
    if scope_key not in reader.snapshots:
        reader.snapshots[scope_key] = core.digest(program.source_snapshot(scope, reader.sources))
    return {
        "definition_digest": core.digest(planning.task_definition(task)),
        "source": planning.coverage_source_identity(program, state, source, reader.artifacts),
        "section": deepcopy(task["source"]),
        "scope": scope,
        "source_digest": reader.snapshots[scope_key],
        "attempt": number,
        "round": round_number,
        "submission": current_round,
        "round_count": len(coordination._items(program.current_attempt(task)["rounds"], "rounds"))
        if mode == "attempt"
        else None,
    }


def bind_input(
    program: Program, state: Record, receipt: Record, reader: Reads | None = None
) -> Record:
    """Read real bindings for authored contributors; never create evidence or passing results."""
    core = _core()
    reader = reader if reader is not None else reads()
    objective = objective_binding(state)
    links: list[Json] = []
    seen: set[tuple[int, str]] = set()
    linked: set[int] = set()
    for value in coordination._items(receipt.get("links"), "coverage links"):
        link = coordination._record(value, "coverage link")
        ordinal = criterion(link.get("criterion"), objective)
        identifier = coordination._text(link.get("task_id"), "linked task")
        require((ordinal, identifier) not in seen, "Duplicate/conflicting contributor link")
        seen.add((ordinal, identifier))
        linked.add(ordinal)
        links.append({**deepcopy(link), "binding": task_binding(program, state, link, reader)})
    uncovered: set[int] = set()
    uncovered_rows: list[Json] = []
    if coordination._items(receipt.get("uncovered", []), "uncovered criteria"):
        for source in planning.descriptors(state):
            path = core.safe_path(program.repo, coordination._relative(source.get("path")))
            require(
                core.sha(reader.artifacts.read(path, 8 * 1024 * 1024)) == source["sha256"],
                "Coverage uncovered frozen source drifted",
            )
    inventory_digest = core.digest(
        {
            "definitions": [
                planning.task_definition(program.task(state, identifier))
                for identifier in objective["member_ids"]
            ],
            "sources": [
                planning.coverage_source_identity(program, state, source, reader.artifacts)
                for source in planning.descriptors(state)
            ],
            "planning": planning.planning_digest(state),
        }
    )
    for value in coordination._items(receipt.get("uncovered", []), "uncovered criteria"):
        item = coordination._record(value, "uncovered criterion")
        ordinal = criterion(item.get("criterion"), objective)
        coordination._text(item.get("reason"), "uncovered reason")
        require(ordinal not in linked | uncovered, "Conflicting uncovered/contributor declaration")
        uncovered.add(ordinal)
        uncovered_rows.append({**deepcopy(item), "inventory_digest": inventory_digest})
    return {
        "expected_revision": phases.revision(state),
        "objective_binding": objective,
        "links": links,
        "uncovered": uncovered_rows,
    }


def annotation_records(state: Record) -> list[Record]:
    return [
        coordination._record(value, "coverage annotation")
        for value in coordination._items(
            state.get("coverage_annotations", []), "coverage annotations"
        )
    ]


def evidence_references(state: Record) -> list[Json]:
    refs: list[Json] = []
    for annotation in annotation_records(state):
        refs.extend(coordination._items(annotation.get("evidence"), "coverage evidence"))
    for value in refs:
        reference(value)
    return refs


def validate(state: Record) -> None:
    """Validate retained annotation structure without applying today's bindings to history."""
    core = _core()
    seen: set[str] = set()
    for item in annotation_records(state):
        receipt = coordination._record(item.get("annotation"), "recorded annotation")
        identifier = coordination._text(receipt.get("id"), "coverage ID")
        require(
            core.portable_task_id(identifier) and identifier.casefold() not in seen,
            "Invalid/duplicate coverage ID",
        )
        seen.add(identifier.casefold())
        require(
            type(receipt.get("schema_version")) is int
            and receipt["schema_version"] == 1
            and receipt.get("operation") == "coverage-record",
            "Invalid coverage schema/operation",
        )
        binding = coordination._record(
            receipt.get("objective_binding"), "recorded objective binding"
        )
        require(
            core.digest(binding.get("criteria")) == binding.get("criteria_sha256"),
            "Recorded criteria digest mismatch",
        )
        coordination._timestamp(item.get("recorded_at"), "coverage recording time")
        coordination._text(receipt.get("reason"), "coverage reason")
        coordination._text(receipt.get("actor_id"), "coverage actor")
        reference(item.get("receipt"))
        for value in coordination._items(receipt.get("links"), "recorded links"):
            link = coordination._record(value, "recorded link")
            criterion(link.get("criterion"), binding)
            coordination._text(link.get("task_id"), "recorded task")
            coordination._choice(
                link.get("scope_kind"), {"candidate", "attempt"}, "recorded scope kind"
            )
            for path in coordination._texts(link.get("paths"), "recorded paths"):
                coordination._relative(path)
            coordination._record(link.get("binding"), "recorded task binding")
            _link_refs(link)
        for value in coordination._items(receipt.get("uncovered", []), "uncovered criteria"):
            uncovered = coordination._record(value, "uncovered criterion")
            criterion(uncovered.get("criterion"), binding)
            coordination._text(uncovered.get("reason"), "uncovered reason")
    evidence_references(state)


def verify_history(program: Program, state: Record, budget: ReadBudget | None = None) -> None:
    """Keep frozen annotations intact without requiring old bindings to remain current."""
    core = _core()
    reader = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    program.verify_refs(evidence_references(state), reader)
    for annotation in annotation_records(state):
        receipt = coordination._record(annotation["annotation"], "annotation")
        ref = reference(annotation["receipt"])
        captured = core.read_json(program.path(str(ref["path"])), budget=reader)
        require(
            core.canonical(captured) == core.canonical(receipt),
            "Frozen coverage receipt metadata mismatch",
        )
        declared = [ref]
        for value in coordination._items(receipt["links"], "links"):
            declared.extend(_link_refs(coordination._record(value, "link")))
        frozen = {
            core.canonical(value)
            for value in coordination._items(annotation["evidence"], "coverage evidence")
        }
        require(
            all(core.canonical(value) in frozen for value in declared),
            "Coverage record omitted frozen linkage evidence",
        )


def _current(program: Program, state: Record, annotation: Record, reader: Reads) -> None:
    core = _core()
    receipt = coordination._record(annotation["annotation"], "annotation")
    program.verify_refs(
        coordination._items(annotation["evidence"], "coverage evidence"), reader.artifacts
    )
    captured = core.read_json(
        program.path(str(reference(annotation["receipt"])["path"])), budget=reader.artifacts
    )
    require(
        core.canonical(captured) == core.canonical(receipt),
        "Frozen coverage receipt metadata mismatch",
    )
    current = bind_input(program, state, receipt, reader)
    for field in ("objective_binding", "links", "uncovered"):
        require(
            core.canonical(receipt[field]) == core.canonical(current[field]),
            f"Stale coverage {field}",
        )


def validate_refs(
    program: Program, state: Record, receipt: Record, task_id: str | None = None
) -> list[Json]:
    """Validate declared reliance only; absent legacy references do not imply linkage."""
    core = _core()
    refs: list[Json] = []
    used_paths: set[str] = set()
    used_criteria: set[int] = set()
    reader = reads()
    current_objective: Record | None = None
    for value in coordination._items(receipt.get("coverage_refs", []), "coverage_refs"):
        row = coordination._record(value, "coverage reliance")
        require(set(row) == {"receipt", "criteria"}, "Invalid coverage reliance envelope")
        ref = reference(row.get("receipt"))
        path = str(ref["path"])
        require(path not in used_paths, "Duplicate/conflicting coverage reliance")
        used_paths.add(path)
        matches = [
            item
            for item in annotation_records(state)
            if core.canonical(item["receipt"]) == core.canonical(ref)
        ]
        require(len(matches) == 1, "Coverage reliance requires exact recorded receipt/hash")
        annotation = matches[0]
        _current(program, state, annotation, reader)
        current_objective = objective_binding(state)
        criteria = coordination._items(row.get("criteria"), "relied criteria")
        require(bool(criteria), "Coverage reliance needs nonempty criteria")
        bound = coordination._record(annotation["annotation"], "annotation")
        links = [
            coordination._record(item, "link")
            for item in coordination._items(bound["links"], "links")
        ]
        known = {criterion(link["criterion"], current_objective) for link in links}
        known |= {
            criterion(coordination._record(item, "uncovered")["criterion"], current_objective)
            for item in coordination._items(bound.get("uncovered", []), "uncovered")
        }
        for item in criteria:
            ordinal = criterion(item, current_objective)
            require(
                ordinal not in used_criteria and ordinal in known,
                "Unknown/duplicate coverage criterion reliance",
            )
            used_criteria.add(ordinal)
            if task_id is not None:
                require(
                    any(
                        link["task_id"] == task_id
                        and criterion(link["criterion"], current_objective) == ordinal
                        for link in links
                    ),
                    "Coverage reliance does not link applicable task",
                )
        refs.extend(coordination._items(annotation["evidence"], "coverage evidence"))
    return refs


def declared_refs(receipt: Record) -> list[Json]:
    """Collect declared reference rows, preserving unknown legacy prose fields."""
    declared = list(coordination._items(receipt.get("coverage_refs", []), "coverage_refs"))
    for family in ("acceptance_evidence", "findings", "criteria_results", "checks"):
        match receipt.get(family, []):
            case list() as rows:
                for value in rows:
                    match value:
                        case dict() as row:
                            declared.extend(
                                coordination._items(row.get("coverage_refs", []), "coverage_refs")
                            )
                        case _:
                            continue
            case _:
                continue
    return declared


def validate_receipt_refs(
    program: Program, state: Record, receipt: Record, task_id: str | None = None
) -> list[Json]:
    """Share optional reliance validation across real inputs and structured finding rows."""
    return validate_refs(program, state, {"coverage_refs": declared_refs(receipt)}, task_id)


def _acceptance_refs(state: Record) -> list[tuple[Record, str | None]]:
    result: list[tuple[Record, str | None]] = []
    for value in coordination._items(state.get("tasks", []), "tasks"):
        task = coordination._record(value, "task")
        identifier = coordination._text(task.get("id"), "task ID")
        for value in coordination._items(task.get("attempts", []), "attempts"):
            for value in coordination._items(
                coordination._record(value, "attempt").get("rounds", []), "rounds"
            ):
                submission = coordination._record(value, "round")
                for key in ("receipt", "review"):
                    if submission.get(key) is not None:
                        result.append(
                            (
                                coordination._record(submission[key], "acceptance reference"),
                                identifier,
                            )
                        )
    if state.get("objective") is not None:
        goal = coordination._record(state["objective"], "objective")
        if goal.get("completion") is not None:
            evidence = coordination._items(goal.get("evidence"), "completion evidence")
            require(bool(evidence), "Completion lacks frozen receipt")
            result.append((coordination._record(evidence[0], "completion reference"), None))
            result.append(
                (
                    coordination._record(
                        goal.get("integrated_review_ref"), "integrated review reference"
                    ),
                    None,
                )
            )
    return result


def publication_guard(program: Program, future: Record) -> Callable[[], None] | None:
    """Recheck only newly declared reliance against actual pre-state at publication."""
    core = _core()
    path = program.path("backlog.json")
    if not path.is_file():
        return None
    before = core.read_json(path, limit=8 * 1024 * 1024)
    existing = {core.canonical(ref) for ref, _ in _acceptance_refs(before)}
    supplied: list[tuple[Record, Record, str | None]] = []
    reader = core.ReadBudget(64 * 1024 * 1024)
    for ref, task_id in _acceptance_refs(future):
        if core.canonical(ref) in existing:
            continue
        metadata, parsed_hash = core.read_json_frozen(
            program.path(str(ref["path"])), limit=8 * 1024 * 1024, budget=reader
        )
        require(parsed_hash == ref["sha256"], "New acceptance receipt changed before publication")
        if declared_refs(metadata):
            validate_receipt_refs(program, before, metadata, task_id)
            supplied.append((ref, metadata, task_id))
    if not supplied:
        return None
    revision = phases.revision(before)

    def recheck() -> None:
        program.require_current_location()
        current = core.read_json(path, limit=8 * 1024 * 1024)
        require(
            phases.revision(current) == revision,
            "Registry changed before coverage reliance publication",
        )
        for ref, metadata, task_id in supplied:
            program.verify_refs([ref])
            validate_receipt_refs(program, current, metadata, task_id)

    return recheck


def record(program: Program, state: Record, name: str) -> None:
    """Freeze one annotation atomically, rechecking its actual pre-state and evidence."""
    core = _core()
    program.require_current_location()
    program.validate(state)
    receipt, parsed_hash = core.read_json_frozen(program.path(name))
    require(
        name.startswith("evidence/_coverage/attempt-1/"),
        "Coverage receipts must be immutable coverage evidence",
    )
    require(
        type(receipt.get("schema_version")) is int
        and receipt["schema_version"] == 1
        and receipt.get("operation") == "coverage-record",
        "Invalid coverage schema/operation",
    )
    identifier = coordination._text(receipt.get("id"), "coverage ID")
    require(
        core.portable_task_id(identifier)
        and all(
            str(item["annotation"]["id"]).casefold() != identifier.casefold()
            for item in annotation_records(state)
        ),
        "Invalid/duplicate coverage ID",
    )
    coordination._text(receipt.get("reason"), "coverage reason")

    def preconditions(current: Record) -> None:
        program.require_current_location()
        require(
            receipt.get("actor_id") == coordination.current_director_id(current)
            and receipt.get("actor_id") is not None,
            "Only current accepted director records coverage",
        )
        coordination.require_task_authority(current, str(receipt["actor_id"]))
        if current.get("phases") is not None:
            phases.guard_baseline(program, current)
        actual = bind_input(program, current, receipt)
        for key, value in actual.items():
            require(
                core.canonical(receipt.get(key)) == core.canonical(value),
                f"Stale coverage recording: {key}",
            )

    preconditions(state)
    refs: list[Json] = [{"path": name, "sha256": parsed_hash}]
    for value in coordination._items(receipt["links"], "links"):
        refs.extend(_link_refs(coordination._record(value, "link")))
    program.verify_refs(refs)
    proposal = deepcopy(state)
    coordination._items(
        proposal.setdefault("coverage_annotations", []), "coverage annotations"
    ).append(
        {
            "annotation": deepcopy(receipt),
            "receipt": refs[0],
            "evidence": refs,
            "recorded_at": core.utc(),
        }
    )
    program.event(
        proposal,
        "coverage-record",
        {
            "actor": receipt["actor_id"],
            "coverage_id": identifier,
            "reason": receipt["reason"],
            "receipt": refs[0],
        },
    )

    def precommit() -> None:
        current = core.read_json(program.path("backlog.json"))
        preconditions(current)
        program.verify_refs(refs)

    program.save(proposal, before_publish=precommit)
    state.clear()
    state.update(proposal)


def projection(program: Program, state: Record, budget: ReadBudget | None = None) -> Record:
    """Detach authored linkage, freshness and recorded results; no Git, locks or writes."""
    core = _core()
    validate(state)
    reader = reads(budget)
    history: list[Json] = []
    current: Record | None = None
    for annotation in annotation_records(state):
        freshness = "current"
        reasons: list[Json] = []
        try:
            _current(program, state, annotation, reader)
        except (
            CoverageError,
            core.ProgramError,
            planning.PlanningError,
            coordination.GovernanceError,
            OSError,
        ) as exc:
            freshness = (
                "unavailable"
                if isinstance(exc, OSError) or "Missing" in str(exc) or "unavailable" in str(exc)
                else "stale"
            )
            reasons.append(str(exc))
        history.append(
            {
                "id": annotation["annotation"]["id"],
                "receipt": deepcopy(annotation["receipt"]),
                "recorded_at": annotation["recorded_at"],
                "freshness": freshness,
                "reasons": reasons,
            }
        )
        current = annotation if freshness == "current" else None
    rows: list[Json] = []
    goal = state.get("objective")
    criteria = (
        coordination._texts(coordination._record(goal, "objective")["criteria"], "criteria")
        if goal is not None
        else []
    )
    annotation = (
        coordination._record(current["annotation"], "annotation") if current is not None else None
    )
    for ordinal, text in enumerate(criteria):
        links = (
            [
                deepcopy(coordination._record(value, "link"))
                for value in coordination._items(annotation["links"], "links")
                if value["criterion"]["ordinal"] == ordinal
            ]
            if annotation
            else []
        )
        uncovered = (
            [
                deepcopy(value)
                for value in coordination._items(annotation.get("uncovered", []), "uncovered")
                if value["criterion"]["ordinal"] == ordinal
            ]
            if annotation
            else []
        )
        observations: list[Json] = []
        for link in links:
            for value in coordination._items(link.get("records", []), "records"):
                artifact = coordination._record(value, "record")
                ref = reference(artifact["receipt"])
                captured = core.read_json(program.path(str(ref["path"])), budget=reader.artifacts)
                observations.append(
                    {
                        "task_id": link["task_id"],
                        "kind": artifact["kind"],
                        "receipt": deepcopy(ref),
                        "facts": {
                            key: deepcopy(captured[key])
                            for key in (
                                "checks",
                                "acceptance_evidence",
                                "criteria_results",
                                "findings",
                                "unresolved_findings",
                                "unresolved_acceptance",
                                "unresolved_blockers",
                                "verdict",
                                "acceptance_complete",
                                "status",
                                "limitations",
                            )
                            if key in captured
                        },
                    }
                )
            link.pop("binding", None)
        rows.append(
            {
                "criterion": {"ordinal": ordinal, "text_sha256": core.sha(text.encode())},
                "text": text,
                "linkage": "linked" if links else "unlinked" if annotation else "unknown",
                "knowledge": "contributors_declared"
                if links
                else "uncovered"
                if uncovered
                else "unknown",
                "links": links,
                "uncovered": uncovered,
                "recorded_observations": observations,
                "acceptance": "not_inferred",
            }
        )
    task_facts: list[Json] = []
    aggregate: Record = {
        "status": "unknown",
        "criteria_results": [],
        "reasons": [],
        "head_freshness": "not_assessed",
    }
    if goal is not None:
        for identifier in coordination._texts(
            coordination._record(goal, "objective")["task_ids"], "objective members"
        ):
            task = program.task(state, identifier)
            acceptance: Record = {
                "status": "unknown",
                "reasons": [],
                "basis": "No authoritative current-attempt acceptance assessed",
            }
            if task["status"] == "done":
                try:
                    program.verify_terminal(task, program.current_attempt(task), reader.artifacts)
                    acceptance.update(
                        {
                            "status": "accepted",
                            "basis": "Validated frozen historical terminal records",
                            "review": deepcopy(program.current_attempt(task)["review"]),
                        }
                    )
                except (core.ProgramError, OSError) as exc:
                    acceptance.update({"status": "failed", "reasons": [str(exc)]})
            task_facts.append(
                {
                    "task_id": identifier,
                    "lifecycle": task["status"],
                    "recorded_acceptance": acceptance,
                }
            )
        goal_record = coordination._record(goal, "objective")
        if goal_record.get("completion") is not None:
            try:
                refs = coordination._items(goal_record.get("evidence"), "completion evidence")
                program.verify_refs(refs, reader.artifacts)
                require(bool(refs), "Completion lacks immutable receipt")
                ref = coordination._record(refs[0], "completion receipt")
                completion = core.read_json(program.path(str(ref["path"])), budget=reader.artifacts)
                require(
                    core.canonical(completion) == core.canonical(goal_record["completion"]),
                    "Recorded completion receipt metadata mismatch",
                )
                source = core.digest(
                    program.source_snapshot(
                        coordination._texts(completion.get("scope"), "completion scope"),
                        reader.sources,
                    )
                )
                aggregate.update(
                    {
                        "status": "recorded",
                        "receipt": deepcopy(ref),
                        "criteria_results": deepcopy(
                            coordination._items(
                                completion.get("criteria_results"), "recorded criterion results"
                            )
                        ),
                        "source_freshness": "current"
                        if source == completion.get("source_digest")
                        else "stale",
                        "limitations": [
                            "Frozen completion is historical; current HEAD and renewed acceptance are not inferred."
                        ],
                    }
                )
            except (coordination.GovernanceError, core.ProgramError, OSError) as exc:
                aggregate.update({"status": "unavailable", "reasons": [str(exc)]})
    return {
        "objective_id": coordination._record(goal, "objective")["id"] if goal else None,
        "criteria": rows,
        "history": history,
        "legacy_absence": not annotation_records(state),
        "tasks": task_facts,
        "recorded_objective_acceptance": aggregate,
        "integrated_review": {
            "status": "not_assessed",
            "requires": [
                "Actual current objective-completion and integrated-review receipts",
                "Exact criteria evidence and current membership revision",
                "Terminal member acceptance and separate delivery/CI",
                "Independent reviewer distinct from director and all member implementers",
                "Current aggregate source, no unresolved relevant questions/handoffs",
            ],
            "limitations": [
                "Only supplied inputs run authoritative completion validation; frozen local provider observations do not authenticate remote state."
            ],
        },
        "limitations": [
            "Links never establish acceptance; recorded check facts are not new execution.",
            "HEAD and hypothetical receipt eligibility are not assessed by this snapshot.",
        ],
    }


def diagnostics(
    program: Program, state: Record, args: argparse.Namespace, *, observed_head: str | None = None
) -> Record:
    """Run only shared nonpublishing acceptance preparation for explicitly supplied inputs."""
    core = _core()
    result: Record = {
        "operation": args.operation,
        "status": "not_assessed",
        "reasons": [],
        "limitations": [
            "First authoritative failure only; success is advisory, never future authorization."
        ],
    }
    if args.operation is None or args.input is None:
        result["reasons"] = ["An operation and its actual receipt are required."]
        structural: Record = {"status": "validated", "reasons": []}
        try:
            program.validate(state)
        except (
            core.ProgramError,
            coordination.GovernanceError,
            phases.PhaseError,
            planning.PlanningError,
            core.questions.QuestionError,
            OSError,
        ) as exc:
            structural.update(
                {"status": "failed", "reasons": [str(exc)], "failure_type": type(exc).__name__}
            )
        result["structural_validation"] = structural
        return result
    try:
        program.validate(state)
        if args.operation in {"submit", "complete", "objective-complete"} and observed_head is None:
            result["reasons"] = [
                "A verified current HEAD observation is required; this pure diagnostic does not read Git."
            ]
            return result
        match args.operation:
            case "submit":
                require(
                    args.task is not None and args.owner is not None,
                    "Submission diagnostics require actual task/owner",
                )
                future = program.prepare_submit(
                    state,
                    argparse.Namespace(id=args.task, owner=args.owner, receipt=args.input),
                    observed_head=coordination._text(observed_head, "observed current HEAD"),
                )
            case "complete" | "rework":
                require(args.task is not None, "Review diagnostics require actual task")
                future = program.prepare_review(
                    state,
                    argparse.Namespace(id=args.task, command=args.operation, review=args.input),
                    observed_head=observed_head,
                )
            case "objective-complete":
                receipt, parsed_hash = coordination._load(program, args.input)
                future = coordination.prepare_objective_completion(
                    program,
                    state,
                    receipt,
                    args.input,
                    parsed_hash,
                    observed_head=coordination._text(observed_head, "observed current HEAD"),
                )
            case _:
                raise CoverageError("Unsupported coverage diagnostic operation")
        guard = publication_guard(program, future)
        program.prepare_save(future)
        if guard is not None:
            guard()
        result["status"] = "validated"
    except (
        core.ProgramError,
        coordination.GovernanceError,
        phases.PhaseError,
        planning.PlanningError,
        core.questions.QuestionError,
        OSError,
    ) as exc:
        result.update(
            {"status": "failed", "reasons": [str(exc)], "failure_type": type(exc).__name__}
        )
    return result


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name in ("coverage-binding", "coverage-record"):
        command = commands.add_parser(name, help="Bind or record optional explicit criterion links")
        command.add_argument("--input", required=True)
    read = commands.add_parser(
        "coverage", help="Read current coverage and actual acceptance diagnostics"
    )
    read.add_argument("--operation", choices=("submit", "complete", "rework", "objective-complete"))
    read.add_argument("--input")
    read.add_argument("--task")
    read.add_argument("--owner")
