"""Reviewed additive source intake and active-objective membership transactions.

This standard-library extension keeps original intake and execution history intact.
The one canonical write is owned by Program.save; previews never publish state.
"""

# noqa: SIZE_OK - The approved ten-file scope keeps this transaction's source,
# prospective state, publication guard and provenance consumers in one module.

from __future__ import annotations

import argparse
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, Literal

if __package__:
    from tools import feature_program_coordination as coordination
    from tools import feature_program_phases as phases
else:
    import feature_program_coordination as coordination
    import feature_program_phases as phases

if TYPE_CHECKING:
    from tools.feature_program import Program, ReadBudget

Json = coordination.Json
Record = coordination.Record
Operation = Literal["task-admit", "objective-membership"]
COMMANDS: Final = frozenset({"task-admit", "objective-membership"})
TASK_FIELDS: Final = frozenset(
    {
        "id",
        "phase",
        "workstream",
        "candidate_paths",
        "depends_on",
        "related_to",
        "conflict_groups",
        "required_checks",
        "next_action",
        "planning_note",
        "expected_result",
        "acceptance_criteria",
    }
)
MARKERS: Final = frozenset({"SUCCESSION_INCOMPLETE.json", "RELOCATION_INCOMPLETE.json"})
SCAN_SKIP: Final = frozenset(
    {
        ".git",
        ".agents",
        ".codex",
        ".aws",
        ".codegraph",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "library",
        "temp",
        "obj",
        "bin",
        "logs",
        "build",
        "builds",
        "dist",
        "coverage",
        "generated",
        "third_party",
        "third-party",
        "external",
        "packages",
        "vendor",
        ".cache",
        ".mypy_cache",
        "test-results",
        "htmlcov",
    }
)


class PlanningError(ValueError):
    """An intake or membership amendment rejected before canonical publication."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanningError(message)


def _core() -> ModuleType:
    """Import lazily to share the actual CLI's core exception and path helpers."""
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def intakes(state: Record) -> dict[str, Record]:  # noqa: DICT_OK
    return coordination._indexed(state.get("source_intakes", []), "source intake")


def source_for_task(state: Record, task: Record) -> Record:
    """Resolve the actual original or supplemental frozen report for this task."""
    identifier = task.get("source_intake_id")
    if identifier is None:
        return coordination._record(state["source"], "original source")
    sources = intakes(state)
    require(identifier in sources, "Unknown task source intake")
    return coordination._record(sources[identifier]["source"], "supplemental source")


def descriptors(state: Record) -> list[Record]:
    return [
        coordination._record(state["source"], "original source"),
        *[
            coordination._record(item["source"], "supplemental source")
            for item in intakes(state).values()
        ],
    ]


def _hash(value: Json, label: str) -> str:
    text = coordination._text(value, label)
    require(bool(re.fullmatch(r"[a-f0-9]{64}", text)), f"Invalid {label}")
    return text


def planning_digest(state: Record) -> Json:
    """Keep single-source bindings compatible; additive bindings exclude path rewrites."""
    sources = intakes(state)
    if not sources:
        return state.get("planning_sha256")
    return phases.revision(
        {
            "original": state["planning_sha256"],
            "intakes": [
                {
                    "id": item["id"],
                    "source_sha256": coordination._record(item["source"], "source")["sha256"],
                    "planning_sha256": coordination._record(item["planning"], "planning")["sha256"],
                    "task_ids": coordination._record(item["source"], "source")["task_ids"],
                }
                for item in sources.values()
            ],
        }
    )


def evidence_references(state: Record) -> list[Json]:
    """Expose immutable amendment receipts to normal integrity and snapshot consumers."""
    refs: list[Json] = []
    for item in coordination._items(state.get("amendments", []), "amendments"):
        refs.extend(
            coordination._items(
                coordination._record(item, "amendment").get("evidence"), "amendment evidence"
            )
        )
    for value in refs:
        ref = coordination._record(value, "amendment evidence")
        coordination._relative(ref.get("path"))
        _hash(ref.get("sha256"), "amendment evidence hash")
    return refs


