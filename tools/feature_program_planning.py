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
import uuid
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, Iterator, Literal

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
Operation = Literal[
    "task-admit",
    "objective-membership",
    "task-plan",
    "task-prioritize",
    "task-split",
    "task-retire",
]
COMMANDS: Final = frozenset(
    {
        "task-admit",
        "objective-membership",
        "task-plan",
        "task-prioritize",
        "task-split",
        "task-retire",
    }
)
PLANNED_COMMANDS: Final = frozenset({"task-plan", "task-prioritize"})
DECOMPOSITION_COMMANDS: Final = frozenset({"task-split", "task-retire"})
PLANNED_FIELDS: Final = frozenset(
    {
        "candidate_paths",
        "depends_on",
        "related_to",
        "conflict_groups",
        "required_checks",
        "next_action",
        "planning_note",
        "phase",
        "workstream",
    }
)
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
PUBLICATION_MARKERS: Final = MARKERS | {"SUCCEEDED.json", "PREDECESSOR.json", "RELOCATED.json"}
STORE_PATH: Final = ".work/programs/.publications"
TRACKING: Final = "PUBLICATION_TRACKING.json"
MAX_PUBLICATIONS: Final = 4096
DOCUMENT_LIMIT: Final = 8 * 1024 * 1024


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


def _priority(value: Json) -> None:
    require(
        value is None or (type(value) is int and -(2**31) <= value < 2**31),
        "Priority must be null or a signed 32-bit integer",
    )


def _same_json(left: Json, right: Json) -> bool:
    """Compare canonical JSON values without Python's bool/int/float coercion."""
    return _core().canonical(left) == _core().canonical(right)


def task_order(state: Record) -> list[Record]:
    """Keep legacy ties until explicit activation, then use deterministic priority ties."""
    tasks = list(coordination._indexed(state["tasks"], "task").values())
    if "execution_priorities" not in state:
        return sorted(tasks, key=lambda task: task["phase"])
    priorities = coordination._record(state["execution_priorities"], "execution priorities")
    return sorted(
        tasks, key=lambda task: (priorities.get(task["id"], 0), task["phase"], task["id"])
    )


def _eligible(state: Record, identifier: str) -> Record:
    tasks = coordination._indexed(state["tasks"], "tasks")
    require(identifier in tasks, "Unknown planned target")
    task = tasks[identifier]
    goal = coordination._record(state["objective"], "objective")
    require(
        task["status"] == "planned"
        and task.get("owner") is None
        and task.get("disposition") in {None, "planned"}
        and identifier in coordination._texts(goal.get("task_ids"), "objective members"),
        "Planning target must be eligible, planned, unowned and a current member",
    )
    return task


def predecessor_binding(program: Program, state: Record, identifier: str) -> Record:
    """Bind the stable selected definition and its actual resolved frozen section."""
    core = _core()
    task = program.task(state, identifier)
    source = source_for_task(state, task)
    raw = core.bounded_bytes(core.safe_path(program.repo, str(source["path"])), DOCUMENT_LIMIT)
    require(core.sha(raw) == source["sha256"], "Predecessor frozen source drifted")
    sections = core.extract_sections(raw)
    require(
        identifier in sections and _same_json(sections[identifier], task["source"]),
        "Predecessor source-section mismatch",
    )
    definition = {
        key: deepcopy(value)
        for key, value in task.items()
        if key not in phases.TASK_PROGRESS_FIELDS
    }
    return {
        "definition_digest": core.digest(definition),
        "source": deepcopy(source),
        "section": deepcopy(sections[identifier]),
    }


def _decompose(
    program: Program, before: Record, proposal: Record, receipt: Record, additions: list[str]
) -> Record:
    """Compose explicit disposition, reciprocal lineage and eligible dependency patches."""
    predecessor = coordination._text(receipt.get("predecessor"), "predecessor")
    original = _eligible(before, predecessor)
    binding = predecessor_binding(program, before, predecessor)
    if "predecessor_before" in receipt:
        require(
            _same_json(receipt["predecessor_before"], binding),
            "Stale predecessor definition/source-section binding",
        )
    lineage = coordination._record(receipt.get("lineage"), "lineage")
    require(
        set(lineage) == {"predecessor", "successors"}
        and lineage["predecessor"] == predecessor
        and _same_json(lineage["successors"], additions),
        "Exact split lineage required",
    )
    require("changes" not in receipt, "Decomposition requires explicit dependency_changes")
    if receipt["operation"] == "task-split":
        require(len(additions) >= 2, "Split requires at least two genuinely new reviewed children")
    else:
        require(
            not additions
            and not {"source", "planning", "intake_authors", "category_placements"}.intersection(
                receipt
            ),
            "Retirement cannot admit source, children or category placements",
        )
    membership = coordination._record(receipt.get("membership"), "membership")
    members = coordination._texts(
        coordination._record(before["objective"], "objective")["task_ids"], "members"
    )
    require(
        _same_json(membership.get("before"), members)
        and _same_json(membership.get("added"), additions)
        and _same_json(membership.get("removed"), [predecessor])
        and set(coordination._texts(membership.get("after"), "after members"))
        == (set(members) - {predecessor}) | set(additions),
        "Decomposition requires exact predecessor/children membership delta",
    )
    tasks = coordination._indexed(proposal["tasks"], "tasks")
    changes = coordination._indexed(receipt.get("dependency_changes"), "dependency changes")
    for identifier, change in changes.items():
        require(
            set(change) == {"id", "before", "after", "reason"}, "Invalid dependency patch fields"
        )
        require(identifier != predecessor, "Retained predecessor dependencies cannot be rewritten")
        target = _eligible(before, identifier)
        coordination._text(change["reason"], "dependency rewrite reason")
        old = coordination._texts(change["before"], "before dependencies")
        new = coordination._texts(change["after"], "after dependencies")
        require(
            _same_json(old, target["depends_on"]) and not _same_json(old, new),
            "Stale or unchanged dependency patch",
        )
        require(len(new) == len(set(new)), "Duplicate rewritten dependencies")
        tasks[identifier]["depends_on"] = deepcopy(new)
    prior_lineage = original.get("lineage")
    ancestor = (
        None
        if prior_lineage is None
        else coordination._record(prior_lineage, "prior lineage")["predecessor"]
    )
    after_fields: list[Json] = [
        {
            "id": predecessor,
            "disposition": "split" if additions else "retired",
            "lineage": {"predecessor": ancestor, "successors": deepcopy(additions)},
        }
    ]
    for identifier in additions:
        after_fields.append(
            {
                "id": identifier,
                "disposition": None,
                "lineage": {"predecessor": predecessor, "successors": []},
            }
        )
    for value in after_fields:
        fields = coordination._record(value, "disposition fields")
        task = tasks[str(fields["id"])]
        task["lineage"] = deepcopy(fields["lineage"])
        if fields["disposition"] is not None:
            task["disposition"] = fields["disposition"]
    return {
        "predecessor": predecessor,
        "predecessor_before": binding,
        "lineage": deepcopy(lineage),
        "dependency_changes": deepcopy(receipt["dependency_changes"]),
        "before": [
            {
                "id": predecessor,
                "disposition": original.get("disposition"),
                "lineage": deepcopy(prior_lineage),
            }
        ],
        "after": after_fields,
    }


