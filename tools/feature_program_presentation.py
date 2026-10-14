"""Reviewed, source-bound presentation; original records remain the only authority.

Typed targets are enumerated from known record families, never resolved from a caller's
JSON path. Reads share the snapshot evidence budget and never acquire publication capabilities.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
from types import ModuleType
from typing import TYPE_CHECKING, Final

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
DOCUMENT_LIMIT: Final = 1024 * 1024
OUTPUT_LIMIT: Final = 8 * 1024 * 1024
PREFIX: Final = "evidence/_presentation/attempt-1/"
READ_COMMANDS: Final = frozenset({"presentation-inventory", "presentation-binding"})
NARRATIVE: Final = frozenset(
    {
        "reason",
        "summary",
        "note",
        "next_action",
        "goal",
        "constraints",
        "decisions",
        "findings",
        "unresolved_findings",
        "unresolved_acceptance",
        "unresolved_blockers",
        "limitations",
        "description",
        "basis",
        "focus",
        "team_state",
        "criterion",
        "planning_note",
    }
)
# These containers carry human narrative, but their machine-valued leaves do not.
CONTAINERS: Final = frozenset(
    {
        "history",
        "acknowledgement",
        "acceptance",
        "outgoing",
        "authority",
        "observation",
        "cleanup",
        "completion",
        "checks",
        "criteria_results",
        "uncovered",
        "review",
        "blocks",
        "releases",
        "resumes",
        "reworks",
        "reopens",
        "decisions",
        "stages",
        "receipt",
        "metadata",
        "inventory",
        "acknowledgements",
        "acceptance_evidence",
        "changes",
    }
)
FAMILIES: Final = {
    "F01": "objective",
    "F02": "objective_criterion",
    "F03": "objective_record",
    "F04": "workstream",
    "F05": "category",
    "F06": "task_title",
    "F07": "task_content",
    "F08": "task_action",
    "F09": "task_note",
    "F10": "task_history",
    "F11": "receipt_narrative",
    "F12": "question",
    "F13": "question_outcome",
    "F14": "actor_alias",
    "F15": "staffing",
    "F16": "handoff",
    "F17": "delivery",
    "F18": "planning",
    "F19": "coverage",
    "F20": "checkpoint",
    "F21": "system_copy",
    "F22": "original_only",
}


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def require(condition: bool, message: str) -> None:
    _core().require(condition, "Presentation: " + message)


def _pointer(*parts: str | int) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def target_key(target: Record) -> str:
    return _core().digest(target)


def _budget_room(path: Path, budget: ReadBudget) -> None:
    """An aggregate-budget rejection must not turn into missing translation coverage."""
    try:
        size = path.stat().st_size if path not in (budget.cached or {}) and path.is_file() else 0
    except OSError:
        return  # The bounded reader reports this artifact's availability separately.
    require(
        min(size, DOCUMENT_LIMIT) <= budget.remaining, "Aggregate evidence read budget exceeded"
    )


def _protected(text: str) -> list[str] | None:
    """Conservatively preserve fenced/inline code and Markdown link destinations.

    Unbalanced code or nontrivial raw HTML is not safely segmentable here. Its original
    remains readable and its inventory disposition explains why it cannot be replaced.
    """
    if re.search(r"<[/!A-Za-z][^>]*>", text):
        return None
    pattern = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+|!?\[[^\]]*\]\(([^\n)]*)\)")
    spans: list[str] = []
    remainder = text
    for match in reversed(list(pattern.finditer(text))):
        spans.insert(0, match.group(1) if match.group(1) is not None else match.group())
        remainder = remainder[: match.start()] + remainder[match.end() :]
    if "`" in remainder or "~~~" in remainder or "](" in remainder:
        return None
    return spans


@dataclass(frozen=True, slots=True)
class Catalog:
    """One bounded observation; targets share detached source descriptors read-only.

    Public projections copy each returned row independently from this local accumulator.
    """

    program: Program
    state: Record
    budget: ReadBudget
    revision: str
    targets: dict[str, Record] = field(default_factory=dict)
    unavailable: list[Record] = field(default_factory=list)

    def add(
        self,
        family: str,
        entity: str,
        name: str,
        value: Json,
        pointer: str,
        *,
        context: Record | None = None,
        historical: bool = False,
        origin: Record | None = None,
        ordinal: int | None = None,
    ) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        core = _core()
        target: Record = {"kind": FAMILIES[family], "entity_id": entity, "field": name}
        if ordinal is not None:
            target["ordinal"] = ordinal
        # Stable typed record identity. The pointer is server-enumerated, never evaluated.
        if family not in {"F01", "F02", "F04", "F05", "F06", "F07", "F08", "F12", "F14"}:
            target["record_pointer"] = pointer
        if origin is not None and family != "F14":
            target["source_path"] = origin["path"]
        source = origin or {"path": "backlog.json", "revision": self.revision}
        source = {**source, "pointer": pointer}
        disposition = "eligible" if _protected(value) is not None else "original_only"
        row: Record = {
            "key": target_key(target),
            "family": family,
            "target": target,
            "original": value,
            "origin": source,
            "historical": historical,
            "disposition": disposition,
            "binding": {
                "program_id": self.state["program_id"],
                "source_value_sha256": core.digest(value),
                "context": context or {},
            },
        }
        if disposition == "original_only":
            row["reason"] = "Mixed code/markup cannot be safely segmented; retain original"
        if row["key"] in self.targets and self.targets[str(row["key"])] == row:
            return
        require(row["key"] not in self.targets, "Ambiguous typed target")
        self.targets[str(row["key"])] = row

    def prose(
        self,
        family: str,
        entity: str,
        value: Json,
        pointer: str,
        *,
        context: Record | None = None,
        historical: bool = False,
        origin: Record | None = None,
    ) -> None:
        """Walk only declared narrative leaves and containers in a typed record family."""
        if isinstance(value, list):
            for index, item in enumerate(value):
                self.prose(
                    family,
                    entity,
                    item,
                    pointer + f"/{index}",
                    context=context,
                    historical=historical,
                    origin=origin,
                )
        elif isinstance(value, dict):
            record_context = {**(context or {}), "record_sha256": _core().digest(value)}
            for name, item in value.items():
                child = pointer + _pointer(name)
                if name in NARRATIVE:
                    if isinstance(item, list):
                        for index, text in enumerate(item):
                            self.add(
                                family,
                                entity,
                                name,
                                text,
                                child + f"/{index}",
                                context=record_context,
                                historical=historical,
                                origin=origin,
                                ordinal=index,
                            )
                    elif isinstance(item, str):
                        self.add(
                            family,
                            entity,
                            name,
                            item,
                            child,
                            context=record_context,
                            historical=historical,
                            origin=origin,
                        )
                    elif isinstance(item, dict):
                        self.prose(
                            family,
                            entity,
                            item,
                            child,
                            context=record_context,
                            historical=historical,
                            origin=origin,
                        )
                elif name in CONTAINERS:
                    self.prose(
                        family,
                        entity,
                        item,
                        child,
                        context=record_context,
                        historical=historical,
                        origin=origin,
                    )


def catalog(program: Program, state: Record, budget: ReadBudget, revision: str) -> Catalog:
    """Enumerate every supported current/historical target without an entity sampling cap."""
    core = _core()
    result = Catalog(program, state, budget, revision)
    goals = [(state.get("objective"), "/objective", False)]
    goals += [
        (goal, f"/objective/history/{index}", True)
        for index, goal in enumerate((state.get("objective") or {}).get("history", []))
    ]
    for goal, path, historical in goals:
        if not isinstance(goal, dict):
            continue
        identifier = str(goal.get("id", "legacy"))
        for name in ("title", "goal"):
            result.add(
                "F01", identifier, name, goal.get(name), path + "/" + name, historical=historical
            )
        criteria = goal.get("criteria", [])
        for index, text in enumerate(criteria):
            if isinstance(text, str):
                result.add(
                    "F02",
                    identifier,
                    "text",
                    text,
                    path + f"/criteria/{index}",
                    ordinal=index,
                    historical=historical,
                    context={
                        "criteria_sha256": core.digest(criteria),
                        "text_sha256": core.sha(text.encode("utf-8")),
                    },
                )
        result.prose(
            "F03",
            identifier,
            {k: v for k, v in goal.items() if k != "history"},
            path,
            historical=historical,
        )
        result.prose(
            "F03",
            identifier,
            goal.get("membership_history", []),
            path + "/membership_history",
            historical=historical,
        )
    for family, collection, names in (
        ("F04", "workstreams", ("name", "title", "focus")),
        ("F05", "categories", ("title", "reason")),
    ):
        for index, entry in enumerate(state.get(collection, [])):
            for name in names:
                result.add(
                    family,
                    str(entry["id"]),
                    name,
                    entry.get(name),
                    _pointer(collection, index, name),
                )
    actors: dict[str, tuple[Record, str, Record | None]] = {}

    def remember_actor(identifier: Json, pointer: str, origin: Record | None = None) -> None:
        if isinstance(identifier, str) and identifier and identifier not in actors:
            actors[identifier] = (
                {"id": identifier, "role": "unknown", "origin": "recorded_actor_reference"},
                pointer,
                origin,
            )

    for index, actor in enumerate(state.get("actors", [])):
        actors[str(actor["id"])] = (actor, f"/actors/{index}/id", None)
        result.prose("F15", str(actor["id"]), actor, f"/actors/{index}")
    captured_sources: dict[int, Record] = {}
    for ti, task in enumerate(state.get("tasks", [])):
        identifier = str(task["id"])
        path = f"/tasks/{ti}"
        section = task["source"]
        source = planning.source_for_task(state, task)
        source_identity = id(source)
        if source_identity not in captured_sources:
            captured_sources[source_identity] = deepcopy(source)
        context = {
            "source": captured_sources[source_identity],
            "section_sha256": section.get("sha256", core.digest(section)),
        }
        result.add(
            "F06",
            identifier,
            "title",
            section.get("title"),
            path + "/source/title",
            context=context,
        )
        for name in ("expected_result", "acceptance_criteria"):
            value = task.get(name)
            if isinstance(value, list):
                for index, text in enumerate(value):
                    result.add(
                        "F07",
                        identifier,
                        name,
                        text,
                        path + f"/{name}/{index}",
                        context=context,
                        ordinal=index,
                    )
            else:
                result.add("F07", identifier, name, value, path + "/" + name, context=context)
        # The heading is the separately bound title; its task ID is not a sentence.
        result.add(
            "F07",
            identifier,
            "source_body",
            "".join(str(section.get("text", "")).splitlines(keepends=True)[1:]),
            path + "/source/text",
            context={**context, "projection_rule": "section_after_first_heading_v1"},
        )
        attempts = task.get("attempts", [])
        attempt_context = {"attempt": attempts[-1].get("number") if attempts else None}
        for name in ("planning_note", "next_action", "blocker", "reason"):
            result.add(
                "F08", identifier, name, task.get(name), path + "/" + name, context=attempt_context
            )
        result.prose("F09", identifier, task.get("notes", []), path + "/notes", historical=True)
        for ni, note in enumerate(task.get("notes", [])):
            remember_actor(note.get("actor"), path + f"/notes/{ni}/actor")
        for ai, attempt in enumerate(attempts):
            ap = path + f"/attempts/{ai}"
            ac = {"task_id": identifier, "attempt": attempt.get("number")}
            historical = ai < len(attempts) - 1 or attempt.get("status") in {"done", "released"}
            result.prose("F10", identifier, attempt, ap, context=ac, historical=historical)
            owner = attempt.get("owner")
            if isinstance(owner, str) and owner not in actors:
                actors[owner] = (
                    {"id": owner, "role": "unknown", "origin": "legacy_assignment"},
                    ap + "/owner",
                    None,
                )
            for ri, submission in enumerate(attempt.get("rounds", [])):
                for kind in ("receipt", "review"):
                    ref = submission.get(kind)
                    if not isinstance(ref, dict):
                        continue
                    artifact_path = program.path(core.string(ref.get("path"), "receipt path"))
                    _budget_room(artifact_path, budget)
                    try:
                        raw, observed = core.read_json_frozen(
                            artifact_path, limit=DOCUMENT_LIMIT, budget=budget
                        )
                        require(observed == ref.get("sha256"), "Source receipt changed")
                    except (core.ProgramError, OSError) as exc:
                        result.unavailable.append({"origin": deepcopy(ref), "reason": str(exc)})
                        continue
                    result.prose(
                        "F11",
                        identifier,
                        raw,
                        "",
                        context={**ac, "round": ri, "receipt": deepcopy(ref)},
                        historical=historical,
                        origin=deepcopy(ref),
                    )
                    for name in ("reviewer", "reviewer_id"):
                        remember_actor(raw.get(name), "/" + name, deepcopy(ref))
        for di, delivery in enumerate(task.get("deliveries", [])):
            result.prose(
                "F17",
                identifier,
                delivery,
                path + f"/deliveries/{di}",
                historical=di < len(task.get("deliveries", [])) - 1,
            )
    for qi, question in enumerate((state.get("questions") or {}).get("items", [])):
        identifier = str(question["id"])
        qp = f"/questions/items/{qi}"
        issued = question.get("issued", question)
        prefix = qp + "/issued" if "issued" in question else qp
        qc = {"version": question.get("version"), "issued_sha256": question.get("issued_sha256")}
        remember_actor(issued.get("actor_id"), prefix + "/actor_id")
        for name in ("prompt", "why", "requested_decision"):
            result.add(
                "F12",
                identifier,
                name,
                issued.get(name),
                prefix + "/" + name,
                context=qc,
                historical=question.get("status") in {"resolved", "withdrawn"},
            )
        options = issued.get("options", [])
        for index, text in enumerate(options):
            result.add(
                "F12",
                identifier,
                "option",
                text,
                prefix + f"/options/{index}",
                ordinal=index,
                context={**qc, "options_sha256": core.digest(options)},
                historical=question.get("status") in {"resolved", "withdrawn"},
            )
        for name in ("acknowledgement", "outcome"):
            result.prose("F13", identifier, question.get(name), qp + "/" + name, context=qc)
    for family, name in (
        ("F15", "director"),
        ("F16", "handoffs"),
        ("F18", "amendments"),
        ("F18", "phases"),
        ("F18", "source_intakes"),
        ("F18", "publication"),
        ("F19", "coverage_annotations"),
        ("F20", "checkpoint"),
    ):
        values = state.get(name)
        if isinstance(values, list):
            for index, item in enumerate(values):
                identifier = str(item.get("id", name)) if isinstance(item, dict) else name
                # Coverage stores the supplied annotation in a named wrapper.
                if name == "coverage_annotations" and isinstance(item, dict):
                    result.prose(
                        family, identifier, item.get("annotation"), f"/{name}/{index}/annotation"
                    )
                else:
                    result.prose(family, identifier, item, f"/{name}/{index}")
        else:
            result.prose(family, name, values, "/" + name)
    for index, event in enumerate(state.get("events", [])):
        # The transaction input/review already preserves this technical audit trail.
        # Translating its own publication reason would create an endless new target.
        if event.get("action") == "presentation-record":
            continue
        remember_actor(event.get("actor"), f"/events/{index}/actor")
        event_task = event.get("task_id") or event.get("id")
        result.prose(
            "F10",
            str(event_task or state["program_id"]),
            event,
            f"/events/{index}",
            context={"entity_type": "task" if event_task else "program"},
            historical=True,
        )
    result.add("F20", str(state["program_id"]), "title", state.get("title"), "/title")
    for identifier, (actor, path, origin) in actors.items():
        result.add(
            "F14",
            identifier,
            "label",
            identifier,
            path,
            origin=origin,
            context={
                "actor_id": identifier,
                "role": actor.get("role", "unknown"),
                "identity_origin": actor.get("origin", "actor_record"),
                **({"receipt": origin} if origin is not None else {}),
            },
        )
    return result


def _records(state: Record) -> list[Json]:
    ledger = state.get("presentation")
    return (
        []
        if ledger is None
        else _core().sequence(
            _core().record(ledger, "presentation").get("records"), "presentation records"
        )
    )


def _reference(value: Json) -> Record:
    core = _core()
    ref = core.record(value, "presentation reference")
    require(set(ref) == {"path", "sha256"}, "References require exact path/hash")
    core.safe_relative(core.string(ref.get("path"), "reference path"))
    require(bool(re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256")))), "Invalid reference hash")
    return ref


def evidence_references(state: Record) -> list[Json]:
    refs: list[Json] = []
    for entry in _records(state):
        refs.extend([entry["receipt"], entry["input"]["review"]])
        for operation in entry["input"]["operations"]:
            ref = operation["binding"]["context"].get("receipt")
            if ref is not None:
                refs.append(ref)
    return refs


def _source_receipts(state: Record) -> list[Record]:
    """Only the same execution/review receipts already hydrated by the snapshot."""
    return [
        submission[kind]
        for task in state.get("tasks", [])
        for attempt in task.get("attempts", [])
        for submission in attempt.get("rounds", [])
        for kind in ("receipt", "review")
        if isinstance(submission.get(kind), dict)
    ]


def _input(value: Json) -> Record:
    core = _core()
    receipt = core.record(value, "presentation input")
    require(
        set(receipt)
        == {
            "schema_version",
            "operation",
            "id",
            "program_id",
            "actor_id",
            "locale",
            "reason",
            "authors",
            "expected_revision",
            "operations",
            "review",
        },
        "Unexpected/missing input fields",
    )
    require(
        type(receipt["schema_version"]) is int
        and receipt["schema_version"] == 1
        and receipt["operation"] == "presentation-record",
        "Invalid schema/operation",
    )
    require(core.portable_task_id(core.string(receipt["id"], "presentation ID")), "Invalid ID")
    for name in ("program_id", "actor_id", "reason"):
        core.string(receipt[name], name)
    require(len(str(receipt["reason"])) <= 65536, "Reason exceeds 65536 characters")
    locale = core.communication_language(receipt["locale"])
    require(locale is not None, "A recorded language is required")
    authors = core.strings(receipt["authors"], "presentation authors")
    require(
        bool(authors) and len(authors) == len(set(authors)), "Authors must be nonempty and unique"
    )
    operations = core.sequence(receipt["operations"], "presentation operations")
    require(0 < len(operations) <= 256, "Transactions require 1..256 operations")
    seen: set[str] = set()
    for value in operations:
        op = core.record(value, "presentation operation")
        target = core.record(op.get("target"), "target")
        for name in ("kind", "entity_id", "field"):
            core.string(target.get(name), name)
        action = op.get("action")
        allowed = {"action", "target", "binding", "supersedes"}
        if action == "set":
            allowed.add("mode")
            require(
                isinstance(op.get("mode"), str)
                and op["mode"] in {"translated", "original", "alias"},
                "Invalid mode",
            )
            if op["mode"] != "original":
                allowed.add("text")
                text = core.string(op.get("text"), "presentation text")
                target = core.record(op.get("target"), "target")
                limit = (
                    128
                    if op["mode"] == "alias"
                    else (
                        500
                        if target.get("field") == "option"
                        else (512 if target.get("field") in {"title", "name", "label"} else 65536)
                    )
                )
                require(len(text) <= limit, f"Text exceeds {limit} characters")
                require(
                    op["mode"] != "alias" or target.get("kind") == "actor_alias",
                    "Alias mode is actor-only",
                )
                require(
                    target.get("kind") != "actor_alias" or op["mode"] == "alias",
                    "Actor replacement requires alias mode",
                )
        else:
            require(action == "withdraw", "Unknown action")
            require(isinstance(op.get("supersedes"), str), "Withdrawal requires a prior entry")
        require(set(op) == allowed, "Unexpected/missing operation fields")
        target = core.record(op["target"], "target")
        require(
            set(target)
            <= {"kind", "entity_id", "field", "ordinal", "record_pointer", "source_path"}
            and {"kind", "entity_id", "field"} <= set(target),
            "Invalid typed target fields",
        )
        require(
            isinstance(target.get("kind"), str)
            and target["kind"] in set(FAMILIES.values()) - {"system_copy", "original_only"},
            "Unsupported target kind",
        )
        for name in ("kind", "entity_id", "field"):
            core.string(target.get(name), name)
        if "ordinal" in target:
            require(
                type(target["ordinal"]) is int and target["ordinal"] >= 0, "Invalid target ordinal"
            )
        binding = core.record(op["binding"], "target binding")
        require(
            set(binding) == {"program_id", "source_value_sha256", "context"},
            "Invalid binding fields",
        )
        core.record(binding["context"], "binding context")
        require(binding["program_id"] == receipt["program_id"], "Binding program mismatch")
        require(
            bool(re.fullmatch(r"[0-9a-f]{64}", str(binding["source_value_sha256"]))),
            "Invalid source hash",
        )
        key = target_key(target)
        require(key not in seen, "Duplicate target in transaction")
        seen.add(key)
        require(
            op["supersedes"] is None or isinstance(op["supersedes"], str), "Invalid supersedes ID"
        )
    require(len(core.canonical(receipt)) <= DOCUMENT_LIMIT, "Input exceeds 1 MiB")
    return receipt


def validate(state: Record) -> None:
    """Validate durable structure without imposing live-source freshness on old records."""
    ledger = state.get("presentation")
    if ledger is None:
        return
    core = _core()
    require(
        isinstance(ledger, dict)
        and set(ledger) == {"schema_version", "records"}
        and type(ledger.get("schema_version")) is int
        and ledger["schema_version"] == 1,
        "Invalid ledger schema",
    )
    identifiers: set[str] = set()
    latest: dict[tuple[str, str], str] = {}
    for value in _records(state):
        entry = core.record(value, "presentation record")
        require(
            set(entry) == {"input", "receipt", "recorded_at", "recorded_by"},
            "Invalid ledger record",
        )
        receipt = _input(entry["input"])
        identifier = str(receipt["id"])
        require(identifier.casefold() not in identifiers, "Duplicate recorded ID")
        identifiers.add(identifier.casefold())
        require(receipt["program_id"] == state["program_id"], "Recorded program mismatch")
        require(entry["recorded_by"] == receipt["actor_id"], "Recording actor mismatch")
        coordination._timestamp(entry["recorded_at"], "presentation recording time")
        for ref in (entry["receipt"], receipt["review"]):
            require(
                str(_reference(ref)["path"]).startswith(PREFIX),
                "Receipt/review outside presentation namespace",
            )
        for op in receipt["operations"]:
            key = (str(receipt["locale"]), target_key(op["target"]))
            require(op["supersedes"] == latest.get(key), "Broken presentation supersession chain")
            latest[key] = identifier


def _latest(state: Record, locale: str | None) -> dict[str, tuple[int, Record, Record]]:
    result: dict[str, tuple[int, Record, Record]] = {}
    for index, entry in enumerate(_records(state)):
        if entry["input"]["locale"] == locale:
            for operation in entry["input"]["operations"]:
                result[target_key(operation["target"])] = (index, entry, operation)
    return result


def review_payload(receipt: Record) -> str:
    """The review itself cannot hash itself; all authored content remains in its payload."""
    return _core().digest(
        {key: value for key, value in receipt.items() if key not in {"review", "expected_revision"}}
    )


def _review(program: Program, receipt: Record, budget: ReadBudget) -> None:
    core = _core()
    ref = _reference(receipt["review"])
    require(str(ref["path"]).startswith(PREFIX), "Review outside presentation namespace")
    review, observed = core.read_json_frozen(
        program.path(str(ref["path"])), limit=DOCUMENT_LIMIT, budget=budget
    )
    require(observed == ref["sha256"], "Frozen presentation review changed")
    require(
        set(review)
        == {
            "schema_version",
            "operation",
            "program_id",
            "locale",
            "payload_sha256",
            "reviewer_id",
            "reviewer_role",
            "verdict",
            "unresolved_findings",
            "summary",
        },
        "Invalid independent review fields",
    )
    require(
        type(review["schema_version"]) is int
        and review["schema_version"] == 1
        and review["operation"] == "presentation-review",
        "Invalid review schema/operation",
    )
    require(
        review["program_id"] == receipt["program_id"]
        and review["locale"] == receipt["locale"]
        and review["payload_sha256"] == review_payload(receipt),
        "Review payload mismatch",
    )
    reviewer = core.string(review["reviewer_id"], "reviewer ID")
    require(
        reviewer not in [receipt["actor_id"], *receipt["authors"]]
        and review["reviewer_role"] == "independent",
        "Reviewer must be independent of director/authors",
    )
    require(
        review["verdict"] == "accepted" and review["unresolved_findings"] == [],
        "Independent review is not accepted",
    )
    core.string(review["summary"], "review summary")


def verify_history(program: Program, state: Record, budget: ReadBudget | None = None) -> None:
    core = _core()
    budget = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    program.verify_refs(evidence_references(state), budget)
    for entry in _records(state):
        value, observed = core.read_json_frozen(
            program.path(entry["receipt"]["path"]), limit=DOCUMENT_LIMIT, budget=budget
        )
        require(
            observed == entry["receipt"]["sha256"] and value == entry["input"],
            "Recorded input differs from frozen receipt",
        )
        _review(program, value, budget)


def bind_input(
    program: Program, state: Record, draft: Record, budget: ReadBudget | None = None
) -> Record:
    """Resolve requested typed targets; never read a caller-supplied target path."""
    core = _core()
    budget = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    require(len(core.canonical(draft)) <= DOCUMENT_LIMIT, "Input exceeds 1 MiB")
    locale = core.communication_language(draft.get("locale"))
    require(
        locale is not None and locale == state.get("communication_language"),
        "Locale must match recorded session language",
    )
    observed = catalog(program, state, budget, phases.revision(state))
    latest = _latest(state, locale)
    operations = core.sequence(draft.get("operations"), "presentation operations")
    require(0 < len(operations) <= 256, "Transactions require 1..256 operations")
    resolved: list[Json] = []
    for item in operations:
        operation = deepcopy(core.record(item, "operation"))
        key = target_key(core.record(operation.get("target"), "target"))
        withdrawing = operation.get("action") == "withdraw"
        require(
            key in observed.targets or (withdrawing and key in latest),
            "Unknown, unavailable or unsupported typed target",
        )
        row = observed.targets.get(key)
        require(withdrawing or row["disposition"] == "eligible", str((row or {}).get("reason")))
        operation["binding"] = deepcopy(
            row["binding"] if row is not None else latest[key][2]["binding"]
        )
        operation["supersedes"] = latest[key][1]["input"]["id"] if key in latest else None
        if operation.get("action") == "set" and operation.get("mode") != "original":
            require(isinstance(operation.get("text"), str), "Presentation text required")
            require(
                _protected(str(operation["text"])) == _protected(str(row["original"])),
                "Protected code/link spans changed",
            )
        resolved.append(operation)
    bound = {**deepcopy(draft), "expected_revision": phases.revision(state), "operations": resolved}
    _input(bound)
    require(bound["program_id"] == state["program_id"], "Program mismatch")
    return {
        "expected_revision": bound["expected_revision"],
        "operations": resolved,
        "review_payload_sha256": review_payload(bound),
    }


def prepare_record(program: Program, state: Record, name: str) -> PreparedChange:
    core = _core()
    program.require_current_location()
    program.validate(state)
    require(core.safe_relative(name).startswith(PREFIX), "Input outside presentation namespace")
    receipt, parsed = core.read_json_frozen(program.path(name), limit=DOCUMENT_LIMIT)
    _input(receipt)
    initial = core.bounded_bytes(program.path("backlog.json"), OUTPUT_LIMIT)
    require(
        core.read_json(program.path("backlog.json")) == state,
        "Original registry differs from supplied state",
    )

    def preconditions(current: Record) -> None:
        program.require_current_location()
        require(
            receipt["actor_id"] == coordination.current_director_id(current)
            and receipt["actor_id"] is not None,
            "Only current acknowledged director records presentation",
        )
        coordination.require_task_authority(current, str(receipt["actor_id"]))
        require(
            all(
                str(entry["input"]["id"]).casefold() != str(receipt["id"]).casefold()
                for entry in _records(current)
            ),
            "Duplicate transaction ID",
        )
        binding = bind_input(program, current, receipt)
        require(
            receipt["expected_revision"] == binding["expected_revision"], "Stale expected revision"
        )
        require(
            receipt["operations"] == binding["operations"], "Stale source binding or supersession"
        )
        _review(program, receipt, core.ReadBudget(64 * 1024 * 1024))

    preconditions(state)
    ref: Record = {"path": name, "sha256": parsed}
    proposal = deepcopy(state)
    ledger = proposal.setdefault("presentation", {"schema_version": 1, "records": []})
    ledger["records"].append(
        {
            "input": deepcopy(receipt),
            "receipt": ref,
            "recorded_by": receipt["actor_id"],
            "recorded_at": core.utc(),
        }
    )
    program.event(
        proposal,
        "presentation-record",
        {
            "actor": receipt["actor_id"],
            "presentation_id": receipt["id"],
            "reason": receipt["reason"],
            "receipt": ref,
        },
    )

    def before_publish() -> None:
        require(
            core.bounded_bytes(program.path("backlog.json"), OUTPUT_LIMIT) == initial,
            "Registry changed before presentation publication",
        )
        current = core.read_json(program.path("backlog.json"))
        program.validate(current)
        preconditions(current)
        verify_history(program, proposal)
        require(
            core.bounded_bytes(program.path("backlog.json"), OUTPUT_LIMIT) == initial,
            "Registry changed before presentation publication",
        )

    return core.PreparedChange(proposal, before_publish)


def projection(
    program: Program,
    state: Record,
    budget: ReadBudget | None = None,
    *,
    revision: str | None = None,
    locale: str | None = None,
    inventory: bool = False,
) -> Record:
    """Project only current verified text, with complete counts and compact missing coverage."""
    core = _core()
    validate(state)
    budget = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    revision = revision or phases.revision(state)
    selected = locale if locale is not None else state.get("communication_language")
    if selected is not None:
        core.communication_language(selected)
    observed = catalog(program, state, budget, revision)
    latest = _latest(state, selected)
    entries: Record = {}
    counts: Counter[str] = Counter()
    by_kind: dict[str, Counter[str]] = {}
    periods = {"current": Counter(), "historical": Counter()}
    availability: dict[int, str | None] = {}
    for key, (index, entry, operation) in latest.items():
        if index not in availability:
            for ref in (entry["receipt"], entry["input"]["review"]):
                _budget_room(program.path(str(ref["path"])), budget)
            try:
                receipt, digest = core.read_json_frozen(
                    program.path(entry["receipt"]["path"]), limit=DOCUMENT_LIMIT, budget=budget
                )
                require(
                    digest == entry["receipt"]["sha256"] and receipt == entry["input"],
                    "Frozen input differs from recorded presentation",
                )
                _review(program, receipt, budget)
                for value in receipt["operations"]:
                    ref = value["binding"]["context"].get("receipt")
                    if ref is not None:
                        program.verify_refs([ref], budget)
                availability[index] = None
            except (core.ProgramError, OSError) as exc:
                availability[index] = str(exc)
        row = observed.targets.get(key)
        reason = availability[index]
        if reason is not None:
            status = "unavailable"
        elif operation["action"] == "withdraw":
            status = "withdrawn"
        elif (
            row is None
            or row["binding"] != operation["binding"]
            or row["disposition"] != "eligible"
        ):
            status = "stale"
            reason = "Original target or bound source context changed"
        else:
            status = "current"
        projected: Record = {
            "target": deepcopy(operation["target"]),
            "binding": deepcopy(operation["binding"]),
            "status": status,
            "mode": operation.get("mode"),
            "transaction_id": entry["input"]["id"],
            "origin": deepcopy(row["origin"]) if row is not None else None,
            "record": {
                "path": "backlog.json",
                "pointer": f"/presentation/records/{index}",
                "revision": revision,
            },
            "receipt": deepcopy(entry["receipt"]),
            "review": deepcopy(entry["input"]["review"]),
        }
        if reason is not None:
            projected["reason"] = reason
        if status == "current" and operation["mode"] != "original":
            projected["text"] = operation["text"]
        entries[key] = projected
    inventory_rows: list[Json] = []
    for key, row in observed.targets.items():
        status = (
            "original_only"
            if row["disposition"] == "original_only"
            else (
                "current_original"
                if key in entries
                and entries[key]["status"] == "current"
                and entries[key]["mode"] == "original"
                else "current_translated"
                if key in entries and entries[key]["status"] == "current"
                else str(entries[key]["status"])
                if key in entries
                else "missing"
            )
        )
        counts[status] += 1
        counts["total"] += 1
        by_kind.setdefault(str(row["target"]["kind"]), Counter())[status] += 1
        period = periods["historical" if row["historical"] else "current"]
        period[status] += 1
        period["total"] += 1
        if inventory:
            inventory_rows.append(
                {
                    **deepcopy(row),
                    "status": status,
                    "latest_transaction_id": entries[key]["transaction_id"]
                    if key in entries
                    else None,
                }
            )
    orphaned = sum(key not in observed.targets for key in entries)
    result: Record = {
        "schema_version": 1,
        "revision": revision,
        "locale": selected,
        "language_source": "communication_language"
        if locale is None
        else "explicit_inventory_locale",
        "coverage_status": "unknown"
        if selected is None
        else (
            "complete"
            if counts["total"]
            == counts["current_original"] + counts["current_translated"] + counts["original_only"]
            and not observed.unavailable
            and not orphaned
            else "partial"
        ),
        "totals": {
            name: counts[name]
            for name in (
                "total",
                "current_translated",
                "current_original",
                "missing",
                "stale",
                "unavailable",
                "withdrawn",
                "original_only",
            )
        },
        "by_kind": {kind: dict(count) for kind, count in by_kind.items()},
        "periods": {kind: dict(count) for kind, count in periods.items()},
        "unavailable_origins": observed.unavailable,
        "orphaned_entries": orphaned,
        "technical_records": {
            "disposition": "original_only",
            "presentation_transactions": len(_records(state)),
            "presentation_audit_events": sum(
                event.get("action") == "presentation-record" for event in state.get("events", [])
            ),
            "reference": {"path": "backlog.json", "pointer": "/presentation", "revision": revision},
        },
        "entries": entries,
        "family_dispositions": [
            {
                "family": family,
                "kind": kind,
                "disposition": "ui_static_copy"
                if family == "F21"
                else "original_only"
                if family == "F22"
                else "typed_targets",
                "targets": sum(row["family"] == family for row in observed.targets.values()),
            }
            for family, kind in FAMILIES.items()
        ],
    }
    if inventory:
        result["targets"] = inventory_rows
    require(
        len(core.canonical(result)) <= OUTPUT_LIMIT,
        "Output exceeds 8 MiB; no truncated prefix returned",
    )
    return result


def read_command(program: Program, args: argparse.Namespace) -> Record:
    """Read one coherent registry and bounded artifacts; no Git, lock or filesystem write."""
    core = _core()
    program.require_current_location()
    path = program.path("backlog.json")
    initial = core.bounded_bytes(path, OUTPUT_LIMIT)
    budget = core.ReadBudget(64 * 1024 * 1024, cached={path: initial})
    state = core.read_json(path, budget=budget)
    program.validate(state, verify_artifacts=False)
    require(len(state["tasks"]) <= 2000, "Inventory exceeds 2000 tasks")
    absent = {
        program.path(ref["path"])
        for ref in [*evidence_references(state), *_source_receipts(state)]
        if not program.path(ref["path"]).is_file()
    }
    if args.command == "presentation-inventory":
        result = projection(
            program, state, budget, revision=core.sha(initial), locale=args.locale, inventory=True
        )
    else:
        draft = core.read_json(program.path(args.input), limit=DOCUMENT_LIMIT, budget=budget)
        result = bind_input(program, state, draft, budget)
        result["observed_registry_sha256"] = core.sha(initial)
    require(len(core.canonical(result)) <= OUTPUT_LIMIT, "Output exceeds 8 MiB")
    for captured_path, captured in (budget.cached or {}).items():
        require(
            captured_path.is_file() and core.bounded_bytes(captured_path, OUTPUT_LIMIT) == captured,
            "Observed registry/evidence changed during presentation read; refresh",
        )
    require(
        not any(item.is_file() for item in absent),
        "Missing presentation evidence appeared during read; refresh",
    )
    program.require_current_location()
    return result


def add_commands(commands: argparse._SubParsersAction) -> None:
    inventory = commands.add_parser(
        "presentation-inventory",
        help="Inspect all typed human-content origins and reviewed coverage",
    )
    inventory.add_argument(
        "--locale", help="Inspect an exact locale without changing session language"
    )
    for name in ("presentation-binding", "presentation-record"):
        command = commands.add_parser(name)
        command.add_argument("--input", required=True, help="Program-relative presentation JSON")