def verify_planning(program: Program, state: Record, budget: ReadBudget | None = None) -> None:
    """Verify every immutable supplemental planning input with a shared read budget."""
    core = _core()
    reader = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    if intakes(state):
        phase_state = coordination._record(state.get("phases"), "adopted phases")
        original = coordination._relative(
            state.get("planning_path", phase_state.get("planning_path"))
        )
        require(
            core.sha(reader.read(core.safe_path(program.repo, original), 8 * 1024 * 1024))
            == state["planning_sha256"],
            "Original frozen planning input drifted",
        )
    for item in intakes(state).values():
        plan = coordination._record(item.get("planning"), "supplemental planning")
        path = core.safe_path(program.repo, coordination._relative(plan.get("path")))
        require(
            core.sha(reader.read(path, 8 * 1024 * 1024))
            == _hash(plan.get("sha256"), "planning hash"),
            "Supplemental planning input drifted",
        )


def source_sections(
    program: Program,
    state: Record,
    verify: bool,
    budget: ReadBudget | None = None,
    *,
    prospective: bool = False,
) -> Record:
    """Validate the disjoint, lossless frozen source union, including structural reads."""
    core = _core()
    tasks = coordination._indexed(state.get("tasks"), "task")
    sources = intakes(state)
    require(len(sources) == len({name.casefold() for name in sources}), "Source intake IDs collide")
    amendments = coordination._indexed(state.get("amendments", []), "amendment")
    require(
        len(amendments) == len({name.casefold() for name in amendments}), "Amendment IDs collide"
    )
    for identifier, item in sources.items():
        require(core.portable_task_id(identifier), "Nonportable intake ID")
        require(
            identifier in amendments and amendments[identifier].get("operation") == "task-admit",
            "Source intake lacks admission history",
        )
        coordination._timestamp(item.get("recorded_at"), "intake timestamp")
        coordination._text(item.get("recorded_by"), "intake actor")
        coordination._text(item.get("objective_id"), "intake objective")
        plan = coordination._record(item.get("planning"), "supplemental planning")
        core.safe_path(program.repo, coordination._relative(plan.get("path")))
        _hash(plan.get("sha256"), "supplemental planning hash")
    result: Record = {}
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for source in descriptors(state):
        name = coordination._relative(source.get("path"))
        core.safe_path(program.repo, name)
        core.safe_path(program.repo, coordination._relative(source.get("reference_base")))
        coordination._text(source.get("review_baseline"), "review baseline")
        expected_hash = _hash(source.get("sha256"), "source hash")
        require(name.casefold() not in seen_paths, "Source paths must be disjoint")
        seen_paths.add(name.casefold())
        members = [key for key, task in tasks.items() if source_for_task(state, task) is source]
        declared = coordination._texts(source.get("task_ids", members), "frozen task IDs")
        require(
            bool(declared)
            and len(declared) == len(set(declared))
            and set(declared) == set(members),
            "Frozen source task identities drifted",
        )
        require(
            not seen_ids.intersection(key.casefold() for key in declared), "Source task IDs collide"
        )
        seen_ids.update(key.casefold() for key in declared)
        if verify:
            reader = budget if budget is not None else core.ReadBudget()
            raw = reader.read(core.safe_path(program.repo, name), 8 * 1024 * 1024)
            require(
                core.sha(raw) == expected_hash,
                "Original report drifted; do not reset progress"
                if source is state["source"]
                else "Supplemental report drifted; preserve intake history",
            )
            sections = core.extract_sections(raw, declared)
        else:
            sections = {key: tasks[key]["source"] for key in declared}
        result.update(sections)
    require(set(result) == set(tasks), "Registry must contain exact unique reviewed IDs")
    for item in amendments.values():
        require(item.get("operation") in COMMANDS, "Unknown amendment operation")
        coordination._text(item.get("actor_id"), "amendment actor")
        coordination._text(item.get("reason"), "amendment reason")
        coordination._timestamp(item.get("recorded_at"), "amendment timestamp")
        coordination._record(item.get("before_binding"), "before binding")
        coordination._record(item.get("after_binding"), "after binding")
        require(
            prospective or bool(coordination._items(item.get("evidence"), "amendment evidence")),
            "Committed amendment lacks frozen review/input evidence",
        )
    objective = state.get("objective")
    if objective is not None:
        for value in coordination._items(
            coordination._record(objective, "objective").get("membership_history", []),
            "membership history",
        ):
            member = coordination._record(value, "membership revision")
            if member.get("revision") != 0:
                require(
                    member.get("amendment_id") in amendments,
                    "Membership revision lacks amendment provenance",
                )
    evidence_references(state)
    if verify:
        verify_planning(program, state, budget)
    return result