def _validate_dispositions(state: Record, latest: dict[tuple[str, str], Json]) -> None:
    """Keep disposed records historical and validate reciprocal, acyclic lineage."""
    tasks = coordination._indexed(state["tasks"], "tasks")
    for identifier, task in tasks.items():
        disposition = task.get("disposition")
        require(
            disposition is None
            or (isinstance(disposition, str) and disposition in {"planned", "split", "retired"}),
            "Unknown task disposition",
        )
        if disposition in {"split", "retired"}:
            require(
                task["status"] == "planned" and task.get("owner") is None,
                "Disposed task must remain planned and unowned",
            )
            require((identifier, "disposition") in latest, "Disposition lacks immutable history")
            goal = coordination._record(state.get("objective"), "objective")
            require(
                identifier not in coordination._texts(goal["task_ids"], "members"),
                "Disposed task cannot be a current objective member",
            )
        if task.get("lineage") is not None:
            require((identifier, "lineage") in latest, "Lineage lacks immutable history")
            lineage = coordination._record(task["lineage"], "task lineage")
            require(set(lineage) == {"predecessor", "successors"}, "Invalid task lineage fields")
            children = coordination._texts(lineage["successors"], "successors")
            require(
                len(children) == len(set(children)) and identifier not in children,
                "Duplicate/self successor",
            )
            require(
                (disposition == "split" and len(children) >= 2)
                or (disposition != "split" and not children),
                "Disposition/lineage mismatch",
            )
            parent = lineage["predecessor"]
            require(
                parent is None
                or (isinstance(parent, str) and parent in tasks and parent != identifier),
                "Unknown/self lineage predecessor",
            )
            if parent is not None:
                require(
                    identifier
                    in coordination._texts(
                        coordination._record(tasks[parent].get("lineage"), "parent lineage")[
                            "successors"
                        ],
                        "parent successors",
                    ),
                    "Nonreciprocal predecessor lineage",
                )
            for child in children:
                require(
                    child in tasks
                    and coordination._record(tasks[child].get("lineage"), "child lineage").get(
                        "predecessor"
                    )
                    == identifier,
                    "Nonreciprocal successor lineage",
                )
            seen = {identifier}
            while parent is not None:
                require(isinstance(parent, str) and parent in tasks, "Unknown lineage ancestor")
                require(parent not in seen, "Lineage cycle")
                seen.add(parent)
                ancestor_lineage = coordination._record(
                    tasks[parent].get("lineage"), "ancestor lineage"
                )
                require(
                    set(ancestor_lineage) == {"predecessor", "successors"},
                    "Invalid ancestor lineage",
                )
                parent = ancestor_lineage["predecessor"]
        if task["status"] != "done" and disposition not in {"split", "retired"}:
            require(
                all(
                    tasks[dependency].get("disposition") not in {"split", "retired"}
                    for dependency in coordination._texts(task["depends_on"], "dependencies")
                    if dependency in tasks
                ),
                "Executable task depends on disposed prerequisite",
            )


def validate_mutations(state: Record, *, prospective: bool = False) -> None:
    """Replay explicit patches; runtime action/history are independent of scope binding."""
    tasks = coordination._indexed(state["tasks"], "tasks")
    latest: dict[tuple[str, str], Json] = {}
    priorities: Record = {}
    activated = False
    for value in coordination._items(state.get("amendments", []), "amendments"):
        event = coordination._record(value, "amendment")
        if event.get("operation") in DECOMPOSITION_COMMANDS:
            receipt = coordination._record(event.get("receipt"), "decomposition receipt")
            detail = coordination._record(event.get("decomposition"), "decomposition history")
            for name in ("predecessor", "lineage", "dependency_changes"):
                require(
                    _same_json(detail.get(name), receipt.get(name)),
                    "Decomposition history/receipt mismatch",
                )
            binding = coordination._record(detail.get("predecessor_before"), "predecessor before")
            require(
                prospective or _same_json(binding, receipt.get("predecessor_before")),
                "Predecessor binding history/receipt mismatch",
            )
            _hash(binding.get("definition_digest"), "predecessor definition digest")
            predecessor = coordination._text(detail.get("predecessor"), "historical predecessor")
            require(predecessor in tasks, "Unknown historical predecessor")
            require(
                _same_json(binding.get("section"), tasks[predecessor]["source"]),
                "Historical predecessor source-section binding changed",
            )
            recorded_source = coordination._record(
                binding.get("source"), "historical predecessor source"
            )
            resolved_source = source_for_task(state, tasks[predecessor])
            require(
                _same_json(
                    {
                        key: value
                        for key, value in recorded_source.items()
                        if key not in {"path", "reference_base"}
                    },
                    {
                        key: value
                        for key, value in resolved_source.items()
                        if key not in {"path", "reference_base"}
                    },
                ),
                "Historical predecessor frozen source identity changed",
            )
            lineage = coordination._record(detail.get("lineage"), "historical lineage")
            children = coordination._texts(lineage.get("successors"), "historical successors")
            require(
                lineage.get("predecessor") == predecessor and len(children) == len(set(children)),
                "Invalid historical decomposition lineage",
            )
            if event["operation"] == "task-split":
                require(
                    len(children) >= 2
                    and event["id"] in intakes(state)
                    and _same_json(
                        coordination._record(
                            intakes(state)[str(event["id"])]["source"], "intake source"
                        )["task_ids"],
                        children,
                    ),
                    "Historical split requires exact actual child intake",
                )
            else:
                require(
                    not children and event["id"] not in intakes(state),
                    "Retirement cannot have child intake",
                )
            before_fields = coordination._indexed(detail.get("before"), "before disposition")
            require(set(before_fields) == {predecessor}, "Invalid predecessor before fields")
            old = before_fields[predecessor]
            require(
                set(old) == {"id", "disposition", "lineage"}
                and old["disposition"] in {None, "planned"},
                "Invalid previous disposition",
            )
            if old["lineage"] is not None:
                prior = coordination._record(old["lineage"], "previous lineage")
                require(
                    set(prior) == {"predecessor", "successors"}
                    and _same_json(prior["successors"], []),
                    "Previous executable lineage cannot have successors",
                )
            ancestor = (
                None
                if old["lineage"] is None
                else coordination._record(old["lineage"], "previous lineage")["predecessor"]
            )
            expected: list[Json] = [
                {
                    "id": predecessor,
                    "disposition": "split" if children else "retired",
                    "lineage": {"predecessor": ancestor, "successors": children},
                }
            ]
            expected += [
                {
                    "id": child,
                    "disposition": None,
                    "lineage": {"predecessor": predecessor, "successors": []},
                }
                for child in children
            ]
            require(_same_json(detail.get("after"), expected), "Disposition history mismatch")
            for name in ("disposition", "lineage"):
                key = (predecessor, name)
                if key in latest:
                    require(
                        _same_json(latest[key], old[name]), "Disposition history chain mismatch"
                    )
            for fields in expected:
                parsed = coordination._record(fields, "after disposition")
                identifier = str(parsed["id"])
                require(identifier in tasks, "Unknown historical child")
                for name in ("disposition", "lineage"):
                    latest[(identifier, name)] = parsed[name]
            for identifier, change in coordination._indexed(
                detail["dependency_changes"], "dependency change"
            ).items():
                require(
                    identifier in tasks
                    and identifier != predecessor
                    and identifier not in children,
                    "Invalid historical dependency target",
                )
                require(
                    set(change) == {"id", "before", "after", "reason"},
                    "Invalid historical dependency patch",
                )
                coordination._text(change["reason"], "dependency reason")
                old_dependencies = coordination._texts(change["before"], "old dependencies")
                new_dependencies = coordination._texts(change["after"], "new dependencies")
                require(
                    not _same_json(old_dependencies, new_dependencies),
                    "Unchanged historical dependency patch",
                )
                key = (identifier, "depends_on")
                if key in latest:
                    require(
                        _same_json(latest[key], old_dependencies), "Planning history chain mismatch"
                    )
                latest[key] = new_dependencies
            continue
        if event.get("operation") not in PLANNED_COMMANDS:
            continue
        receipt = coordination._record(event.get("receipt"), "planning receipt")
        require(
            _same_json(event.get("changes"), receipt.get("changes")),
            "Planning history/receipt mismatch",
        )
        require(
            _same_json(event.get("priority_before"), priorities if activated else None),
            "Priority history before-state mismatch",
        )
        changes = coordination._indexed(event.get("changes"), "planning change")
        require(bool(changes), "Planning mutation requires changes")
        for identifier, change in changes.items():
            require(identifier in tasks, "Unknown historical planning target")
            if event["operation"] == "task-prioritize":
                _priority(change.get("before"))
                _priority(change.get("after"))
                require(
                    _same_json(priorities.get(identifier), change["before"]),
                    "Priority history mismatch",
                )
                if change["after"] is None:
                    priorities.pop(identifier, None)
                else:
                    priorities[identifier] = change["after"]
                activated = True
            else:
                before = coordination._record(change.get("before"), "before fields")
                after = coordination._record(change.get("after"), "after fields")
                require(
                    bool(before) and set(before) == set(after) and set(after) <= PLANNED_FIELDS,
                    "Invalid historical planned fields",
                )
                for name, current in after.items():
                    key = (identifier, name)
                    if key in latest and name != "next_action":
                        require(
                            _same_json(latest[key], before[name]), "Planning history chain mismatch"
                        )
                    latest[key] = current
        require(
            _same_json(event.get("priority_after"), priorities if activated else None),
            "Priority history after-state mismatch",
        )
    require(
        _same_json(state.get("execution_priorities"), priorities if activated else None),
        "Effective priority differs from immutable planning history",
    )
    for (identifier, name), value in latest.items():
        if name != "next_action":
            require(
                _same_json(tasks[identifier].get(name), value),
                "Effective metadata differs from planning history",
            )
    _validate_dispositions(state, latest)


