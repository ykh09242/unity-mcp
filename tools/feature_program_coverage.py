"""Explicit criterion links and integrated proof; historical execution remains separate."""

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
    from tools.feature_program import PreparedChange, Program, ReadBudget

Json = coordination.Json
Record = coordination.Record


@dataclass(frozen=True, slots=True)
class Reads:
    """Per-observation bounded reads; never a persistent freshness cache."""

    artifacts: ReadBudget
    sources: ReadBudget
    snapshots: Record = field(default_factory=dict)
    sections: dict[tuple[str, str], Record] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class _Bindings:
    """Lazy derived bindings for one immutable state and its existing read ledgers."""

    program: Program
    state: Record
    reader: Reads
    values: Record = field(default_factory=dict)
    definitions: Record = field(default_factory=dict)
    sources: dict[bytes, Record] = field(default_factory=dict)

    def objective(self) -> Record:
        if "objective" not in self.values:
            self.values["objective"] = objective_binding(self.state)
        return self.values["objective"]

    def definition(self, task: Record) -> Record:
        identifier = str(task["id"])
        if identifier not in self.definitions:
            self.definitions[identifier] = planning.task_definition(task)
        return self.definitions[identifier]

    def source(self, source: Record) -> Record:
        key = _core().canonical(source)
        if key not in self.sources:
            self.sources[key] = planning.coverage_source_identity(
                self.program, self.state, source, self.reader.artifacts
            )
        return self.sources[key]

    def inventory(self) -> str:
        core = _core()
        if "inventory" not in self.values:
            self.values["inventory"] = core.digest(
                {
                    "definitions": [
                        self.definition(self.program.task(self.state, identifier))
                        for identifier in self.objective()["member_ids"]
                    ],
                    "sources": [self.source(source) for source in planning.descriptors(self.state)],
                    "planning": planning.planning_digest(self.state),
                }
            )
        return self.values["inventory"]

    def revision(self) -> str:
        if "revision" not in self.values:
            self.values["revision"] = phases.revision(self.state)
        return self.values["revision"]


def _metadata(program: Program, value: Json, reader: ReadBudget) -> Record:
    """Decode the exact admitted bytes after checking this consumer's expected hash."""
    core = _core()
    ref = reference(value)
    name = str(ref["path"])
    captured = reader.capture(program.directory, name, 8 * 1024 * 1024)
    core.require(captured.sha256 == ref["sha256"], f"Frozen evidence changed: {name}")
    return reader.json(captured)


def task_binding(program: Program, state: Record, link: Record, reader: Reads) -> Record:
    """Bind an independently supplied link without accepting unchecked derived facts."""
    return _task_binding(program, state, link, reader, _Bindings(program, state, reader))