def observe_sources(program: Program, state: Record) -> list[str]:
    """Observe missing/drifted source and planning bytes without mutating recorded state."""
    core = _core()
    budget = core.ReadBudget(64 * 1024 * 1024)
    warnings: list[str] = []
    tasks = coordination._indexed(state["tasks"], "task")
    for index, source in enumerate(descriptors(state)):
        label = "Original source" if index == 0 else f"Supplemental source {index}"
        path = core.safe_path(program.repo, coordination._relative(source["path"]))
        if not path.is_file():
            warnings.append(f"{label} is unavailable; displaying frozen recorded prose.")
            continue
        raw = budget.read(path, 8 * 1024 * 1024)
        if core.sha(raw) != source["sha256"]:
            warnings.append(f"{label} drifted; displaying frozen recorded prose.")
            continue
        members = [key for key, task in tasks.items() if source_for_task(state, task) is source]
        sections = core.extract_sections(raw, members)
        require(
            all(tasks[key]["source"] == sections[key] for key in members),
            "Frozen source sections do not match the unchanged original report",
        )
    plans = [coordination._record(item["planning"], "planning") for item in intakes(state).values()]
    original = state.get("planning_path")
    if original is not None:
        plans.insert(0, {"path": original, "sha256": state["planning_sha256"]})
    for plan in plans:
        path = core.safe_path(program.repo, coordination._relative(plan["path"]))
        if not path.is_file():
            warnings.append(f"Frozen planning input unavailable: {plan['path']}")
        elif core.sha(budget.read(path, 8 * 1024 * 1024)) != plan["sha256"]:
            warnings.append(f"Frozen planning input drifted: {plan['path']}")
    return warnings


def require_completed_publication(program: Program, state: Record) -> None:
    """Rare definition mutations also inspect other-endpoint incomplete publications."""
    core = _core()
    program.require_current_location()
    aliases = {program.directory.relative_to(program.repo).as_posix()}
    for value in coordination._items(state.get("events", []), "events"):
        event = coordination._record(value, "event")
        if event.get("action") == "relocate":
            aliases.add(coordination._relative(event.get("source_directory")))
    visited = 0
    entries = 0
    marker_budget = core.ReadBudget(8 * 1024 * 1024)

    def failed_scan(exc: OSError) -> None:
        raise PlanningError(
            f"Publication marker discovery cannot inspect {exc.filename}; inspect program locations before retry"
        ) from exc

    for folder, dirs, files in os.walk(program.repo, followlinks=False, onerror=failed_scan):
        visited += 1
        entries += len(dirs) + len(files)
        require(
            visited <= 10000 and entries <= 100000,
            "Publication marker discovery budget exceeded; inspect program locations and excluded trees before retry",
        )
        base = Path(folder)
        selected: list[str] = []
        for name in dirs:
            path = base / name
            if (
                name.casefold() in SCAN_SKIP
                or path.is_symlink()
                or getattr(path, "is_junction", lambda: False)()
            ):
                continue
            relative = path.relative_to(program.repo).as_posix()
            if relative.casefold().endswith("assets/resources/gamedata"):
                continue
            try:
                core.safe_path(program.repo, relative)
            except core.ProgramError:
                continue
            selected.append(name)
        dirs[:] = selected
        for name in MARKERS.intersection(files):
            relative_marker = (base / name).relative_to(program.repo).as_posix()
            try:
                marker = core.read_json(
                    core.safe_path(program.repo, relative_marker),
                    limit=64 * 1024,
                    budget=marker_budget,
                )
                fields = (
                    ("predecessor", "successor")
                    if name == "SUCCESSION_INCOMPLETE.json"
                    else ("source", "destination")
                )
                endpoints = {coordination._relative(marker.get(field)) for field in fields}
            except (core.ProgramError, coordination.GovernanceError) as exc:
                raise PlanningError(
                    f"Cannot verify publication marker {relative_marker}: {exc}; inspect or recover that publication before retry"
                ) from exc
            require(
                not endpoints.intersection(aliases),
                f"Definition changes require completed publication: {base.relative_to(program.repo).as_posix()}/{name}; recover that publication first",
            )
    backlink = program.path("PREDECESSOR.json")
    if backlink.exists():
        marker = core.read_json(backlink, limit=64 * 1024)
        require(
            marker.get("schema_version") == 1
            and marker.get("successor_id") == state["program_id"]
            and coordination._relative(marker.get("successor")) in aliases,
            "Succession backlink identity conflicts; inspect completed publication before amendment",
        )
        predecessor = core.safe_path(
            program.repo, coordination._relative(marker.get("predecessor"))
        )
        counterpart = core.read_json(core.safe_path(predecessor, "SUCCEEDED.json"), limit=64 * 1024)
        require(
            counterpart == marker,
            "Succession receipts conflict; investigate predecessor before amendment",
        )
        require(
            not core.safe_path(predecessor, "SUCCESSION_INCOMPLETE.json").exists(),
            "Predecessor cleanup incomplete; recover succession before amendment",
        )