def _amend_planned(program: Program, proposal: Record, receipt: Record) -> None:
    core = _core()
    require(
        not {"membership", "source", "planning", "category_placements"}.intersection(receipt),
        "Planned mutations cannot amend membership, source or categories",
    )
    changes = coordination._indexed(receipt.get("changes"), "planning change")
    require(bool(changes), "Planning mutation requires changes")
    tasks = coordination._indexed(proposal["tasks"], "tasks")
    goal = coordination._record(proposal["objective"], "objective")
    priorities = deepcopy(
        coordination._record(proposal.get("execution_priorities", {}), "priorities")
    )
    original_tasks = deepcopy(tasks)
    for identifier, change in changes.items():
        require(set(change) == {"id", "before", "after"}, "Unsupported planning change fields")
        require(identifier in tasks, "Unknown planned target")
        task = tasks[identifier]
        require(
            task["status"] == "planned"
            and task.get("owner") is None
            and task.get("disposition") in {None, "planned"}
            and identifier in coordination._texts(goal.get("task_ids"), "objective members"),
            "Planning target must be eligible, planned, unowned and a current member",
        )
        if receipt["operation"] == "task-prioritize":
            _priority(change["before"])
            _priority(change["after"])
            require(
                _same_json(priorities.get(identifier), change["before"]), "Stale before priority"
            )
            if change["after"] is None:
                priorities.pop(identifier, None)
            else:
                priorities[identifier] = change["after"]
        else:
            before = coordination._record(change["before"], "before fields")
            after = coordination._record(change["after"], "after fields")
            require(
                bool(after) and set(before) == set(after) and set(after) <= PLANNED_FIELDS,
                "Only explicit whitelisted planned fields may change",
            )
            require(
                all(_same_json(task.get(name), value) for name, value in before.items()),
                "Stale before metadata",
            )
            require(not _same_json(before, after), "Planning metadata must actually change")
            for name in (
                "candidate_paths",
                "depends_on",
                "related_to",
                "conflict_groups",
                "required_checks",
            ):
                if name in after:
                    values = coordination._texts(after[name], name)
                    require(len(values) == len(set(values)), "Duplicate planned metadata values")
            if "candidate_paths" in after:
                require(bool(after["candidate_paths"]), "Complete candidate paths required")
                for name in coordination._texts(after["candidate_paths"], "candidate paths"):
                    path = core.safe_path(program.repo, name)
                    require(
                        not any(part.casefold() == ".work" for part in Path(name).parts)
                        and not program.directory.is_relative_to(path),
                        "Private or program-wide ownership is invalid",
                    )
            if "planning_note" in after:
                coordination._text(after["planning_note"], "planning note")
            task.update(deepcopy(after))
    if receipt["operation"] == "task-prioritize":
        require(
            "execution_priorities" not in proposal
            or not _same_json(priorities, proposal["execution_priorities"]),
            "Priority must actually change",
        )
        proposal["execution_priorities"] = priorities
    coordination._categories(proposal)
    for identifier in changes:
        for other in tasks.values():
            if other["id"] != identifier and other["status"] in core.OWNED_STATES:
                require(
                    set(program.collisions(tasks[identifier], other))
                    <= set(program.collisions(original_tasks[identifier], other)),
                    "Planned amendment conflicts with owned work",
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
            identifier in amendments
            and amendments[identifier].get("operation") in {"task-admit", "task-split"},
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
    validate_mutations(state, prospective=prospective)
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


def _present(path: Path) -> bool:
    """Only actual absence is absence; permission and other I/O errors remain errors."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _aliases(program: Program, state: Record) -> set[str]:
    result = {program.directory.relative_to(program.repo).as_posix()}
    for value in coordination._items(state.get("events", []), "events"):
        event = coordination._record(value, "event")
        if event.get("action") == "relocate":
            result.add(coordination._relative(event.get("source_directory")))
    return result


def _path_keys(names: list[str] | set[str]) -> set[str]:
    return {name.casefold() for name in names}


def validate_publication_endpoint(repo: Path, directory: Path) -> None:
    relative = directory.relative_to(repo).as_posix().casefold().rstrip("/")
    store = STORE_PATH.casefold()
    require(
        relative != store
        and not relative.startswith(store + "/")
        and not store.startswith(relative + "/"),
        "Program endpoint overlaps reserved publication store",
    )


def marker_inventory(repo: Path, endpoints: list[str], budget: ReadBudget) -> Record:
    """Inspect exactly named endpoint markers; no parent or repository traversal."""
    core = _core()
    result: Record = {}
    for endpoint in endpoints:
        directory = core.safe_path(repo, endpoint)
        observed: Record = {}
        for name in sorted(PUBLICATION_MARKERS):
            path = core.safe_path(directory, name)
            if _present(path):
                observed[name] = core.sha(budget.read(path, DOCUMENT_LIMIT))
        result[endpoint] = observed
    return result


@dataclass(frozen=True, slots=True)
class PublicationStore:
    """The one bounded repository locator; endpoint locks precede its nonblocking lock."""

    repo: Path

    def path(self, name: str) -> Path:
        return _core().safe_path(_core().safe_path(self.repo, STORE_PATH), name)

    @contextmanager
    def lock(self) -> Iterator[None]:
        directory = _core().safe_path(self.repo, STORE_PATH)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.path(".lock")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PlanningError("Publication ledger busy; retry, never steal its lock") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_core().canonical({"pid": os.getpid(), "created_at": _core().utc()}))
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            path.unlink()

    def load(self, *, create: bool = False) -> Record:
        core = _core()
        if not _present(self.path("ledger.json")):
            require(create, "Publication ledger missing; explicit reconciliation required")
            require(
                not _present(self.path("identity.json")),
                "Adopted publication ledger missing; restore original storage, never recreate it",
            )
            ledger: Record = {
                "schema_version": 1,
                "ledger_id": uuid.uuid4().hex,
                "active": [],
                "history": [],
            }
            self.save(ledger)
            self.freeze("identity.json", {"schema_version": 1, "ledger_id": ledger["ledger_id"]})
            return ledger
        ledger = core.read_json(self.path("ledger.json"))
        require(ledger.get("schema_version") == 1, "Unsupported publication ledger")
        require(
            bool(
                re.fullmatch(
                    r"[a-f0-9]{32}", coordination._text(ledger.get("ledger_id"), "ledger identity")
                )
            ),
            "Invalid publication ledger identity",
        )
        identity = {"schema_version": 1, "ledger_id": ledger["ledger_id"]}
        if _present(self.path("identity.json")):
            require(
                core.read_json(self.path("identity.json")) == identity,
                "Publication ledger/store identity conflict",
            )
        else:
            require(
                create and ledger.get("active") == [] and ledger.get("history") == [],
                "Publication store identity missing; restore original storage",
            )
            self.freeze("identity.json", identity)
        records = coordination._items(
            ledger.get("active"), "active publications"
        ) + coordination._items(ledger.get("history"), "publication history")
        require(len(records) <= MAX_PUBLICATIONS, "Publication ledger exceeds 4096 records")
        identifiers: set[str] = set()
        for value in records:
            item = coordination._record(value, "publication summary")
            identifier = coordination._text(item.get("id"), "publication ID")
            require(
                core.portable_task_id(identifier) and identifier.casefold() not in identifiers,
                "Invalid/duplicate publication ID",
            )
            identifiers.add(identifier.casefold())
            coordination._choice(item.get("kind"), {"succession", "relocation"}, "publication kind")
            coordination._choice(
                item.get("stage"),
                {
                    "registered",
                    "publishing",
                    "legacy-observed",
                    "completed",
                    "cancelled",
                    "superseded",
                },
                "publication stage",
            )
            endpoints = coordination._texts(item.get("endpoints"), "publication endpoints")
            require(
                len(endpoints) == 2 and len(_path_keys(endpoints)) == 2,
                "Publication needs two distinct endpoints",
            )
            for endpoint in endpoints:
                core.safe_relative(endpoint)
                validate_publication_endpoint(self.repo, self.repo / endpoint)
            ids = coordination._texts(item.get("program_ids"), "publication program identities")
            require(len(ids) == 2, "Publication needs both program identities")
            ref = coordination._record(item.get("receipt"), "publication receipt")
            require(
                coordination._relative(ref.get("path")).startswith(f"transactions/{identifier}/"),
                "Publication receipt identity mismatch",
            )
            _hash(ref.get("sha256"), "publication receipt hash")
            expected_stages = (
                {"registered", "publishing", "legacy-observed"}
                if value in coordination._items(ledger["active"], "active")
                else {"completed", "cancelled", "superseded"}
            )
            require(item["stage"] in expected_stages, "Publication active/history stage conflict")
            if item["stage"] in {"completed", "cancelled", "superseded"}:
                result = coordination._record(item.get("result"), "publication result")
                require(
                    coordination._relative(result.get("path")).startswith("transactions/"),
                    "Publication result outside immutable transactions",
                )
                _hash(result.get("sha256"), "publication result hash")
        return ledger

    def save(self, ledger: Record) -> None:
        require(
            len(
                coordination._items(ledger["active"], "active")
                + coordination._items(ledger["history"], "history")
            )
            <= MAX_PUBLICATIONS,
            "Publication ledger exceeds 4096 records",
        )
        encoded = _core().canonical(ledger)
        require(len(encoded) <= DOCUMENT_LIMIT, "Publication ledger exceeds 8 MiB")
        reserved = deepcopy(ledger)
        for value in coordination._items(reserved["active"], "active"):
            item = coordination._record(value, "publication")
            identifier = coordination._text(item["id"], "publication ID")
            item.update(
                {
                    "result": {
                        "path": f"transactions/{identifier}/reconciled-{'f' * 64}.json",
                        "sha256": "f" * 64,
                    },
                    "replacement_id": "f" * 64,
                    "failed_marker_sha256": "f" * 64,
                }
            )
        require(
            len(_core().canonical(reserved)) <= DOCUMENT_LIMIT,
            "Publication ledger exceeds 8 MiB including reserved finalization metadata",
        )
        _core().atomic_write(self.path("ledger.json"), encoded)

    def freeze(self, name: str, value: Record) -> Record:
        core = _core()
        encoded = core.canonical(value)
        require(len(encoded) <= DOCUMENT_LIMIT, "Publication document exceeds 8 MiB")
        path = self.path(name)
        if _present(path):
            require(
                core.bounded_bytes(path, DOCUMENT_LIMIT) == encoded,
                "Immutable publication receipt conflicts",
            )
        else:
            core.atomic_write(path, encoded)
        return {"path": name, "sha256": core.sha(encoded)}

    def payload(self, item: Record, budget: ReadBudget) -> Record:
        ref = coordination._record(item["receipt"], "publication receipt")
        path = self.path(coordination._relative(ref["path"]))
        require(
            _core().sha(budget.read(path, DOCUMENT_LIMIT)) == ref["sha256"],
            "Frozen publication receipt changed",
        )
        result = _core().read_json(path, budget=budget)
        require(
            result.get("id") == item["id"]
            and result.get("kind") == item["kind"]
            and result.get("endpoints") == item["endpoints"]
            and result.get("program_ids") == item["program_ids"],
            "Publication receipt/summary identity conflict",
        )
        coordination._record(result.get("marker"), "registered publication marker")
        if item["stage"] == "registered":
            before = coordination._record(
                result.get("before_markers"), "pre-publication marker inventory"
            )
            require(
                set(before) == set(item["endpoints"]), "Pre-publication inventory endpoint conflict"
            )
            for observed in before.values():
                for name, expected in coordination._record(observed, "observed marker").items():
                    require(
                        name in PUBLICATION_MARKERS, "Unsupported publication inventory filename"
                    )
                    _hash(expected, "observed marker hash")
        return result

    def active(self, ledger: Record, identifier: str) -> Record:
        matches = [
            coordination._record(value, "active publication")
            for value in coordination._items(ledger["active"], "active")
            if coordination._record(value, "publication")["id"] == identifier
        ]
        require(len(matches) == 1, "Active publication identity missing or ambiguous")
        return matches[0]


def _tracking(program: Program, ledger: Record, budget: ReadBudget | None = None) -> Record:
    core = _core()
    reader = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    require(
        _present(program.path(TRACKING)),
        "Definition mutation requires explicit publication-adopt for this untracked registry",
    )
    tracking = core.read_json(program.path(TRACKING), budget=reader)
    require(
        tracking.get("schema_version") == 1 and tracking.get("ledger_id") == ledger["ledger_id"],
        "Publication tracking/ledger identity conflict",
    )
    ref = coordination._record(tracking.get("receipt"), "tracking receipt")
    program.verify_refs([ref], reader)
    receipt = core.read_json(program.path(coordination._relative(ref["path"])), budget=reader)
    require(
        receipt.get("program_id") == tracking.get("program_id")
        and receipt.get("ledger_id") == ledger["ledger_id"]
        and receipt.get("legacy_endpoints", []) == tracking.get("legacy_endpoints", []),
        "Tracking receipt identity/inventory conflict",
    )
    coordination._choice(receipt.get("mode"), {"fresh-init", "legacy-adopted"}, "tracking mode")
    program.verify_refs(
        coordination._items(receipt.get("evidence", []), "tracking evidence"), reader
    )
    return tracking


def require_unreserved(program: Program) -> None:
    store = PublicationStore(program.repo)
    if _present(store.path("ledger.json")):
        current = program.directory.relative_to(program.repo).as_posix()
        require(
            not any(
                current.casefold()
                in _path_keys(
                    coordination._texts(
                        coordination._record(value, "publication")["endpoints"], "endpoints"
                    )
                )
                for value in coordination._items(store.load()["active"], "active")
            ),
            "Program destination reserved by active publication; recover or reconcile it first",
        )


def publication_runtime_guard(program: Program) -> None:
    """Guard a publishing succession predecessor even before its local marker exists."""
    store = PublicationStore(program.repo)
    if not _present(store.path("ledger.json")):
        return
    current = program.directory.relative_to(program.repo).as_posix().casefold()
    ledger = store.load()
    for value in coordination._items(ledger["history"], "history"):
        item = coordination._record(value, "publication history")
        if item["stage"] == "superseded" and item["endpoints"][1].casefold() == current:
            raise _core().ProgramError(
                f"Superseded incomplete destination remains guarded: {item['id']}; use verified replacement"
            )
    for value in coordination._items(ledger["active"], "active"):
        item = coordination._record(value, "publication")
        if (
            item["kind"] == "succession"
            and item["stage"] == "publishing"
            and item["endpoints"][0].casefold() == current
        ):
            store.payload(item, _core().ReadBudget(64 * 1024 * 1024))
            raise _core().ProgramError(
                f"Succession publication incomplete: {item['id']}; use program-succeed-recover"
            )


def publication_register(
    program: Program,
    kind: str,
    marker: Record,
    target: Program,
    *,
    supersedes: str | None = None,
    legacy_observed: bool = False,
) -> str:
    core = _core()
    store = PublicationStore(program.repo)
    endpoints = [
        program.directory.relative_to(program.repo).as_posix(),
        target.directory.relative_to(program.repo).as_posix(),
    ]
    for endpoint in (program, target):
        validate_publication_endpoint(endpoint.repo, endpoint.directory)
    with store.lock():
        # A missing adopted store cannot be recreated by a publisher.
        for endpoint in (program, target):
            if _present(endpoint.path(TRACKING)):
                _tracking(endpoint, store.load())
        ledger = store.load(create=True)
        if supersedes is not None:
            store.active(ledger, supersedes)
        for value in coordination._items(ledger["active"], "active"):
            item = coordination._record(value, "publication")
            if _path_keys(coordination._texts(item["endpoints"], "endpoints")) & _path_keys(
                endpoints
            ):
                require(
                    item["id"] == supersedes and kind == "relocation",
                    "Related publication remains active; recover or explicitly supersede failed copy",
                )
                _failed_copy(store, item, core.ReadBudget(64 * 1024 * 1024))
                require(
                    item["endpoints"][0] == endpoints[0] and item["endpoints"][1] != endpoints[1],
                    "Replacement must use same source and distinct destination",
                )
        require(
            len(
                coordination._items(ledger["active"], "active")
                + coordination._items(ledger["history"], "history")
            )
            < MAX_PUBLICATIONS,
            "Publication ledger exceeds 4096 records",
        )
        identifier = uuid.uuid4().hex
        ids = (
            [marker["predecessor_id"], marker["successor_id"]]
            if kind == "succession"
            else [marker["program_id"], marker["program_id"]]
        )
        payload: Record = {
            "id": identifier,
            "kind": kind,
            "endpoints": endpoints,
            "program_ids": ids,
            "marker": marker,
            "before_markers": marker_inventory(
                program.repo, endpoints, core.ReadBudget(64 * 1024 * 1024)
            ),
            "recorded_at": core.utc(),
            "legacy_observed": legacy_observed,
            "supersedes": supersedes,
        }
        ref = store.freeze(f"transactions/{identifier}/registered.json", payload)
        coordination._items(ledger["active"], "active").append(
            {
                "id": identifier,
                "kind": kind,
                "endpoints": endpoints,
                "program_ids": ids,
                "stage": "legacy-observed" if legacy_observed else "registered",
                "receipt": ref,
            }
        )
        store.save(ledger)
    return identifier


def publication_begin(program: Program, identifier: str) -> None:
    store = PublicationStore(program.repo)
    with store.lock():
        ledger = store.load()
        item = store.active(ledger, identifier)
        require(
            item["stage"] in {"registered", "publishing", "legacy-observed"},
            "Publication stage cannot begin",
        )
        item["stage"] = "publishing"
        store.save(ledger)


def publication_find(
    program: Program, kind: str, identifier: str | None = None
) -> tuple[Record, Record] | None:
    store = PublicationStore(program.repo)
    if not _present(store.path("ledger.json")):
        return None
    current = program.directory.relative_to(program.repo).as_posix()
    matches = [
        coordination._record(value, "publication")
        for value in coordination._items(store.load()["active"], "active")
        if coordination._record(value, "publication")["kind"] == kind
        and current.casefold()
        in _path_keys(coordination._record(value, "publication")["endpoints"])
        and (identifier is None or coordination._record(value, "publication")["id"] == identifier)
    ]
    if not matches:
        require(identifier is None, "Selected publication identity not found")
        return None
    require(len(matches) == 1, "Publication recovery ambiguous; supply --publication-id")
    return matches[0], store.payload(matches[0], _core().ReadBudget(64 * 1024 * 1024))


def _failed_copy(store: PublicationStore, item: Record, budget: ReadBudget) -> Record:
    core = _core()
    require(item["kind"] == "relocation", "Supersession requires failed relocation copy")
    payload = store.payload(item, budget)
    target = core.safe_path(store.repo, item["endpoints"][1])
    marker = core.read_json(core.safe_path(target, "RELOCATION_INCOMPLETE.json"), budget=budget)
    require(
        marker.get("phase") == "copying"
        and marker.get("source") == item["endpoints"][0]
        and marker.get("destination") == item["endpoints"][1],
        "Only exact copying-stage destination can be superseded",
    )
    require(
        not _present(core.safe_path(target, "RELOCATED.json")),
        "Failed destination has conflicting archive",
    )
    expected = payload.get("failed_marker_sha256")
    if expected is None and payload.get("marker") is not None:
        declared = coordination._record(payload["marker"], "registered relocation")
        if declared.get("copying_marker") is not None:
            expected = core.sha(core.canonical(declared["copying_marker"]))
    if expected is not None:
        require(
            core.sha(
                budget.read(core.safe_path(target, "RELOCATION_INCOMPLETE.json"), DOCUMENT_LIMIT)
            )
            == expected,
            "Failed-copy marker changed",
        )
    return marker


def _failed_copy_digest(store: PublicationStore, item: Record, budget: ReadBudget) -> str:
    directory = _core().safe_path(
        store.repo, coordination._text(item["endpoints"][1], "failed destination")
    )
    return _core().sha(
        budget.read(_core().safe_path(directory, "RELOCATION_INCOMPLETE.json"), DOCUMENT_LIMIT)
    )


def _completed_proof(
    store: PublicationStore,
    item: Record,
    payload: Record,
    budget: ReadBudget,
    manifest: Record | None = None,
    *,
    contract_check: bool = True,
) -> Record:
    core = _core()
    source, target = [
        core.Program(store.repo, core.safe_path(store.repo, name))
        for name in coordination._texts(item["endpoints"], "endpoints")
    ]
    for endpoint in (source, target):
        require(
            all(not _present(endpoint.path(name)) for name in MARKERS),
            "Publication cleanup incomplete; recover first",
        )
    if item["kind"] == "succession":
        marker = coordination._record(payload["marker"], "succession marker")
        require(
            core.read_json(source.path("SUCCEEDED.json"), budget=budget)
            == marker
            == core.read_json(target.path("PREDECESSOR.json"), budget=budget),
            "Completed succession receipts conflict",
        )
        current = core.read_json(target.path("backlog.json"), budget=budget)
        require(
            current["program_id"] == item["program_ids"][1]
            and (
                not contract_check
                or phases._succession_contract(current) == marker["successor_contract"]
            ),
            "Completed successor frozen contract changed",
        )
        proof = {
            "marker_sha256": core.sha(budget.read(source.path("SUCCEEDED.json"), DOCUMENT_LIMIT)),
            "successor_marker_sha256": core.sha(
                budget.read(target.path("PREDECESSOR.json"), DOCUMENT_LIMIT)
            ),
        }
        if item["stage"] == "completed":
            _verify_completion(store, item, proof, budget)
        return proof
    archive = core.read_json(source.path("RELOCATED.json"), budget=budget)
    marker = coordination._record(payload["marker"], "relocation marker")
    if manifest is None:
        original = coordination._text(marker["original_registry_sha256"], "original registry hash")
        manifest = core.read_json(
            target.path(f"recovery/relocations/{original}/manifest.json"), budget=budget
        )
    require(
        manifest.get("phase") == "ready"
        and manifest.get("source") == item["endpoints"][0]
        and manifest.get("destination") == item["endpoints"][1]
        and archive.get("destination") == item["endpoints"][1]
        and archive.get("registry_sha256") == manifest.get("registry_sha256"),
        "Completed relocation manifest/archive conflict",
    )
    current = core.read_json(target.path("backlog.json"), budget=budget)
    require(
        current["program_id"] == item["program_ids"][1]
        and any(
            coordination._record(value, "event").get("action") == "relocate"
            and coordination._record(value, "event").get("source_directory") == item["endpoints"][0]
            and coordination._record(value, "event").get("destination_directory")
            == item["endpoints"][1]
            and coordination._record(value, "event").get("original_registry_sha256")
            == manifest["original_registry_sha256"]
            for value in coordination._items(current["events"], "events")
        ),
        "Current relocation lineage mismatch",
    )
    manifest_name = coordination._relative(manifest.get("manifest_path"))
    require(
        core.read_json(target.path(manifest_name), budget=budget) == manifest,
        "Relocation manifest changed during finalization",
    )
    proof = {
        "manifest_path": manifest_name,
        "manifest_sha256": core.sha(budget.read(target.path(manifest_name), DOCUMENT_LIMIT)),
        "archive_sha256": core.sha(budget.read(source.path("RELOCATED.json"), DOCUMENT_LIMIT)),
    }
    if item["stage"] == "completed":
        _verify_completion(store, item, proof, budget)
    return proof


def _verify_completion(
    store: PublicationStore, item: Record, proof: Record, budget: ReadBudget
) -> None:
    ref = coordination._record(item["result"], "publication result")
    path = store.path(coordination._relative(ref["path"]))
    require(
        _core().sha(budget.read(path, DOCUMENT_LIMIT)) == ref["sha256"],
        "Frozen publication completion changed",
    )
    result = _core().read_json(path, budget=budget)
    require(
        result.get("operation_id") == item["id"] and result.get("proof") == proof,
        "Publication completion evidence conflicts",
    )


def publication_complete(program: Program, identifier: str, manifest: Record | None = None) -> None:
    core = _core()
    store = PublicationStore(program.repo)
    with store.lock():
        ledger = store.load()
        item = store.active(ledger, identifier)
        budget = core.ReadBudget(64 * 1024 * 1024)
        payload = store.payload(item, budget)
        proof = _completed_proof(store, item, payload, budget, manifest)
        result_name = f"transactions/{identifier}/completed.json"
        if _present(store.path(result_name)):
            result = core.read_json(store.path(result_name), budget=budget)
            require(
                result.get("operation_id") == identifier and result.get("proof") == proof,
                "Completion evidence conflicts",
            )
        else:
            result = {"operation_id": identifier, "proof": proof, "recorded_at": core.utc()}
        item.update({"stage": "completed", "result": store.freeze(result_name, result)})
        removed = [identifier]
        if payload.get("supersedes") is not None:
            old = store.active(
                ledger, coordination._text(payload["supersedes"], "superseded operation")
            )
            _failed_copy(store, old, budget)
            old.update(
                {
                    "stage": "superseded",
                    "replacement_id": identifier,
                    "failed_marker_sha256": _failed_copy_digest(store, old, budget),
                    "result": item["result"],
                }
            )
            coordination._items(ledger["history"], "history").append(deepcopy(old))
            removed.append(coordination._text(old["id"], "old publication ID"))
        coordination._items(ledger["history"], "history").append(deepcopy(item))
        ledger["active"] = [
            value
            for value in coordination._items(ledger["active"], "active")
            if coordination._record(value, "publication")["id"] not in removed
        ]
        store.save(ledger)


def require_completed_publication(program: Program, state: Record) -> None:
    """Read only the central locator and positively related legacy endpoints."""
    core = _core()
    program.require_current_location()
    store = PublicationStore(program.repo)
    ledger = store.load()
    budget = core.ReadBudget(64 * 1024 * 1024)
    tracking = _tracking(program, ledger, budget)
    require(
        tracking.get("program_id") == state["program_id"],
        "Publication tracking program identity mismatch",
    )
    aliases = _aliases(program, state)
    endpoints = set(
        inspect_publication_inventory(
            program,
            state,
            coordination._texts(tracking.get("legacy_endpoints", []), "legacy endpoints"),
            budget,
        )
    )
    superseded: set[str] = set()
    for value in coordination._items(ledger["history"], "history"):
        item = coordination._record(value, "publication history")
        if item["stage"] == "completed" and _path_keys(item["endpoints"]) & _path_keys(endpoints):
            _completed_proof(store, item, store.payload(item, budget), budget, contract_check=False)
        if item["stage"] == "superseded" and _path_keys(
            coordination._texts(item["endpoints"], "endpoints")
        ) & _path_keys(aliases):
            _failed_copy(store, item, budget)
            require(
                _failed_copy_digest(store, item, budget) == item["failed_marker_sha256"],
                "Superseded failed-copy marker changed",
            )
            replacements = [
                coordination._record(entry, "history")
                for entry in coordination._items(ledger["history"], "history")
                if coordination._record(entry, "history")["id"] == item["replacement_id"]
            ]
            require(
                len(replacements) == 1
                and replacements[0]["stage"] == "completed"
                and replacements[0]["endpoints"][1] in aliases,
                "Failed-copy replacement lineage missing",
            )
            _completed_proof(store, replacements[0], store.payload(replacements[0], budget), budget)
            superseded.add(coordination._text(item["endpoints"][1], "failed destination"))
    for value in coordination._items(ledger["active"], "active"):
        item = coordination._record(value, "active publication")
        if _path_keys(coordination._texts(item["endpoints"], "endpoints")) & _path_keys(endpoints):
            store.payload(item, budget)
            raise PlanningError(
                f"Definition changes require publication recovery/reconciliation: {item['id']} ({item['stage']})"
            )
    for endpoint in endpoints:
        if endpoint in superseded:
            continue
        directory = core.safe_path(program.repo, endpoint)
        require(
            all(not _present(core.safe_path(directory, name)) for name in MARKERS),
            f"Related legacy publication incomplete: {endpoint}; recover/reconcile first",
        )
        backlink = core.safe_path(directory, "PREDECESSOR.json")
        if _present(backlink):
            marker = core.read_json(backlink, budget=budget)
            predecessor = core.safe_path(
                program.repo, coordination._relative(marker.get("predecessor"))
            )
            require(
                core.read_json(core.safe_path(predecessor, "SUCCEEDED.json"), budget=budget)
                == marker
                and not _present(core.safe_path(predecessor, "SUCCESSION_INCOMPLETE.json")),
                "Legacy succession counterpart incomplete/conflicting",
            )


STARTER_DOCS: Final = frozenset(
    {
        "README.md",
        "DIRECTOR.md",
        "SUBDIRECTOR.md",
        "WORKER.md",
        "REVIEW.md",
        "EVIDENCE.md",
        "DECISIONS.md",
        "DESIGN.md",
        "PRIORITY.md",
        "ACTORS.md",
        "RESPONSIBILITIES.md",
        "MODEL-POLICY.md",
        "STAFFING.md",
        "DELIVERY.md",
        "QUESTIONS.md",
    }
)


def fresh_init(program: Program, args: argparse.Namespace, state: Record) -> None:
    """Track only genuinely fresh inputs, preserving an exact resumable initialization receipt."""
    core = _core()
    require_unreserved(program)
    allowed = set(STARTER_DOCS)
    for name in (args.report, args.planning):
        path = core.safe_path(program.repo, name)
        validate_publication_endpoint(program.repo, path)
        if path.is_relative_to(program.directory):
            allowed.add(path.relative_to(program.directory).as_posix())
    snapshot: Record = {}
    budget = core.ReadBudget(64 * 1024 * 1024)
    for folder, dirs, files in os.walk(program.directory):
        relative = Path(folder).relative_to(program.directory)
        require(len(snapshot) + len(dirs) <= 4096, "Fresh-init input layout exceeds bounded budget")
        for name in dirs:
            child = (relative / name).as_posix()
            core.safe_path(program.directory, child)
            require(
                any(item.startswith(child + "/") for item in allowed),
                "Fresh init rejects unrelated directories/runtime artifacts",
            )
        for name in files:
            child = (relative / name).as_posix()
            path = program.path(child)
            if child in {".feature-program.lock", "FRESH_INIT.json", TRACKING}:
                continue
            require(child in allowed, f"Fresh init rejects prior/unrelated material: {child}")
            snapshot[child] = core.sha(budget.read(path, DOCUMENT_LIMIT))
    identity: Record = {
        "program_id": state["program_id"],
        "directory": program.directory.relative_to(program.repo).as_posix(),
        "source": state["source"],
        "planning_path": state["planning_path"],
        "planning_sha256": state["planning_sha256"],
        "inputs": snapshot,
        "definitions": phases._succession_contract(state),
    }
    store = PublicationStore(program.repo)
    with store.lock():
        pending = program.path("FRESH_INIT.json")
        if _present(pending) or _present(program.path(TRACKING)):
            require(_present(pending), "Fresh-init retry lacks original initialization receipt")
            receipt = core.read_json(pending)
            ledger = store.load()
            require(
                receipt.get("identity") == identity
                and receipt.get("ledger_id") == ledger["ledger_id"],
                "Partial initialization inputs/identity changed; preserve and inspect",
            )
        else:
            ledger = store.load(create=True)
            receipt = {
                "schema_version": 1,
                "mode": "fresh-init",
                "program_id": state["program_id"],
                "ledger_id": ledger["ledger_id"],
                "identity": identity,
                "legacy_endpoints": [],
                "recorded_at": core.utc(),
            }
            encoded = core.canonical(receipt)
            require(len(encoded) <= DOCUMENT_LIMIT, "Fresh-init receipt exceeds 8 MiB")
            core.atomic_write(pending, encoded)
        require_unreserved(program)
        tracking = {
            "schema_version": 1,
            "mode": "fresh-init",
            "program_id": state["program_id"],
            "ledger_id": ledger["ledger_id"],
            "legacy_endpoints": [],
            "receipt": {"path": "FRESH_INIT.json", "sha256": core.sha(core.canonical(receipt))},
        }
        if _present(program.path(TRACKING)):
            require(
                core.read_json(program.path(TRACKING)) == tracking,
                "Partial initialization tracking conflicts",
            )
        else:
            core.atomic_write(program.path(TRACKING), core.canonical(tracking))


def inspect_publication_inventory(
    program: Program, state: Record, endpoints: list[str], budget: ReadBudget | None = None
) -> Record:
    """Return finite named marker closure; caller attests completeness, not filesystem proof."""
    core = _core()
    required = _aliases(program, state)
    selected = set(endpoints) | required
    budget = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    inspected: set[str] = set()
    while selected - inspected:
        require(
            len(selected) <= MAX_PUBLICATIONS, "Legacy endpoint inventory exceeds 4096 endpoints"
        )
        name = sorted(selected - inspected)[0]
        directory = core.safe_path(program.repo, name)
        validate_publication_endpoint(program.repo, directory)
        require(
            directory.is_dir(),
            f"Related endpoint unavailable: {name}; restore/inspect it before adoption or mutation",
        )
        for marker_name in PUBLICATION_MARKERS:
            path = core.safe_path(directory, marker_name)
            if _present(path):
                marker = core.read_json(path, budget=budget)
                fields = (
                    ("predecessor", "successor")
                    if marker_name
                    in {"SUCCEEDED.json", "PREDECESSOR.json", "SUCCESSION_INCOMPLETE.json"}
                    else (
                        ("destination",)
                        if marker_name == "RELOCATED.json"
                        else ("source", "destination")
                    )
                )
                selected.update(coordination._relative(marker.get(field)) for field in fields)
        inspected.add(name)
    return marker_inventory(program.repo, sorted(selected), budget)


def _publication_authority(program: Program, state: Record, receipt: Record) -> None:
    require(
        receipt.get("schema_version") == 1
        and receipt.get("program_id") == state["program_id"]
        and receipt.get("expected_revision") == phases.revision(state),
        "Publication reconciliation identity/revision mismatch",
    )
    require(
        receipt.get("actor_id") == coordination.current_director_id(state),
        "Publication decision requires current director",
    )
    coordination._text(receipt.get("reason"), "publication decision reason")
    require(
        receipt.get("binding") == phases.planning_binding(state),
        "Publication decision scope binding mismatch",
    )
    tasks = coordination._indexed(state["tasks"], "task")
    first = next(iter(tasks))
    phases.guard_execution(
        program,
        state,
        first,
        coordination._texts(tasks[first]["candidate_paths"], "scope"),
        membership_check=False,
    )


def _publication_review(program: Program, receipt: Record, digest: str) -> list[Json]:
    review_name = coordination._relative(receipt.get("review"))
    category = "_phases" if review_name.startswith("evidence/_phases/") else "_publications"
    review, refs = program.frozen_json(category, 1, review_name)
    authors = coordination._texts(receipt.get("inventory_authors"), "publication inventory authors")
    require(
        bool(authors) and len(authors) == len(set(authors)),
        "Publication inventory needs explicit unique authors",
    )
    require(
        review.get("inventory_digest") == digest
        and review.get("program_id") == receipt["program_id"]
        and review.get("binding") == receipt["binding"]
        and review.get("reviewer_role") == "independent"
        and review.get("verdict") == "accepted"
        and review.get("unresolved_findings") == []
        and review.get("reviewer") not in [receipt["actor_id"], *authors],
        "Publication inventory review incomplete/stale/not independent",
    )
    coordination._text(review.get("reviewer"), "publication inventory reviewer")
    refs += program.evidence(
        "_publications",
        1,
        coordination._texts(receipt.get("evidence"), "publication decision evidence"),
    )
    return refs


@contextmanager
def _endpoint_locks(program: Program, names: list[str]) -> Iterator[None]:
    """Never acquire an endpoint while holding the central ledger lock."""
    current = program.directory.relative_to(program.repo).as_posix().casefold()
    with ExitStack() as locks:
        for name in sorted(set(names)):
            if name.casefold() != current:
                endpoint = _core().Program(program.repo, _core().safe_path(program.repo, name))
                locks.enter_context(endpoint.lock(publication_recovery=True))
        yield


def publication_adopt(program: Program, state: Record, name: str) -> None:
    core = _core()
    require(not _present(program.path(TRACKING)), "Publication tracking already adopted")
    receipt, refs = program.frozen_json("_publications", 1, name)
    _publication_authority(program, state, receipt)
    require(
        receipt.get("obsolete_writers_quiesced") is True,
        "Explicitly reconcile obsolete untracked writers before adoption",
    )
    inventory = coordination._record(receipt.get("inventory"), "legacy endpoint inventory")
    actual = inspect_publication_inventory(program, state, list(inventory))
    require(inventory == actual, "Legacy inventory incomplete or inspected markers changed")
    for value in coordination._items(
        receipt.get("uninspected_artifacts", []), "uninspected historical artifacts"
    ):
        item = coordination._record(value, "uninspected artifact")
        path = coordination._relative(item.get("path"))
        coordination._text(item.get("reason"), "uninspected artifact rationale")
        require(
            not any(endpoint == path or endpoint.startswith(path + "/") for endpoint in actual),
            "Related endpoint cannot be an uninspected exclusion",
        )
    refs += _publication_review(
        program,
        receipt,
        phases.revision(
            {
                "inventory": inventory,
                "uninspected_artifacts": receipt.get("uninspected_artifacts", []),
            }
        ),
    )
    store = PublicationStore(program.repo)
    adopted_name = "evidence/_publications/attempt-1/adopted.json"
    with _endpoint_locks(program, list(inventory)), store.lock():
        existed = _present(store.path("ledger.json"))
        pending = (
            core.read_json(program.path(adopted_name))
            if _present(program.path(adopted_name))
            else None
        )
        ledger = store.load(create=pending is None)
        require(
            (
                pending is not None
                and pending.get("decision") == receipt
                and pending.get("evidence") == refs
                and pending.get("ledger_id") == ledger["ledger_id"]
                and pending.get("published_ledger_revision") == phases.revision(ledger)
            )
            or (
                pending is None
                and receipt.get("ledger_revision") == (phases.revision(ledger) if existed else None)
            ),
            "Publication ledger revision stale",
        )
        require(
            inspect_publication_inventory(program, state, list(inventory)) == inventory
            and phases.revision(core.read_json(program.path("backlog.json")))
            == receipt["expected_revision"],
            "Adoption inputs changed before publication",
        )
        program.verify_refs(refs)
        reader = core.ReadBudget(64 * 1024 * 1024)
        seen: set[str] = set()
        for endpoint, observed in inventory.items():
            for marker_name in MARKERS & set(coordination._record(observed, "observed markers")):
                marker = core.read_json(
                    core.safe_path(core.safe_path(program.repo, endpoint), marker_name),
                    budget=reader,
                )
                kind = "succession" if marker_name == "SUCCESSION_INCOMPLETE.json" else "relocation"
                paths = (
                    [
                        coordination._relative(marker.get("predecessor")),
                        coordination._relative(marker.get("successor")),
                    ]
                    if kind == "succession"
                    else [
                        coordination._relative(marker.get("source")),
                        coordination._relative(marker.get("destination")),
                    ]
                )
                identifier = "legacy-" + phases.revision({"kind": kind, "marker": marker})[:32]
                if identifier in seen or any(
                    coordination._record(value, "publication")["endpoints"] == paths
                    and coordination._record(value, "publication")["kind"] == kind
                    for value in coordination._items(ledger["active"], "active")
                ):
                    continue
                seen.add(identifier)
                source_state = core.read_json(
                    core.safe_path(core.safe_path(program.repo, paths[0]), "backlog.json"),
                    budget=reader,
                )
                ids = (
                    [
                        coordination._text(marker.get("predecessor_id"), "predecessor ID"),
                        coordination._text(marker.get("successor_id"), "successor ID"),
                    ]
                    if kind == "succession"
                    else [source_state["program_id"], source_state["program_id"]]
                )
                payload = {
                    "id": identifier,
                    "kind": kind,
                    "endpoints": paths,
                    "program_ids": ids,
                    "marker": marker,
                    "legacy_observed_at": core.utc(),
                    "failed_marker_sha256": coordination._record(observed, "observed markers")[
                        marker_name
                    ]
                    if marker.get("phase") == "copying"
                    else None,
                }
                payload_name = f"transactions/{identifier}/legacy-observed.json"
                if _present(store.path(payload_name)):
                    previous = core.read_json(store.path(payload_name), budget=reader)
                    require(
                        {
                            key: value
                            for key, value in previous.items()
                            if key != "legacy_observed_at"
                        }
                        == {
                            key: value
                            for key, value in payload.items()
                            if key != "legacy_observed_at"
                        },
                        "Prior legacy observation conflicts; preserve and inspect",
                    )
                    payload = previous
                require(
                    len(
                        coordination._items(ledger["active"], "active")
                        + coordination._items(ledger["history"], "history")
                    )
                    < MAX_PUBLICATIONS,
                    "Publication ledger exceeds 4096 records",
                )
                ref = store.freeze(payload_name, payload)
                coordination._items(ledger["active"], "active").append(
                    {
                        "id": identifier,
                        "kind": kind,
                        "endpoints": paths,
                        "program_ids": ids,
                        "stage": "legacy-observed",
                        "receipt": ref,
                    }
                )
        adopted = (
            pending
            if pending is not None
            else {
                "schema_version": 1,
                "mode": "legacy-adopted",
                "program_id": state["program_id"],
                "ledger_id": ledger["ledger_id"],
                "legacy_endpoints": sorted(inventory),
                "decision": receipt,
                "evidence": refs,
                "recorded_at": core.utc(),
                "published_ledger_revision": phases.revision(ledger),
            }
        )
        require(
            len(core.canonical(adopted)) <= DOCUMENT_LIMIT,
            "Publication adoption observation exceeds 8 MiB",
        )
        store.save(ledger)
        if pending is None:
            core.atomic_write(program.path(adopted_name), core.canonical(adopted))
        core.atomic_write(
            program.path(TRACKING),
            core.canonical(
                {
                    "schema_version": 1,
                    "mode": "legacy-adopted",
                    "program_id": state["program_id"],
                    "ledger_id": ledger["ledger_id"],
                    "legacy_endpoints": sorted(inventory),
                    "receipt": {"path": adopted_name, "sha256": core.sha(core.canonical(adopted))},
                }
            ),
        )


def publication_reconcile(program: Program, state: Record, name: str) -> None:
    core = _core()
    receipt, refs = program.frozen_json("_publications", 1, name)
    _publication_authority(program, state, receipt)
    action = coordination._choice(
        receipt.get("action"),
        {"cancel-registration", "supersede-failed-copy"},
        "publication reconciliation action",
    )
    refs += _publication_review(
        program,
        receipt,
        phases.revision(
            {key: value for key, value in receipt.items() if key not in {"review", "evidence"}}
        ),
    )
    store = PublicationStore(program.repo)
    initial = store.load()
    item = store.active(
        initial, coordination._text(receipt.get("operation_id"), "publication operation ID")
    )
    require(
        bool(_path_keys(item["endpoints"]) & _path_keys(_aliases(program, state))),
        "Reconciliation operation unrelated to selected program",
    )
    # Nonblocking endpoint locks are always acquired before the ledger lock.
    with _endpoint_locks(program, coordination._texts(item["endpoints"], "endpoints")):
        with store.lock():
            ledger = store.load()
            require(
                receipt.get("ledger_revision") == phases.revision(ledger),
                "Reconciliation ledger revision stale",
            )
            item = store.active(ledger, item["id"])
            budget = core.ReadBudget(64 * 1024 * 1024)
            payload = store.payload(item, budget)
            if action == "cancel-registration":
                require(
                    item["stage"] == "registered"
                    and marker_inventory(program.repo, item["endpoints"], budget)
                    == payload["before_markers"],
                    "Only unchanged registered-stage publication can be cancelled",
                )
                item["stage"] = "cancelled"
            else:
                _failed_copy(store, item, budget)
                replacements = [
                    coordination._record(value, "replacement")
                    for value in coordination._items(ledger["history"], "history")
                    if coordination._record(value, "history")["id"] == receipt.get("replacement_id")
                ]
                require(
                    len(replacements) == 1
                    and replacements[0]["stage"] == "completed"
                    and replacements[0]["kind"] == "relocation"
                    and replacements[0]["endpoints"][0] == item["endpoints"][0]
                    and replacements[0]["endpoints"][1] != item["endpoints"][1],
                    "Supersession needs distinct completed replacement",
                )
                _completed_proof(
                    store, replacements[0], store.payload(replacements[0], budget), budget
                )
                _failed_copy(store, item, budget)
                item.update(
                    {
                        "stage": "superseded",
                        "replacement_id": replacements[0]["id"],
                        "failed_marker_sha256": _failed_copy_digest(store, item, budget),
                    }
                )
            program.verify_refs(refs)
            require(
                phases.revision(core.read_json(program.path("backlog.json")))
                == receipt["expected_revision"],
                "Registry changed before reconciliation",
            )
            result_name = f"transactions/{item['id']}/reconciled-{phases.revision(receipt)}.json"
            result = {
                "receipt": receipt,
                "evidence": refs,
                "recorded_at": core.utc(),
                "observed_registry_revision": phases.revision(state),
            }
            if _present(store.path(result_name)):
                previous = core.read_json(store.path(result_name), budget=budget)
                require(
                    previous.get("receipt") == receipt
                    and previous.get("evidence") == refs
                    and previous.get("observed_registry_revision") == phases.revision(state),
                    "Prior reconciliation observation conflicts; preserve and inspect",
                )
                result = previous
            item["result"] = store.freeze(result_name, result)
            ledger["active"] = [
                value
                for value in coordination._items(ledger["active"], "active")
                if coordination._record(value, "active")["id"] != item["id"]
            ]
            coordination._items(ledger["history"], "history").append(item)
            store.save(ledger)


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
    phases.guard_baseline(program, state)
    proposal = deepcopy(state)
    before_binding = phases.planning_binding(state)
    additions: list[str] = []
    if operation in PLANNED_COMMANDS:
        _amend_planned(program, proposal, receipt)
    elif operation in {"task-admit", "task-split"}:
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
    decomposition: Record | None = None
    if operation in DECOMPOSITION_COMMANDS:
        decomposition = _decompose(program, state, proposal, receipt, additions)
    if operation not in PLANNED_COMMANDS:
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
    if decomposition is not None:
        event["decomposition"] = decomposition
    if operation in PLANNED_COMMANDS:
        event.update(
            {
                "changes": deepcopy(receipt["changes"]),
                "priority_before": deepcopy(state.get("execution_priorities")),
                "priority_after": deepcopy(proposal.get("execution_priorities")),
                "order_before": [task["id"] for task in task_order(state)],
                "order_after": [task["id"] for task in task_order(proposal)],
                "ready_before": [task["id"] for task in program.status(state, True)["candidates"]],
                "ready_after": [
                    task["id"] for task in program.status(proposal, True)["candidates"]
                ],
                "renewal_required": before_binding != after_binding,
            }
        )
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
        "renewal_required": future.before_binding != future.after_binding,
    }
    if receipt["operation"] not in PLANNED_COMMANDS:
        result["membership"] = receipt["membership"]
    if receipt["operation"] in DECOMPOSITION_COMMANDS:
        result["predecessor_before"] = predecessor_binding(
            program, state, str(receipt["predecessor"])
        )
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
    if args.command in DECOMPOSITION_COMMANDS:
        require(
            _same_json(
                receipt.get("predecessor_before"),
                predecessor_binding(program, state, str(receipt["predecessor"])),
            ),
            "Stale predecessor definition/source-section binding",
        )
    require(
        receipt.get("before_binding") == future.before_binding
        and receipt.get("after_binding") == future.after_binding,
        "Stale before/after scope binding",
    )
    additional = program.evidence(
        "_planning", 1, coordination._texts(receipt.get("evidence"), "amendment review evidence")
    )
    refs += additional
    renewal_required = (
        args.command not in PLANNED_COMMANDS or future.before_binding != future.after_binding
    )
    if renewal_required:
        phases.renew(
            program,
            future.state,
            coordination._record(receipt.get("renewal"), "prospective phase renewal"),
            phases.revision(state),
            coordination._texts(receipt.get("intake_authors", []), "intake authors"),
        )
    else:
        require("renewal" not in receipt, "Unchanged scope must preserve existing phase acceptance")
    amendments = coordination._items(future.state["amendments"], "amendments")
    coordination._record(amendments[-1], "amendment")["evidence"] = refs
    if args.command not in PLANNED_COMMANDS:
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
            "Registry changed before amendment publication",
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
    for name in (
        "admission-binding",
        "task-admit",
        "objective-membership",
        "task-plan",
        "task-prioritize",
        "task-split",
        "task-retire",
        "publication-adopt",
        "publication-reconcile",
    ):
        command = commands.add_parser(
            name, help="Reviewed prospective admission/membership contract"
        )
        command.add_argument(
            "--input", required=True, help="Program-relative proposal or frozen receipt"
        )