def _task_binding(
    program: Program, state: Record, link: Record, reader: Reads, bindings: _Bindings
) -> Record:
    core = _core()
    task_id = coordination._text(link.get("task_id"), "linked task")
    task = program.task(state, task_id)
    require(
        task_id in bindings.objective()["member_ids"],
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
    source_name = coordination._relative(source.get("path"))
    captured = reader.artifacts.capture(program.repo, source_name, 8 * 1024 * 1024)
    require(captured.sha256 == source["sha256"], "Coverage frozen source drifted")
    source_key = (source_name, captured.sha256)
    if source_key not in reader.sections:
        reader.sections[source_key] = core.extract_sections(captured.content)
    sections = reader.sections[source_key]
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
        metadata = _metadata(program, ref, reader.artifacts)
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
        "definition_digest": core.digest(bindings.definition(task)),
        "source": deepcopy(bindings.source(source)),
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
    reader = reader if reader is not None else reads()
    return _bind_input(program, state, receipt, reader, _Bindings(program, state, reader))


def _bind_input(
    program: Program, state: Record, receipt: Record, reader: Reads, bindings: _Bindings
) -> Record:
    core = _core()
    objective = bindings.objective()
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
        links.append(
            {**deepcopy(link), "binding": _task_binding(program, state, link, reader, bindings)}
        )
    uncovered: set[int] = set()
    uncovered_rows: list[Json] = []
    if coordination._items(receipt.get("uncovered", []), "uncovered criteria"):
        for source in planning.descriptors(state):
            path = core.safe_path(program.repo, coordination._relative(source.get("path")))
            require(
                core.sha(reader.artifacts.read(path, 8 * 1024 * 1024)) == source["sha256"],
                "Coverage uncovered frozen source drifted",
            )
    inventory_digest = bindings.inventory()
    for value in coordination._items(receipt.get("uncovered", []), "uncovered criteria"):
        item = coordination._record(value, "uncovered criterion")
        ordinal = criterion(item.get("criterion"), objective)
        coordination._text(item.get("reason"), "uncovered reason")
        require(ordinal not in linked | uncovered, "Conflicting uncovered/contributor declaration")
        uncovered.add(ordinal)
        uncovered_rows.append({**deepcopy(item), "inventory_digest": inventory_digest})
    return {
        "expected_revision": bindings.revision(),
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


def _current(
    program: Program,
    state: Record,
    annotation: Record,
    reader: Reads,
    bindings: _Bindings | None = None,
) -> None:
    core = _core()
    receipt = coordination._record(annotation["annotation"], "annotation")
    program.verify_refs(
        coordination._items(annotation["evidence"], "coverage evidence"), reader.artifacts
    )
    captured = _metadata(program, annotation["receipt"], reader.artifacts)
    require(
        core.canonical(captured) == core.canonical(receipt),
        "Frozen coverage receipt metadata mismatch",
    )
    current = _bind_input(
        program,
        state,
        receipt,
        reader,
        bindings if bindings is not None else _Bindings(program, state, reader),
    )
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
    bindings = _Bindings(program, state, reader)
    annotations: dict[bytes, list[Record]] = {}
    for annotation in annotation_records(state):
        annotations.setdefault(core.canonical(annotation["receipt"]), []).append(annotation)
    current_objective: Record | None = None
    for value in coordination._items(receipt.get("coverage_refs", []), "coverage_refs"):
        row = coordination._record(value, "coverage reliance")
        require(set(row) == {"receipt", "criteria"}, "Invalid coverage reliance envelope")
        ref = reference(row.get("receipt"))
        path = str(ref["path"])
        require(path not in used_paths, "Duplicate/conflicting coverage reliance")
        used_paths.add(path)
        matches = annotations.get(core.canonical(ref), [])
        require(len(matches) == 1, "Coverage reliance requires exact recorded receipt/hash")
        annotation = matches[0]
        _current(program, state, annotation, reader, bindings)
        current_objective = bindings.objective()
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
    if not core.observed_is_file(path):
        return None
    observed = program.read_registry_observation()
    before = core.parse_registry_state(observed, path=path)
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
        current_observation = program.read_registry_observation()
        current = core.parse_registry_state(current_observation, path=path)
        require(
            phases.revision(current) == revision
            and current_observation.storage_sha256 == observed.storage_sha256,
            "Registry changed before coverage reliance publication",
        )
        for ref, metadata, task_id in supplied:
            program.verify_refs([ref])
            validate_receipt_refs(program, current, metadata, task_id)

    return recheck


def record(program: Program, state: Record, name: str) -> None:
    """Freeze one annotation atomically, rechecking its actual pre-state and evidence."""
    prepared = prepare_record(program, state, name)
    program.save(prepared.state, before_publish=prepared.before_publish)
    state.clear()
    state.update(prepared.state)


def prepare_record(program: Program, state: Record, name: str) -> PreparedChange:
    """Prepare the real immutable annotation without publishing it."""
    core = _core()
    program.require_current_location()
    observed = program.read_registry_observation()
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
        current_observation = program.read_registry_observation()
        current = core.parse_registry_state(current_observation, path=program.path("backlog.json"))
        require(
            current_observation.storage_sha256 == observed.storage_sha256,
            "Registry changed before coverage recording publication",
        )
        preconditions(current)
        program.verify_refs(refs)

    return core.PreparedChange(proposal, precommit)


def validate_integrated_mapping(
    program: Program,
    state: Record,
    review: Record,
    *,
    budget: ReadBudget | None = None,
    verification_facts: Record | None = None,
) -> list[Json]:
    """Validate optional reviewed check reliance through ordinary exact observations."""
    core = _core()
    mapping = coordination._record(review.get("obligation_coverage"), "obligation coverage")
    require(
        set(mapping) == {"schema_version", "items"}
        and type(mapping.get("schema_version")) is int
        and mapping["schema_version"] == 1,
        "Invalid integrated obligation mapping",
    )
    entries = coordination._items(mapping["items"], "mapped obligations")
    require(len(entries) <= 2000, "Too many mapped obligations")
    facts = (
        verification_facts
        if verification_facts is not None
        else core.verification.projection(program, state, budget=budget, limit=None)
    )
    records = core.verification._index(state)
    binding = objective_binding(state)
    retained: list[Json] = []
    seen: set[tuple[str, str, int]] = set()
    for value in entries:
        item = coordination._record(value, "mapped obligation")
        require(
            set(item)
            == {
                "criterion",
                "task_id",
                "attempt",
                "check_id",
                "submission",
                "request_ref",
                "observation_ref",
                "context_digest",
            },
            "Invalid mapped obligation fields",
        )
        ordinal = criterion(item["criterion"], binding)
        task_id = coordination._text(item["task_id"], "mapped task")
        require(task_id in binding["member_ids"], "Mapped task outside objective")
        task = program.task(state, task_id)
        attempt = program.current_attempt(task)
        check = coordination._text(item["check_id"], "mapped check")
        require(check in task["required_checks"], "Mapped check is not required")
        require(
            type(item["attempt"]) is int and item["attempt"] == attempt["number"],
            "Mapped task attempt changed",
        )
        submission = coordination._record(attempt.get("submission"), "mapped submission")
        require(
            item["submission"]
            == {key: submission[key] for key in ("round", "receipt", "source_digest")},
            "Mapped submission changed",
        )
        request_ref = reference(item["request_ref"])
        observation_ref = reference(item["observation_ref"])
        matched = [record for record in records.values() if record["receipt"] == observation_ref]
        require(len(matched) == 1, "Mapped observation is not exact recorded evidence")
        observation = matched[0]
        request = records[str(observation["request_id"])]
        require(
            request["receipt"] == request_ref
            and request["input"]["task_id"] == task_id
            and check in request["input"]["check_ids"]
            and item["context_digest"] == core.digest(request["context"]),
            "Mapped request/context identity changed",
        )
        lanes = facts["tasks"][task_id]["lanes"]["items"]
        matches = [
            lane
            for lane in lanes
            if lane["request_ref"] == request_ref and lane["observation_ref"] == observation_ref
        ]
        require(len(matches) == 1, "Mapped result is not the current lane selection")
        lane = matches[0]
        require(
            lane["input_complete"] is True
            and (lane.get("environment") is not None or "input_capture" in lane["context"]),
            "Mapped execution requires complete declared inputs and environment",
        )
        require(
            lane["phase"] == "finished"
            and lane["result"] == "passed"
            and lane["applicability"] == "current"
            and not lane["observation_problems"],
            "Mapped execution lacks current passing complete inputs",
        )
        provider = lane.get("provider_execution")
        require(
            provider is None
            or provider.get("status") == "passed"
            and provider.get("current") is True,
            "Mapped provider execution is not current and passing",
        )
        key = (task_id, check, ordinal)
        require(key not in seen, "Duplicate mapped obligation")
        seen.add(key)
        retained.extend([*request["evidence"], *observation["evidence"]])
    refs = list({core.canonical(ref): ref for ref in retained}.values())
    program.verify_refs(refs, budget)
    return refs


def _completion_boundary(state: Record, completion: Record) -> int | None:
    """Locate the exact canonical acceptance event, never infer ordering from clocks."""
    matches = [
        index
        for index, event in enumerate(state.get("events", []))
        if event.get("action") == "objective-complete" and event.get("receipt") == completion
    ]
    return matches[-1] if matches else None


def _scope_covers(scope: list[str], paths: list[str]) -> bool:
    return all(
        any(path == root or path.startswith(root.rstrip("/") + "/") for root in scope)
        for path in paths
    )


def _integrated_obligations(
    program: Program,
    state: Record,
    facts: Record,
    *,
    accepted: bool,
    scope: list[str],
    boundary: int | None,
    mapping: list[Json],
) -> tuple[list[Json], list[Json]]:
    """Assess the full member set before display bounds; never alter executed lane facts."""
    core = _core()
    records = core.verification._index(state)
    positions = {
        event.get("verification_id"): index
        for index, event in enumerate(state.get("events", []))
        if event.get("action") == "verification-record"
    }
    director = coordination.current_director_id(state) or "leader"
    obligations: list[Json] = []
    members: list[Json] = []
    for task_id in objective_binding(state)["member_ids"]:
        task = program.task(state, task_id)
        attempts = task.get("attempts", [])
        attempt = program.current_attempt(task)["number"] if attempts else None
        task_facts = facts.get("tasks", {}).get(task_id, {})
        lanes = task_facts.get("lanes", {}).get("items", [])
        rows: list[Json] = []
        for check in task["required_checks"]:
            selected = [lane for lane in lanes if check in lane["check_ids"]]
            if not selected:
                selected = [None]
            for lane in selected:
                status, basis, codes, actionable = "unmapped", "none", ["mapping_absent"], False
                actor, action, trigger = (
                    director,
                    "assess_obligation_mapping",
                    "explicit_obligation_evidence",
                )
                criterion_ref: Json = None
                if lane is not None:
                    event_id = lane["observation_id"] or lane["request_id"]
                    old = (
                        boundary is not None
                        and positions.get(event_id, len(state.get("events", []))) < boundary
                    )
                    request = records[lane["request_id"]]
                    historical = (
                        accepted
                        and old
                        and lane["phase"] == "finished"
                        and lane["result"] == "passed"
                        and not lane["observation_problems"]
                        and set(lane.get("currentness_changes", []))
                        <= {"source_digest", "input_digest"}
                        and _scope_covers(scope, request["input"]["input_scope"])
                    )
                    provider = lane.get("provider_execution")
                    provider_bad = provider is not None and (
                        provider.get("status") != "passed" or not provider.get("current")
                    )
                    match lane["phase"]:
                        case "requested" | "running" | "blocked":
                            status, codes, actionable = "pending", ["verification_pending"], True
                        case "canceled":
                            status, codes, actionable = (
                                "requires_recheck",
                                ["verification_canceled"],
                                True,
                            )
                        case "finished":
                            if lane["result"] != "passed":
                                status, codes, actionable = (
                                    "adverse",
                                    ["verification_nonpassing"],
                                    True,
                                )
                            elif lane["observation_problems"]:
                                status, codes, actionable = (
                                    "requires_recheck",
                                    ["execution_inputs_incomplete"],
                                    True,
                                )
                            elif historical:
                                status, basis, codes = (
                                    "historical_only",
                                    "integrated_member",
                                    ["historical_execution"],
                                )
                            elif lane["applicability"] != "current" or provider_bad:
                                status, codes, actionable = (
                                    "requires_recheck",
                                    ["verification_changed"],
                                    True,
                                )
                        case _:
                            status, codes, actionable = (
                                "unavailable",
                                ["verification_unavailable"],
                                True,
                            )
                    if lane["applicability"] == "unavailable":
                        status, basis, codes, actionable = (
                            "unavailable",
                            "none",
                            ["verification_unavailable"],
                            True,
                        )
                    if (
                        provider is not None
                        and not actionable
                        and lane["applicability"] != "unavailable"
                        and provider.get("adopted") is True
                        and provider.get("status") == "pending"
                    ):
                        status, basis, codes, actionable = (
                            "pending",
                            "none",
                            ["provider_execution_pending"],
                            True,
                        )
                    elif (
                        provider is not None
                        and lane["applicability"] != "unavailable"
                        and provider.get("status")
                        in {
                            "failed",
                            "unavailable",
                        }
                    ):
                        status = "unavailable" if provider["status"] == "unavailable" else "adverse"
                        basis, codes, actionable = "none", ["provider_execution_adverse"], True
                    matches = [
                        item
                        for item in mapping
                        if item["task_id"] == task_id
                        and item["check_id"] == check
                        and item["request_ref"] == lane["request_ref"]
                        and item["observation_ref"] == lane["observation_ref"]
                    ]
                    if accepted and matches and not actionable:
                        status, basis, codes = "fulfilled", "integrated_mapping", []
                        criterion_ref = deepcopy(matches[0]["criterion"])
                    if actionable:
                        actor = lane.get("resolver_id") or lane["verifier_id"] or director
                        action, trigger = (
                            "resolve_verification",
                            lane.get("next_trigger") or "current_verification_evidence",
                        )
                if (
                    not accepted
                    and not actionable
                    and state["objective"].get("completion") is not None
                ):
                    status, basis, codes, actionable = (
                        "requires_recheck",
                        "none",
                        ["integrated_acceptance_not_current"],
                        True,
                    )
                    actor, action, trigger = (
                        director,
                        "review_integrated_completion",
                        "current_independent_review",
                    )
                rows.append(
                    {
                        "task_id": task_id,
                        "attempt": attempt,
                        "check_id": check,
                        "criterion": criterion_ref,
                        "status": status,
                        "actionable": actionable,
                        "basis": basis,
                        "reason_codes": codes,
                        "next_actor_id": actor,
                        "next_action": action,
                        "next_trigger": trigger,
                        "request_id": lane["request_id"] if lane else None,
                        "observation_id": lane["observation_id"] if lane else None,
                    }
                )
                if status == "fulfilled":
                    for match in matches[1:]:
                        rows.append(
                            {**deepcopy(rows[-1]), "criterion": deepcopy(match["criterion"])}
                        )
        for finding in task_facts.get("findings", {}).get("items", []):
            if not any(
                resolution.get("status") == "resolved" for resolution in finding["resolutions"]
            ):
                rows.append(
                    {
                        "task_id": task_id,
                        "attempt": attempt,
                        "check_id": None,
                        "criterion": None,
                        "status": "adverse",
                        "actionable": True,
                        "basis": "none",
                        "reason_codes": ["unresolved_finding"],
                        "next_actor_id": task.get("owner") or director,
                        "next_action": "resolve_finding",
                        "next_trigger": "validated_finding_resolution",
                        "request_id": finding["request_id"],
                        "observation_id": finding["id"],
                    }
                )
        obligations.extend(rows)
        members.append(
            {
                "task_id": task_id,
                "attempt": attempt,
                "status": "requires_recheck"
                if any(row["actionable"] for row in rows)
                else "accepted"
                if accepted
                else "not_assessed",
                "obligations": deepcopy(rows),
            }
        )
    return members, obligations


def assess_integrated_completion(
    program: Program,
    state: Record,
    *,
    verification_facts: Record | None = None,
    budget: ReadBudget | None = None,
    observed_head: str | None = None,
) -> Record:
    """Assess recorded integrated proof and separate named coverage from historical execution."""
    core = _core()
    reader = reads(budget)
    goal = state.get("objective")
    director = coordination.current_director_id(state) or "leader"
    result: Record = {
        "schema_version": 1,
        "status": "not_assessed",
        "currentness": "unknown",
        "reason_codes": [],
        "reasons": [],
        "basis": None,
        "members": [],
        "obligations": [],
        "counts": {
            "members": 0,
            "accepted_members": 0,
            "obligations": 0,
            "fulfilled": 0,
            "unmapped": 0,
            "historical": 0,
            "actionable": 0,
        },
        "next_actor_id": director,
        "next_action": "review_integrated_completion",
        "next_trigger": "current_independent_review",
        "remote_verified_by_tool": False,
    }
    if goal is None:
        result["reason_codes"] = ["objective_absent"]
        return result
    facts = (
        verification_facts
        if verification_facts is not None
        else core.verification.projection(program, state, budget=reader.artifacts, limit=None)
    )
    core.verification._require_full_projection(state, facts)
    completion = goal.get("completion")
    scope: list[str] = []
    mapping: list[Json] = []
    boundary = None
    retained: list[Json] = list(core.verification.evidence_references(state))
    source: Record | None = None
    if completion is None:
        result["reason_codes"] = ["completion_absent"]
    else:
        try:
            refs = coordination._items(goal.get("evidence"), "completion evidence")
            require(bool(refs), "Completion lacks immutable receipt")
            retained.extend(refs)
            program.verify_refs(refs, reader.artifacts)
            ref = reference(refs[0])
            loaded = _metadata(program, ref, reader.artifacts)
            require(loaded == completion, "Recorded completion receipt metadata mismatch")
            scope = coordination._texts(loaded.get("scope"), "completion scope")
            source = program.source_snapshot(scope, reader.sources)
            head = observed_head if observed_head is not None else program.current_head()
            changed = []
            if core.digest(source) != loaded.get("source_digest"):
                changed.append("aggregate_source_changed")
            if head != loaded.get("source_head"):
                changed.append("aggregate_head_changed")
            if changed:
                result.update(status="not_accepted", currentness="stale", reason_codes=changed)
            else:
                proof = coordination.assess_objective_completion(
                    program, state, loaded, str(ref["path"]), str(ref["sha256"]), observed_head=head
                )
                review_ref = reference(proof["integrated_review_refs"][0])
                review = _metadata(program, review_ref, reader.artifacts)
                retained.extend(proof["evidence_refs"] + proof["integrated_review_refs"])
                mapping = review.get("obligation_coverage", {}).get("items", [])
                boundary = _completion_boundary(state, loaded)
                require(boundary is not None, "Completion canonical acceptance event unavailable")
                basis = {
                    "completion_ref": deepcopy(ref),
                    "integrated_review_ref": deepcopy(review_ref),
                    "objective_binding": objective_binding(state),
                    "source_head": head,
                    "source_digest": loaded["source_digest"],
                    "scope": deepcopy(scope),
                    "reviewer_id": review["reviewer"],
                    "acceptance_event_index": boundary,
                }
                basis["identity"] = core.digest(basis)
                result.update(status="accepted", currentness="current", basis=basis)
        except coordination.GovernanceError as exc:
            result.update(
                status="not_accepted",
                currentness="stale",
                reason_codes=["integrated_proof_not_current"],
                reasons=[str(exc)],
            )
        except (core.ProgramError, planning.PlanningError, OSError) as exc:
            result.update(
                status="unavailable",
                currentness="unavailable",
                reason_codes=["integrated_proof_unavailable"],
                reasons=[str(exc)],
            )
    members, obligations = _integrated_obligations(
        program,
        state,
        facts,
        accepted=result["status"] == "accepted",
        scope=scope,
        boundary=boundary,
        mapping=mapping,
    )
    actionable = [row for row in obligations if row["actionable"]]
    result.update(
        members=members,
        obligations=obligations,
        counts={
            "members": len(members),
            "accepted_members": sum(row["status"] == "accepted" for row in members),
            "obligations": len(obligations),
            "fulfilled": sum(row["status"] == "fulfilled" for row in obligations),
            "unmapped": sum(row["status"] == "unmapped" for row in obligations),
            "historical": sum(row["status"] == "historical_only" for row in obligations),
            "actionable": len(actionable),
        },
    )
    if actionable:
        priority = {"unavailable": 0, "adverse": 1, "pending": 2, "requires_recheck": 3}
        first = min(actionable, key=lambda row: priority[row["status"]])
        result.update({key: first[key] for key in ("next_actor_id", "next_action", "next_trigger")})
        if result["status"] == "accepted":
            result.update(status="not_accepted", reason_codes=["current_obligations_actionable"])
    elif result["status"] == "accepted":
        result.update(
            next_actor_id="user", next_action="set_next_goal", next_trigger="next_user_goal"
        )
    if source is not None:
        require(
            program.source_snapshot(scope) == source,
            "Integrated source changed during observation; refresh",
        )
    if result["basis"] is not None:
        current_basis = dict(coordination._record(result["basis"], "assessed integrated basis"))
        require(
            program.current_head() == current_basis["source_head"],
            "Integrated HEAD changed during observation; refresh",
        )
    if result["status"] == "accepted":
        program.verify_refs(retained)
    return result


def projection(
    program: Program,
    state: Record,
    budget: ReadBudget | None = None,
    *,
    verification_facts: Record | None = None,
    observed_head: str | None = None,
) -> Record:
    """Detach authored linkage, freshness and recorded results; no Git, locks or writes."""
    core = _core()
    validate(state)
    reader = reads(budget)
    bindings = _Bindings(program, state, reader)
    history: list[Json] = []
    current: Record | None = None
    for annotation in annotation_records(state):
        freshness = "current"
        reasons: list[Json] = []
        try:
            _current(program, state, annotation, reader, bindings)
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
    links_by_ordinal: dict[int, list[Record]] = {}
    uncovered_by_ordinal: dict[int, list[Json]] = {}
    if annotation is not None:
        for value in coordination._items(annotation["links"], "links"):
            link = coordination._record(value, "link")
            links_by_ordinal.setdefault(link["criterion"]["ordinal"], []).append(link)
        for value in coordination._items(annotation.get("uncovered", []), "uncovered"):
            uncovered_by_ordinal.setdefault(value["criterion"]["ordinal"], []).append(value)
    for ordinal, text in enumerate(criteria):
        links = deepcopy(links_by_ordinal.get(ordinal, []))
        uncovered = deepcopy(uncovered_by_ordinal.get(ordinal, []))
        observations: list[Json] = []
        for link in links:
            for value in coordination._items(link.get("records", []), "records"):
                artifact = coordination._record(value, "record")
                ref = reference(artifact["receipt"])
                captured = _metadata(program, ref, reader.artifacts)
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
                completion = _metadata(program, ref, reader.artifacts)
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
        "integrated_review": assess_integrated_completion(
            program,
            state,
            verification_facts=verification_facts,
            budget=reader.artifacts,
            observed_head=observed_head,
        ),
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
    if args.operation is None or args.input is None:
        result["reasons"] = ["An operation and its actual receipt are required."]
        return result
    if structural["status"] == "failed":
        result.update(structural)
        return result
    try:
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