@dataclass(frozen=True, slots=True)
class Prospective:
    state: Record
    receipt: Record
    before_binding: Record
    after_binding: Record


def construct(program: Program, state: Record, receipt: Record) -> Prospective:
    """Parse an authorized proposal and build a fully validated future state in memory."""
    core = _core()
    require(receipt.get("schema_version") == 1, "Unsupported amendment receipt")
    operation = coordination._choice(receipt.get("operation"), set(COMMANDS), "amendment operation")
    identifier = coordination._text(receipt.get("id"), "amendment ID")
    require(core.portable_task_id(identifier), "Nonportable amendment ID")
    require(
        identifier.casefold()
        not in {
            key.casefold()
            for key in coordination._indexed(state.get("amendments", []), "amendment")
        },
        "Amendment already committed; inspect recorded result instead of duplicating",
    )
    require(
        receipt.get("expected_revision") == phases.revision(state),
        "Stale expected registry revision",
    )
    require(receipt.get("program_id") == state["program_id"], "Amendment program mismatch")
    actor = coordination._text(receipt.get("actor_id"), "amendment actor")
    require(
        actor == coordination.current_director_id(state), "Only current director may amend work"
    )
    coordination._text(receipt.get("reason"), "amendment reason")
    goal = coordination._record(state.get("objective"), "current objective")
    require(
        receipt.get("objective_id") == goal["id"] and goal.get("status") == "active",
        "Amendment requires exact active objective",
    )
    require(
        state.get("phases") is not None,
        "Explicit prior phase adoption and accepted baseline required",
    )
    first = next(iter(coordination._indexed(state["tasks"], "tasks")))
    phases.guard_execution(
        program,
        state,
        first,
        coordination._texts(program.task(state, first)["candidate_paths"], "scope"),
        membership_check=False,
    )
    proposal = deepcopy(state)
    before_binding = phases.planning_binding(state)
    additions: list[str] = []
    if operation == "task-admit":
        source = deepcopy(coordination._record(receipt.get("source"), "supplemental source"))
        plan = deepcopy(coordination._record(receipt.get("planning"), "supplemental planning"))
        path = core.safe_path(program.repo, coordination._relative(source.get("path")))
        raw = core.bounded_bytes(path, 8 * 1024 * 1024)
        require(
            core.sha(raw) == _hash(source.get("sha256"), "source hash"),
            "Supplemental source drifted",
        )
        planning_path = core.safe_path(program.repo, coordination._relative(plan.get("path")))
        metadata, captured = core.read_json_frozen(planning_path, limit=8 * 1024 * 1024)
        require(
            captured == _hash(plan.get("sha256"), "planning hash"),
            "Supplemental planning input drifted",
        )
        require(
            metadata.get("schema_version") == 1 and set(metadata) == {"schema_version", "tasks"},
            "Supplemental plan may only declare schema_version and tasks",
        )
        authors = coordination._texts(receipt.get("intake_authors"), "intake authors")
        require(
            bool(authors) and len(authors) == len(set(authors)),
            "Explicit unique intake authors required",
        )
        definitions = coordination._indexed(metadata.get("tasks"), "new task")
        additions = list(definitions)
        require(
            additions == coordination._texts(source.get("task_ids"), "supplemental IDs"),
            "Supplemental source and plan IDs must match exactly in order",
        )
        existing = {key.casefold() for key in coordination._indexed(state["tasks"], "task")}
        require(
            not existing.intersection(key.casefold() for key in additions),
            "Duplicate/case-colliding admitted task IDs",
        )
        sections = core.extract_sections(raw, additions)
        for key, task in definitions.items():
            require(
                set(task) <= TASK_FIELDS,
                "Supplemental task contains runtime or unsupported metadata",
            )
            require(
                bool(coordination._texts(task.get("candidate_paths"), "owned paths")),
                "Admitted task requires complete owned paths",
            )
            for name in coordination._texts(task["candidate_paths"], "owned paths"):
                path = core.safe_path(program.repo, name)
                require(
                    not any(part.casefold() == ".work" for part in Path(name).parts)
                    and not program.directory.is_relative_to(path),
                    "Private or program-wide admitted ownership is invalid",
                )
            coordination._text(task.get("expected_result"), "expected result")
            criteria = coordination._texts(
                task.get("acceptance_criteria"), "admitted acceptance criteria"
            )
            require(
                bool(criteria) and len(criteria) == len(set(criteria)),
                "Admitted task requires explicit unique acceptance criteria",
            )
            task.update(
                {
                    "source": sections[key],
                    "source_intake_id": identifier,
                    "status": "planned",
                    "owner": None,
                    "attempts": [],
                    "notes": [],
                }
            )
            coordination._items(proposal["tasks"], "tasks").append(task)
        coordination._items(proposal.setdefault("source_intakes", []), "source intakes").append(
            {
                "id": identifier,
                "source": source,
                "planning": plan,
                "objective_id": goal["id"],
                "recorded_by": actor,
                "recorded_at": core.utc(),
            }
        )
        _place_categories(proposal, receipt, additions)
    else:
        require(
            "source" not in receipt and "planning" not in receipt,
            "Membership-only amendments cannot invent source intake",
        )
    coordination.amend_membership(proposal, receipt, additions)
    after_binding = phases.planning_binding(proposal)
    event: Record = {
        "id": identifier,
        "operation": operation,
        "actor_id": actor,
        "reason": receipt["reason"],
        "objective_id": goal["id"],
        "recorded_at": core.utc(),
        "before_binding": before_binding,
        "after_binding": after_binding,
        "receipt": deepcopy(receipt),
        "evidence": [],
    }
    coordination._items(proposal.setdefault("amendments", []), "amendments").append(event)
    # Current gates are historical while the future source union is validated.
    core.Program.validate(program, proposal, prospective=True)
    return Prospective(proposal, receipt, before_binding, after_binding)


