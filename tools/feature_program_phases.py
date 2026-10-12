"""Opt-in planning gates and explicit discovery-program succession.

Run through tools/feature_program.py; all mutations retain its local locks.
Planning bindings deliberately exclude implementation bytes and runtime progress.
"""

# noqa: SIZE_OK - The reviewed eight-file scope keeps phase-boundary enforcement
# here; gate and succession helpers are distinct seams, with no wider file split.

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import ModuleType
from typing import Final, Literal, TYPE_CHECKING, assert_never

if __package__:
    from tools import feature_program_coordination as coordination
else:
    import feature_program_coordination as coordination

if TYPE_CHECKING:
    from tools.feature_program import Program

Json = coordination.Json
Record = coordination.Record
PhaseCommand = Literal["phase-adopt", "inventory-freeze", "candidate-review", "execution-accept"]
COMMANDS: Final = frozenset(
    {"phase-adopt", "inventory-freeze", "candidate-review", "execution-accept"}
)
TASK_PROGRESS_FIELDS: Final = frozenset(
    {"status", "owner", "attempts", "notes", "next_action", "deliveries"}
)


class PhaseError(ValueError):
    """A rejected planning gate or succession transition."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PhaseError(message)


def revision(state: Record) -> str:
    """Return an optimistic concurrency token, never a permanent acceptance binding."""
    raw = (
        json.dumps(state, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _tasks(state: Record) -> dict[str, Record]:
    return coordination._indexed(state.get("tasks"), "task")


def _definitions(state: Record) -> list[Json]:
    """Include authored planning extensions, excluding only known runtime fields."""
    return [
        {key: deepcopy(value) for key, value in task.items() if key not in TASK_PROGRESS_FIELDS}
        for _, task in sorted(_tasks(state).items())
    ]


def planning_binding(state: Record) -> Record:
    """Bind declared work and objective, excluding execution, staffing and evidence."""
    goal = coordination.objective_contract(state)
    require(goal is not None, "Phase gates require a recorded current objective")
    tasks = _tasks(state)
    definitions = _definitions(state)
    declared: Record = {
        "tasks": definitions,
        "workstreams": state.get("workstreams"),
        "verification_policy_sha256": state.get("verification_policy_sha256"),
    }
    if state.get("source_intakes") or goal.get("membership_revision") is not None:
        declared["categories"] = state.get("categories")
    return {
        "program_id": state["program_id"],
        "objective_id": goal["id"],
        "objective_digest": revision(goal),
        "task_definition_digest": revision(declared),
        "planning_digest": _planning().planning_digest(state),
        "task_ids": sorted(tasks),
    }


def binding(program: Program, state: Record) -> Record:
    """Expose exact values for explicit, fail-safe phase receipts."""
    program.require_current_location()
    result = {**planning_binding(state), "expected_revision": revision(state)}
    contract = state.get("phases")
    if contract is not None:
        inventory = coordination._record(contract, "phases").get("inventory")
        if inventory is not None:
            frozen = coordination._record(inventory, "inventory")
            result["candidate_digest"] = frozen["candidate_digest"]
            result["inventory_digest"] = frozen["inventory_digest"]
            result["units"] = deepcopy(frozen["units"])
    return result


def _units(value: Json, state: Record) -> list[Json]:
    """Parse the complete exact task/path contract rather than dropping excluded work."""
    units = coordination._indexed(value, "unit")
    tasks = _tasks(state)
    require(set(units) == set(tasks), "Units must identify every current declared task exactly")
    result: list[Json] = []
    for identifier, unit in sorted(units.items()):
        scopes = [
            coordination._relative(path)
            for path in coordination._items(unit.get("scope"), "unit scope")
        ]
        require(
            bool(scopes) and len(scopes) == len(set(scopes)),
            "Unit scope must be nonempty and unique",
        )
        require(
            sorted(scopes)
            == sorted(
                coordination._texts(tasks[identifier].get("candidate_paths"), "candidate paths")
            ),
            f"Accepted unit scope must match declared candidate paths: {identifier}",
        )
        result.append({"id": identifier, "scope": sorted(scopes)})
    return result


def validate(state: Record) -> None:
    """Validate optional gate structure without forcing historical programs to adopt."""
    contract = state.get("phases")
    if contract is None:
        return
    phases = coordination._record(contract, "phases")
    require(phases.get("schema_version") == 1, "Unsupported phases contract")
    coordination._text(phases.get("adopted_at"), "adoption timestamp")
    coordination._text(phases.get("adopted_by"), "adoption actor")
    coordination._items(phases.get("history"), "phase history")
    for key in ("inventory", "review", "acceptance"):
        value = phases.get(key)
        if value is not None:
            item = coordination._record(value, key)
            coordination._record(item.get("binding"), "planning binding")
            coordination._items(item.get("evidence"), "phase evidence")


def evidence_references(state: Record) -> list[Json]:
    contract = state.get("phases")
    if contract is None:
        return []
    phases = coordination._record(contract, "phases")
    refs: list[Json] = []
    for value in coordination._items(phases.get("history"), "phase history"):
        refs.extend(
            coordination._items(
                coordination._record(value, "phase record").get("evidence"), "phase evidence"
            )
        )
    return refs


def _current(program: Program, state: Record) -> Record:
    phases = coordination._record(state.get("phases"), "adopted phases")
    inventory = coordination._record(phases.get("inventory"), "frozen inventory")
    require(
        inventory["binding"] == planning_binding(state),
        "Stale planning contract; freeze and independently review current definitions",
    )
    _verify_planning(
        program, state, coordination._text(phases.get("planning_path"), "planning path")
    )
    program.verify_refs(evidence_references(state))
    return phases


def _verify_planning(program: Program, state: Record, name: str) -> None:
    """Keep frozen planning provenance separate from evolving implementation files."""
    if __package__:
        from tools.feature_program import bounded_bytes, safe_path
    else:
        from feature_program import bounded_bytes, safe_path
    path = safe_path(program.repo, name)
    require(
        hashlib.sha256(bounded_bytes(path, 8 * 1024 * 1024)).hexdigest()
        == state.get("planning_sha256"),
        "Stale frozen planning input digest",
    )
    _planning().verify_planning(program, state)


def guard_execution(
    program: Program,
    state: Record,
    task_id: str,
    scope: list[str],
    *,
    membership_check: bool = True,
) -> None:
    """Enforce gates inside claim/submit themselves, including direct API callers."""
    if state.get("phases") is None:
        return
    goal = coordination._record(state.get("objective"), "objective")
    if membership_check and goal.get("membership_revision") is not None:
        require(
            task_id in coordination._texts(goal.get("task_ids"), "members"),
            "Task is excluded from amended current objective",
        )
    program.require_current_location()
    phases = _current(program, state)
    inventory = coordination._record(phases.get("inventory"), "inventory")
    accepted = coordination._record(phases.get("acceptance"), "execution acceptance")
    review = coordination._record(phases.get("review"), "candidate review")
    require(
        review.get("verdict") == "accepted" and accepted.get("review_digest") == revision(review),
        "Current accepted independent candidate review required",
    )
    require(
        accepted["binding"] == inventory["binding"]
        and accepted["inventory_digest"] == inventory["inventory_digest"],
        "Stale execution acceptance",
    )
    require(
        accepted.get("actor_id") == coordination.current_director_id(state),
        "Execution acceptance requires the current director",
    )
    units = coordination._indexed(accepted.get("units"), "accepted unit")
    require(task_id in units, "Task is outside accepted execution scope")
    allowed = coordination._texts(units[task_id]["scope"], "accepted scope")
    require(
        bool(scope)
        and all(
            any(path == entry or path.startswith(entry.rstrip("/") + "/") for entry in allowed)
            for path in scope
        ),
        "Claim/submission scope is outside exact accepted scope",
    )


@dataclass(frozen=True, slots=True)
class PhaseInput:
    command: PhaseCommand
    name: str
    receipt: Record
    refs: list[Json]


def _planning() -> ModuleType:
    if __package__:
        from tools import feature_program_planning
    else:
        import feature_program_planning
    return feature_program_planning


def prepare(
    program: Program, state: Record, command: PhaseCommand, name: str, expected_revision: str
) -> PhaseInput:
    """Capture one real bound receipt for standalone or prospective phase transitions."""
    receipt, refs = program.frozen_json("_phases", 1, name)
    require(
        receipt.get("expected_revision") == expected_revision, "Stale expected registry revision"
    )
    require(
        all(receipt.get(key) == value for key, value in planning_binding(state).items()),
        "Stale objective/task-definition/planning binding",
    )
    coordination._text(receipt.get("reason"), "phase reason")
    coordination._text(receipt.get("actor_id"), "phase actor")
    require(
        coordination.current_director_id(state) is not None,
        "Phase gates require an appointed current director",
    )
    if receipt.get("evidence"):
        refs += program.evidence(
            "_phases", 1, coordination._texts(receipt["evidence"], "phase evidence")
        )
    return PhaseInput(command, name, receipt, refs)


def renew(
    program: Program,
    state: Record,
    renewal: Record,
    expected_revision: str,
    intake_authors: list[str],
) -> None:
    """Validate reviewed future scope without fabricating persisted intermediate states."""
    for command, key in (
        ("inventory-freeze", "inventory"),
        ("candidate-review", "review"),
        ("execution-accept", "acceptance"),
    ):
        name = coordination._relative(renewal.get(key))
        transition = prepare(program, state, command, name, expected_revision)
        if key == "inventory":
            require(
                set(intake_authors)
                <= set(
                    coordination._texts(
                        transition.receipt.get("inventory_authors"), "inventory authors"
                    )
                ),
                "Renewal must identify all intake authors",
            )
        _apply(program, state, transition)
        coordination._record(state["phases"], "phases")["history"][-1]["transaction"] = (
            "prospective-renewal"
        )
    first = next(iter(_tasks(state)))
    guard_execution(
        program,
        state,
        first,
        coordination._texts(_tasks(state)[first]["candidate_paths"], "paths"),
        membership_check=False,
    )


def apply_command(program: Program, state: Record, args: argparse.Namespace) -> bool:
    """Apply one bound receipt while the caller holds the canonical program lock."""
    if args.command not in COMMANDS:
        return False
    program.require_current_location()
    transition = prepare(program, state, args.command, args.input, revision(state))
    receipt, refs = transition.receipt, transition.refs
    actor = receipt["actor_id"]
    proposal = deepcopy(state)
    _apply(program, proposal, transition)
    program.event(
        proposal,
        args.command,
        {
            "actor": actor,
            "reason": receipt["reason"],
            "expected_revision": receipt["expected_revision"],
            "evidence": refs,
        },
    )
    validate(proposal)
    state.clear()
    state.update(proposal)
    return True


def _apply(program: Program, state: Record, transition: PhaseInput) -> None:
    receipt = transition.receipt
    actor = receipt["actor_id"]
    director = coordination.current_director_id(state)
    current = planning_binding(state)
    if transition.command == "phase-adopt":
        require(actor == director, "Only current director may adopt phases")
        require(state.get("phases") is None, "Phases already adopted")
        require(
            receipt.get("acknowledged") is True,
            "Adoption must explicitly acknowledge current enforcement",
        )
        planning_path = coordination._relative(
            receipt.get("planning_path", state.get("planning_path"))
        )
        _verify_planning(program, state, planning_path)
        state["phases"] = {
            "schema_version": 1,
            "adopted_at": datetime.now(timezone.utc).isoformat(),
            "adopted_by": actor,
            "planning_path": planning_path,
            "inventory": None,
            "review": None,
            "acceptance": None,
            "history": [
                {"action": "phase-adopt", "receipt": deepcopy(receipt), "evidence": transition.refs}
            ],
        }
        return
    phases = coordination._record(state.get("phases"), "adopted phases")
    _verify_planning(
        program, state, coordination._text(phases.get("planning_path"), "planning path")
    )
    entry: Record = {
        "binding": current,
        "actor_id": actor,
        "receipt": deepcopy(receipt),
        "evidence": transition.refs,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    match transition.command:
        case "inventory-freeze":
            require(actor == director, "Only current director may freeze inventory")
            candidate = coordination._relative(receipt.get("candidate_path"))
            candidate_refs = program.evidence("_phases", 1, [candidate])
            candidate_digest = coordination._record(candidate_refs[0], "candidate reference")[
                "sha256"
            ]
            require(
                candidate_digest == receipt.get("candidate_digest"),
                "Stale candidate inventory digest",
            )
            entry.update(
                {"candidate_digest": candidate_digest, "units": _units(receipt.get("units"), state)}
            )
            authors = coordination._texts(receipt.get("inventory_authors"), "inventory authors")
            require(
                bool(authors) and len(authors) == len(set(authors)),
                "Inventory authors must be explicit and unique",
            )
            entry["inventory_authors"] = authors
            entry["inventory_digest"] = revision(
                {
                    "binding": current,
                    "candidate_digest": candidate_digest,
                    "units": entry["units"],
                    "inventory_authors": authors,
                }
            )
            entry["evidence"] = [*transition.refs, *candidate_refs]
            phases.update({"inventory": entry, "review": None, "acceptance": None})
        case "candidate-review":
            _current(program, state)
            inventory = coordination._record(phases["inventory"], "inventory")
            require(
                actor != director and actor != inventory["actor_id"],
                "Candidate reviewer must be independent of director and inventory author",
            )
            require(
                actor
                not in coordination._texts(inventory["inventory_authors"], "inventory authors"),
                "Candidate reviewer must be independent of inventory authors",
            )
            require(
                receipt.get("reviewer_role") == "independent",
                "Explicit independent candidate-review identity required",
            )
            require(
                receipt.get("inventory_digest") == inventory["inventory_digest"]
                and receipt.get("candidate_digest") == inventory["candidate_digest"],
                "Stale candidate-review inventory binding",
            )
            require(
                _units(receipt.get("units"), state) == inventory["units"],
                "Candidate review exact scope mismatch",
            )
            verdict = coordination._choice(
                receipt.get("verdict"), {"accepted", "rejected"}, "candidate verdict"
            )
            findings = coordination._texts(receipt.get("unresolved_findings"), "candidate findings")
            require(
                verdict != "accepted" or not findings,
                "Accepted candidate review has unresolved findings",
            )
            entry.update(
                {
                    "verdict": verdict,
                    "inventory_digest": inventory["inventory_digest"],
                    "candidate_digest": inventory["candidate_digest"],
                    "units": deepcopy(inventory["units"]),
                }
            )
            phases.update({"review": entry, "acceptance": None})
        case "execution-accept":
            _current(program, state)
            inventory = coordination._record(phases["inventory"], "inventory")
            review = coordination._record(phases.get("review"), "independent candidate review")
            require(actor == director, "Only current director may accept execution scope")
            require(
                review.get("verdict") == "accepted"
                and review["binding"] == current
                and review["inventory_digest"] == inventory["inventory_digest"],
                "Current accepted independent candidate review required",
            )
            require(
                receipt.get("inventory_digest") == inventory["inventory_digest"]
                and receipt.get("candidate_digest") == inventory["candidate_digest"],
                "Stale accepted inventory binding",
            )
            require(
                _units(receipt.get("units"), state) == inventory["units"],
                "Execution acceptance exact scope mismatch",
            )
            require(
                receipt.get("acceptance_complete") is True,
                "Explicit complete scope acceptance required",
            )
            entry.update(
                {
                    "inventory_digest": inventory["inventory_digest"],
                    "units": deepcopy(inventory["units"]),
                    "review_digest": revision(review),
                }
            )
            phases["acceptance"] = entry
        case unreachable:
            assert_never(unreachable)
    coordination._items(phases["history"], "phase history").append(
        {"action": transition.command, **deepcopy(entry)}
    )


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name in sorted(COMMANDS):
        command = commands.add_parser(
            name, help="Record opt-in planning gate from bound local receipt"
        )
        command.add_argument(
            "--input", required=True, help="JSON under evidence/_phases/attempt-1/"
        )
    commands.add_parser("phase-binding", help="Print current planning bindings and revision token")
    successor = commands.add_parser(
        "program-succeed",
        help="Guard unclaimed discovery and identify initialized execution successor",
    )
    successor.add_argument("--successor", required=True)
    successor.add_argument("--successor-id", required=True)
    successor.add_argument("--expected-revision", required=True)
    successor.add_argument("--successor-revision", required=True)
    successor.add_argument("--owner", required=True)
    successor.add_argument("--reason", required=True)
    recover = commands.add_parser(
        "program-succeed-recover",
        help="Finish explicitly incomplete succession; never reactivate discovery",
    )
    recover.add_argument("--owner", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument(
        "--publication-id", help="Exact active operation when recovery is ambiguous"
    )


def succession_guard(program: Program) -> None:
    """Reject execution at guarded locations, including interrupted publication."""
    require(
        not program.path("SUCCESSION_INCOMPLETE.json").exists(),
        "Succession publication incomplete; use program-succeed-recover at predecessor",
    )
    if program.path("SUCCEEDED.json").exists():
        if __package__:
            from tools.feature_program import read_json
        else:
            from feature_program import read_json
        marker = read_json(program.path("SUCCEEDED.json"), limit=64 * 1024)
        raise PhaseError(f"Discovery program succeeded; explicitly use {marker.get('successor')}")


def _pair(predecessor: Program, successor_name: str) -> Program:
    if __package__:
        from tools.feature_program import Program, safe_path
    else:
        from feature_program import Program, safe_path
    target = Path(successor_name)
    require(not target.is_absolute(), "Successor must be an explicit repository-relative path")
    successor = Program(predecessor.repo, safe_path(predecessor.repo, successor_name))
    require(successor.path("backlog.json").is_file(), "Successor must already be initialized")
    require(
        successor.directory != predecessor.repo
        and not successor.directory.is_relative_to(predecessor.directory)
        and not predecessor.directory.is_relative_to(successor.directory),
        "Succession paths must be distinct and nonoverlapping",
    )
    for directory in (predecessor.directory, successor.directory):
        require(
            not any(path != directory / "backlog.json" for path in directory.rglob("backlog.json")),
            "Nested initialized programs make succession ambiguous",
        )
    return successor


def _succession_contract(state: Record) -> str:
    """Identify frozen successor provenance while permitting normal execution progress."""
    return revision(
        {
            "program_id": state["program_id"],
            "baseline_head": state["baseline_head"],
            "source": state["source"],
            "planning_sha256": _planning().planning_digest(state),
            "tasks": _definitions(state),
            "workstreams": state["workstreams"],
            "verification_policy_sha256": state.get("verification_policy_sha256"),
            "objective": coordination.objective_contract(state),
        }
    )


@dataclass(frozen=True, slots=True)
class Publication:
    marker: Record
    recovery: bool = False
    operation_id: str | None = None


def _publish(predecessor: Program, successor: Program, publication: Publication) -> None:
    """Predecessor guard always precedes any successor publication; retry is idempotent."""
    if __package__:
        from tools.feature_program import atomic_write, canonical, read_json, bounded_bytes
    else:
        from feature_program import atomic_write, canonical, read_json, bounded_bytes
    marker = publication.marker
    encoded = canonical(marker)
    for program, key in ((predecessor, "predecessor_revision"), (successor, "successor_revision")):
        current = read_json(program.path("backlog.json"))
        program.validate(current)
        if current.get("planning_path") is not None:
            _verify_planning(
                program, current, coordination._text(current["planning_path"], "planning path")
            )
        if publication.recovery and program is successor:
            require(
                _succession_contract(read_json(successor.path("backlog.json")))
                == marker["successor_contract"],
                "Frozen successor provenance changed; preserve markers and investigate",
            )
            continue
        require(
            hashlib.sha256(bounded_bytes(program.path("backlog.json"), 8 * 1024 * 1024)).hexdigest()
            == marker[key],
            "Succession registry changed; preserve markers and investigate",
        )
    operation_id = publication.operation_id or _planning().publication_register(
        predecessor, "succession", marker, successor, legacy_observed=publication.recovery
    )
    _planning().publication_begin(predecessor, operation_id)
    atomic_write(predecessor.path("SUCCESSION_INCOMPLETE.json"), encoded)
    atomic_write(successor.path("SUCCESSION_INCOMPLETE.json"), encoded)
    atomic_write(successor.path("PREDECESSOR.json"), encoded)
    atomic_write(predecessor.path("SUCCEEDED.json"), encoded)
    successor.path("SUCCESSION_INCOMPLETE.json").unlink()
    predecessor.path("SUCCESSION_INCOMPLETE.json").unlink()
    _planning().publication_complete(predecessor, operation_id)


def succeed(predecessor: Program, state: Record, args: argparse.Namespace) -> None:
    """Publish sidecars only; never copy/reset tasks or label discovery complete."""
    require(
        args.owner == (coordination.current_director_id(state) or "leader"),
        "Succession requires current director or legacy leader",
    )
    require(
        all(
            task.get("owner") is None and task.get("status") == "planned"
            for task in _tasks(state).values()
        ),
        "Succession requires unclaimed discovery tasks",
    )
    require(args.expected_revision == revision(state), "Stale predecessor registry revision")
    successor = _pair(predecessor, args.successor)
    with successor.lock():
        target = successor.load()
        if target.get("planning_path") is not None:
            _verify_planning(
                successor, target, coordination._text(target["planning_path"], "planning path")
            )
        require(
            target["program_id"] == args.successor_id
            and target["program_id"] != state["program_id"],
            "Successor identity must match initialized distinct program",
        )
        require(args.successor_revision == revision(target), "Stale successor registry revision")
        require(
            not successor.path("PREDECESSOR.json").exists(), "Successor already has a predecessor"
        )
        marker: Record = {
            "schema_version": 1,
            "predecessor": predecessor.directory.relative_to(predecessor.repo).as_posix(),
            "predecessor_id": state["program_id"],
            "successor": args.successor,
            "successor_id": target["program_id"],
            "predecessor_revision": args.expected_revision,
            "successor_revision": args.successor_revision,
            "successor_contract": _succession_contract(target),
            "actor": args.owner,
            "reason": args.reason,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        _publish(predecessor, successor, Publication(marker))


def recover_succession(predecessor: Program, args: argparse.Namespace) -> None:
    """Recheck both frozen registries and finish a guarded transaction under both locks."""
    if __package__:
        from tools.feature_program import read_json, atomic_write, canonical
    else:
        from feature_program import read_json, atomic_write, canonical
    located = _planning().publication_find(
        predecessor, "succession", getattr(args, "publication_id", None)
    )
    marker = (
        coordination._record(located[1]["marker"], "succession marker")
        if located is not None
        else read_json(predecessor.path("SUCCESSION_INCOMPLETE.json"), limit=64 * 1024)
    )
    require(marker.get("schema_version") == 1, "Unsupported succession publication receipt")
    for field in ("predecessor", "successor", "predecessor_id", "successor_id", "actor", "reason"):
        coordination._text(marker.get(field), f"succession {field}")
    for field in ("predecessor_revision", "successor_revision", "successor_contract"):
        require(
            bool(re.fullmatch(r"[a-f0-9]{64}", coordination._text(marker.get(field), field))),
            f"Invalid succession {field}",
        )
    coordination._timestamp(marker.get("recorded_at"), "succession recording timestamp")
    require(
        marker.get("predecessor") == predecessor.directory.relative_to(predecessor.repo).as_posix(),
        "Recover succession at its exact predecessor",
    )
    successor = _pair(predecessor, coordination._text(marker.get("successor"), "successor"))
    require(
        not successor.path("SUCCEEDED.json").exists(),
        "Successor has itself succeeded; investigate incomplete publication",
    )
    with successor.lock(publication_recovery=True):
        state = read_json(predecessor.path("backlog.json"))
        target = read_json(successor.path("backlog.json"))
        predecessor.validate(state)
        successor.validate(target)
        if target.get("planning_path") is not None:
            _verify_planning(
                successor, target, coordination._text(target["planning_path"], "planning path")
            )
        require(
            state["program_id"] == marker["predecessor_id"]
            and target["program_id"] == marker["successor_id"],
            "Succession identity changed",
        )
        require(
            args.owner == (coordination.current_director_id(state) or "leader"),
            "Succession recovery requires predecessor director",
        )
        require(marker.get("actor") == args.owner, "Recovery actor must match publication actor")
        for program, name in (
            (predecessor, "SUCCEEDED.json"),
            (successor, "SUCCESSION_INCOMPLETE.json"),
            (successor, "PREDECESSOR.json"),
        ):
            if program.path(name).exists():
                require(
                    read_json(program.path(name)) == marker,
                    "Conflicting succession marker; preserve and investigate",
                )
        require(
            _succession_contract(target) == marker.get("successor_contract"),
            "Frozen successor provenance changed; preserve guards and investigate",
        )
        require(
            revision(state) == marker.get("predecessor_revision"),
            "Predecessor registry changed; preserve guards and investigate",
        )
        atomic_write(
            predecessor.path("SUCCESSION_RECOVERY.json"),
            canonical(
                {
                    "actor": args.owner,
                    "reason": args.reason,
                    "observed_successor_revision": revision(target),
                    "publication": deepcopy(marker),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
        if (
            located is not None
            and not predecessor.path("SUCCESSION_INCOMPLETE.json").exists()
            and predecessor.path("SUCCEEDED.json").exists()
        ):
            _planning().publication_complete(
                predecessor, coordination._text(located[0]["id"], "publication ID")
            )
        else:
            _publish(
                predecessor,
                successor,
                Publication(
                    marker,
                    recovery=True,
                    operation_id=coordination._text(located[0]["id"], "publication ID")
                    if located is not None
                    else None,
                ),
            )