def _place_categories(state: Record, receipt: Record, additions: list[str]) -> None:
    categories = state.get("categories")
    placement = coordination._indexed(receipt.get("category_placements", []), "category placement")
    if categories is None:
        require(not placement, "Category placements require authored categories")
        return
    require(
        set(placement) == set(additions), "Every new task needs exactly one existing category leaf"
    )
    indexed = coordination._indexed(categories, "category")
    parents = {item.get("parent_id") for item in indexed.values()}
    for task_id, item in placement.items():
        category_id = coordination._text(item.get("category_id"), "category placement")
        require(
            category_id in indexed and category_id not in parents,
            "Placement requires existing category leaf",
        )
        coordination._items(
            indexed[category_id].setdefault("task_ids", []), "category task IDs"
        ).append(task_id)
    coordination._categories(state)


def preview(program: Program, state: Record, receipt: Record) -> Record:
    """Expose exact prospective bindings without locks, writes, or authorization grants."""
    original = phases.revision(state)
    future = construct(program, state, receipt)
    units = [
        {"id": key, "scope": sorted(coordination._texts(task["candidate_paths"], "paths"))}
        for key, task in sorted(coordination._indexed(future.state["tasks"], "task").items())
    ]
    result: Record = {
        "expected_revision": original,
        "before_binding": future.before_binding,
        "after_binding": future.after_binding,
        "units": units,
        "membership": receipt["membership"],
    }
    if receipt.get("inventory") is not None:
        spec = coordination._record(receipt["inventory"], "prospective inventory")
        result["inventory_digest"] = phases.revision(
            {
                "binding": future.after_binding,
                "candidate_digest": spec.get("candidate_digest"),
                "units": units,
                "inventory_authors": spec.get("inventory_authors"),
            }
        )
    require(phases.revision(program.load()) == original, "Registry changed during preview; refresh")
    return result


def execute(program: Program, state: Record, args: argparse.Namespace) -> bool:
    """Commit exactly one reviewed prospective transaction under the caller's lock."""
    if args.command not in COMMANDS:
        return False
    core = _core()
    require_completed_publication(program, state)
    receipt, refs = program.frozen_json("_planning", 1, args.input)
    require(receipt.get("operation") == args.command, "Amendment operation mismatch")
    future = construct(program, state, receipt)
    require(
        receipt.get("before_binding") == future.before_binding
        and receipt.get("after_binding") == future.after_binding,
        "Stale before/after scope binding",
    )
    renewal = coordination._record(receipt.get("renewal"), "prospective phase renewal")
    additional = program.evidence(
        "_planning", 1, coordination._texts(receipt.get("evidence"), "amendment review evidence")
    )
    refs += additional
    phases.renew(
        program,
        future.state,
        renewal,
        phases.revision(state),
        coordination._texts(receipt.get("intake_authors", []), "intake authors"),
    )
    amendments = coordination._items(future.state["amendments"], "amendments")
    coordination._record(amendments[-1], "amendment")["evidence"] = refs
    goal = coordination._record(future.state["objective"], "objective")
    history = coordination._items(goal["membership_history"], "membership history")
    coordination._record(history[-1], "membership revision").update(
        {
            "evidence": refs,
            "before_binding": future.before_binding,
            "after_binding": future.after_binding,
        }
    )
    program.event(
        future.state,
        args.command,
        {
            "actor": receipt["actor_id"],
            "reason": receipt["reason"],
            "amendment_id": receipt["id"],
            "before_binding": future.before_binding,
            "after_binding": future.after_binding,
            "criterion_impact": "unknown",
        },
    )

    def precommit() -> None:
        require_completed_publication(program, state)
        require(
            phases.revision(core.read_json(program.path("backlog.json")))
            == receipt["expected_revision"],
            "Registry changed before admission publication",
        )
        program.verify_refs([*refs, *phases.evidence_references(future.state)])
        source_sections(program, future.state, True, core.ReadBudget(64 * 1024 * 1024))
        contract = coordination._record(future.state["phases"], "phases")
        phases._verify_planning(
            program, future.state, coordination._text(contract["planning_path"], "planning path")
        )

    program.save(future.state, before_publish=precommit)
    state.clear()
    state.update(future.state)
    return True


def remap_sources(state: Record, old_root: str, new_root: str) -> None:
    """Relocate current supplemental locators without rewriting historical receipts."""
    for item in intakes(state).values():
        source = coordination._record(item["source"], "source")
        planning = coordination._record(item["planning"], "planning")
        for target, key in ((source, "path"), (source, "reference_base"), (planning, "path")):
            name = coordination._relative(target[key])
            if name == old_root or name.startswith(old_root + "/"):
                target[key] = new_root + name[len(old_root) :]


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name in ("admission-binding", "task-admit", "objective-membership"):
        command = commands.add_parser(
            name, help="Reviewed prospective admission/membership contract"
        )
        command.add_argument(
            "--input", required=True, help="Program-relative proposal or frozen receipt"
        )
