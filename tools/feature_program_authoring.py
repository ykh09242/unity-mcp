"""Authoring over authoritative preparations plus explicit guarded flow host dispatch.

Stored requests remain inert. Only explicit live execution and normal record publication
perform those actions; closure preparation may materialize immutable evidence files.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import os
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Final

if __package__:
    from tools import feature_program_coordination as coordination
    from tools import feature_program_coverage as coverage
    from tools import feature_program_phases as phases
    from tools import feature_program_planning as planning
    from tools import feature_program_presentation as presentation
    from tools import feature_program_questions as questions
    from tools import feature_program_verification as verification
    from tools import feature_program_delivery as provider_delivery
else:
    import feature_program_coordination as coordination
    import feature_program_coverage as coverage
    import feature_program_phases as phases
    import feature_program_planning as planning
    import feature_program_presentation as presentation
    import feature_program_questions as questions
    import feature_program_verification as verification
    import feature_program_delivery as provider_delivery

if TYPE_CHECKING:
    from tools.feature_program import PreparedChange, Program, ReadBudget, _ValidatedState
    from tools.feature_program_batch import _CheckedPlan
    from tools.feature_program_closure import _StageInspection

Json = coordination.Json
Record = coordination.Record
READ_COMMANDS: Final = frozenset(
    {
        "authoring-support",
        "scaffold",
        "preview",
        "recovery-readiness",
        "delivery-inputs",
        "verification-packet",
        "submission-inputs",
        "premerge-inputs",
        "cleanup-inputs",
        "inputs-prepare",
        "inputs-capture",
        "inputs-compare",
        "review-queue",
        "review-assignment",
        "verification-observation",
        "verification-environment",
        "closure-readiness",
        "closure-prepare",
        "review-handoff-prepare",
        "evidence-prepare",
        "batch-prepare",
        "records-prepare",
        "handoff-action",
        "handoff-report-prepare",
        "review-capacity",
        "reviewer-allocation",
        "batch-inspect",
    }
)
PUBLICATION_COMMANDS: Final = frozenset(
    {
        "publication-adopt",
        "publication-reconcile",
        "program-succeed",
        "program-succeed-recover",
        "relocate",
        "relocate-recover",
    }
)
COMMANDS: Final = (
    coordination.COMMANDS
    | questions.COMMANDS
    | phases.COMMANDS
    | planning.COMMANDS
    | PUBLICATION_COMMANDS
    | {
        "claim",
        "submit",
        "complete",
        "rework",
        "resume",
        "delivery",
        "note",
        "block",
        "release",
        "reopen",
        "language",
        "checkpoint",
        "coverage-record",
        "presentation-record",
        "verification-record",
        "review-handoff-record",
    }
)
TEMPLATES: Final = ("revalidation", "execution", "review", "delivery", "resume")
HEAD_COMMANDS: Final = (
    frozenset(
        {
            "claim",
            "submit",
            "complete",
            "resume",
            "checkpoint",
            "director-appoint",
            "transfer",
            "transfer-ack",
            "transfer-accept",
            "objective-complete",
            "verification-record",
        }
    )
    | questions.COMMANDS
)
LIMIT: Final = 8 * 1024 * 1024
PROVIDER_FIELDS: Final = (
    "branch",
    "base",
    "commits",
    "pr_url",
    "tested_commit",
    "ci",
    "merge_status",
    "merge_commit",
)


def _core() -> ModuleType:
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _errors() -> tuple[type[Exception], ...]:
    return (
        _core().ProgramError,
        coordination.GovernanceError,
        questions.QuestionError,
        phases.PhaseError,
        planning.PlanningError,
        provider_delivery.DeliveryError,
        OSError,
    )


def add_commands(commands) -> None:
    commands.add_parser("authoring-support", help="List exact preview/scaffold input families")
    preview = commands.add_parser("preview", help="Prepare a command without locks or publication")
    preview.add_argument("--request", required=True, help='Program-relative JSON: {"argv": [...]}')
    scaffold = commands.add_parser("scaffold", help="Print editable, nonpassing input JSON")
    scaffold.add_argument("--operation", required=True, choices=sorted(COMMANDS | {"template"}))
    scaffold.add_argument("--task")
    scaffold.add_argument("--kind", choices=TEMPLATES)
    scaffold.add_argument("--scope", action="append")
    commands.add_parser("recovery-readiness", help="Observe recovery prerequisites without repair")
    delivery = commands.add_parser(
        "delivery-inputs", help="Bind supplied frozen provider observations"
    )
    delivery.add_argument(
        "--input", required=True, help="Program-relative frozen-observation request"
    )
    for name in (
        "review-handoff-prepare",
        "review-handoff-record",
        "evidence-prepare",
        "batch-prepare",
        "records-prepare",
        "handoff-action",
        "handoff-report-prepare",
        "review-capacity",
        "reviewer-allocation",
    ):
        command = commands.add_parser(
            name, help="Prepare or record finite source-bound workflow facts"
        )
        command.add_argument("--input", required=True, help="Program-relative immutable JSON")
        if name == "review-handoff-prepare":
            command.add_argument(
                "--materialize", action="store_true", help="Freeze exact inert packet only"
            )
        if name in {
            "evidence-prepare",
            "batch-prepare",
            "records-prepare",
            "handoff-report-prepare",
        }:
            command.add_argument(
                "--result-path", help="Retain an actual immutable preparation result"
            )
    command = commands.add_parser(
        "batch-inspect", help="Inspect an exact batch without publication"
    )
    command.add_argument("--plan", required=True)
    command = commands.add_parser("batch-publish", help="Publish one guarded local record batch")
    command.add_argument("--plan", required=True)
    command.add_argument("--owner", required=True)
    command.add_argument("--expected-revision", required=True)
    command.add_argument("--result-path", help="Retain the actual publication result")
    command = commands.add_parser("verification-record", help="Record immutable verification facts")
    command.add_argument("--input", required=True, help="Program-relative verification record JSON")
    command = commands.add_parser(
        "verification-packet", help="Print exact verification context JSON"
    )
    command.add_argument("id")
    for name in ("submission-inputs", "premerge-inputs", "cleanup-inputs"):
        command = commands.add_parser(name, help="Prepare ordinary input from frozen observations")
        command.add_argument("--input", required=True, help="Program-relative preparation JSON")

    command = commands.add_parser("inputs-prepare", help="Declare a finite known check input scope")
    command.add_argument("--check-id", required=True)
    command.add_argument("--path", action="append", required=True)
    command.add_argument("--complete", action="store_true")
    command = commands.add_parser(
        "inputs-capture", help="Capture exact declared inputs without execution"
    )
    for field in ("manifest", "environment", "method"):
        command.add_argument("--" + field, required=True, help="Program-relative frozen JSON")
    command.add_argument("--git-revision", help="Explicit local Git object revision")
    command = commands.add_parser(
        "inputs-compare", help="Compare frozen input captures conservatively"
    )
    command.add_argument("--before", required=True)
    command.add_argument("--after", required=True)
    commands.add_parser("review-queue", help="Project complete review obligations once")
    command = commands.add_parser(
        "review-assignment", help="Prepare an explicit verifier assignment"
    )
    command.add_argument("--input", required=True)
    command = commands.add_parser(
        "verification-environment", help="Read actual runtime identity without execution"
    )
    command.add_argument(
        "--environment", help="Optional program-relative safe declared environment JSON"
    )
    command.add_argument(
        "argv", nargs=argparse.REMAINDER, help="-- followed by executable and arguments"
    )
    command = commands.add_parser(
        "verification-observation", help="Prepare an observation from actual frozen capture"
    )
    command.add_argument("--capture", required=True, help="Program-relative capture reference JSON")
    command.add_argument(
        "--request", required=True, help="Program-relative current request binding JSON"
    )
    commands.add_parser("closure-readiness", help="Observe ordinary closure requirements")
    command = commands.add_parser(
        "closure-prepare", help="Preflight the full record sequence without publication"
    )
    command.add_argument("--input", required=True)
    command.add_argument(
        "--result-path", help="Optional immutable evidence path for actual preparation result"
    )
    command = commands.add_parser(
        "verification-run", help="Execute one explicit live argv with immutable evidence"
    )
    command.add_argument("id")
    command.add_argument("--owner", required=True)
    command.add_argument(
        "--request", required=True, help="Program-relative current adopted request binding JSON"
    )
    command.add_argument("--expected-revision", required=True)
    command.add_argument("--capture-dir", required=True)
    command.add_argument("--cwd", default=".")
    command.add_argument("--execution-kind", default="test")
    command.add_argument("--timeout-seconds", type=float, default=300)
    command.add_argument("--stream-limit-bytes", type=int, default=1024 * 1024)
    command.add_argument("--artifact", action="append", default=[])
    command.add_argument("argv", nargs="*", help="-- followed by live executable and arguments")
    command = commands.add_parser(
        "closure-resume",
        help="Publish only uncommitted exact record stages using current task/director authority",
    )
    command.add_argument("--plan", required=True)
    command.add_argument("--owner", required=True)
    command.add_argument("--expected-revision", required=True)


def _parsers() -> dict[str, argparse.ArgumentParser]:
    for action in _core().parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("Missing command parser")


def support() -> Record:
    parsers = _parsers()
    return {
        "commands": [
            {
                "command": name,
                "arguments": [
                    {
                        "name": action.dest,
                        "flags": action.option_strings,
                        "required": action.required or not action.option_strings,
                        "repeatable": isinstance(action, argparse._AppendAction),
                    }
                    for action in parsers[name]._actions
                    if action.dest != "help"
                ],
                "receipt_fields": sorted(_shape(name)),
                "assessment": "partial_publication"
                if name in PUBLICATION_COMMANDS
                else "full_state",
                "inspected": "authoritative preparation and final save preflight"
                if name not in PUBLICATION_COMMANDS
                else "shared read-only publication preparation",
                "not_assessed": ["locked publication and resulting external files"]
                if name in PUBLICATION_COMMANDS
                else ["future locked execution; future timestamps"],
            }
            for name in sorted(COMMANDS)
        ],
        "templates": list(TEMPLATES),
        "read_operations": {
            "recovery-readiness": "bounded current registry, refs, pending decisions, collisions, lock observations",
            "delivery-inputs": "explicit provider path/hash and individual task/attempt/submission/check bindings",
            "verification-packet": "exact task/attempt/submission/source context and obligations",
            "submission-inputs": "ordinary nonpassing execution draft until actual complete evidence exists",
            "premerge-inputs": "advisory frozen provider execution/context preparation",
            "cleanup-inputs": "advisory frozen provider execution/context preparation",
        },
        "request_format": {"argv": ["COMMAND", "actual command arguments and options"]},
        "advisory": True,
    }


def _request(program: Program, name: str) -> tuple[argparse.Namespace, Record]:
    core = _core()
    request, captured = core.read_json_frozen(program.path(name), limit=LIMIT)
    core.require(set(request) == {"argv"}, "Preview request requires only argv")
    argv = [
        core.string(value, "command argument")
        for value in core.sequence(request["argv"], "command arguments")
    ]
    core.require(bool(argv) and argv[0] in COMMANDS, "Unsupported preview command")
    core.require(not any(value in {"--help", "-h"} for value in argv), "Help is not a transition")
    # Parsing the selected subparser prevents repository/program overrides or recursive reads.
    parser = deepcopy(_parsers()[argv[0]])
    parser.exit_on_error = False
    parser.error = lambda message: (_ for _ in ()).throw(core.ProgramError(message))
    try:
        args = parser.parse_args(argv[1:])
    except argparse.ArgumentError as exc:
        raise core.ProgramError(str(exc)) from exc
    args.command = argv[0]
    for field in ("owner", "reason", "next_action", "note"):
        if hasattr(args, field):
            core.string(getattr(args, field), field)
    return args, {"path": name, "sha256": captured}


def prepare(
    program: Program,
    state: Record,
    args: argparse.Namespace,
    *,
    observed_head: str | None = None,
    observed_dirty: str = "",
    publication_base: Record | None = None,
    materialize_handoff: bool = False,
) -> PreparedChange:
    """Dispatch only to authoritative, detached preparation; no effects are intercepted."""
    core = _core()
    command = args.command
    proposal = deepcopy(state)
    if command in coordination.COMMANDS:
        proposal = coordination.prepare_command(program, state, args, observed_head=observed_head)
    elif command in questions.COMMANDS:
        proposal = questions.prepare_command(program, state, args, observed_head=observed_head)
    elif command in phases.COMMANDS:
        phases.apply_command(program, proposal, args)
    elif command in planning.COMMANDS:
        return planning.prepare_transition(program, state, args)
    elif command == "coverage-record":
        return coverage.prepare_record(program, state, args.input)
    elif command == "presentation-record":
        return presentation.prepare_record(program, state, args.input)
    elif command == "verification-record":
        return verification.prepare_record(
            program,
            state,
            args.input,
            observed_head=observed_head,
            publication_base=publication_base,
        )
    elif command == "claim":
        proposal = program.prepare_claim(state, args, observed_head=observed_head)
    elif command == "submit":
        proposal = program.prepare_submit(state, args, observed_head=observed_head)
        if getattr(args, "handoff", None) is not None:
            proposal = prepare_submission_handoff(
                program, proposal, args, materialize=materialize_handoff
            )["state"]
    elif command == "review-handoff-record":
        proposal = prepare_handoff_record(
            program, state, args.input, materialize=materialize_handoff
        )
    elif command in {"complete", "rework"}:
        proposal = program.prepare_review(state, args, observed_head=observed_head)
    elif command == "resume":
        return program.prepare_resume(state, args, observed_head=observed_head)
    elif command == "language":
        proposal = program.prepare_language(state, args)
    elif command == "checkpoint":
        proposal = program.prepare_checkpoint(
            state, args, observed_head=observed_head, observed_dirty=observed_dirty
        )
    elif command == "delivery":
        program.delivery(proposal, args)
    elif command in {"note", "block", "release", "reopen"}:
        program.transition(proposal, args)
    else:
        raise core.ProgramError("Command requires publication preparation")
    return core.PreparedChange(proposal)


def _publication(program: Program, state: Record, args: argparse.Namespace) -> Record:
    core = _core()
    match args.command:
        case "publication-adopt":
            receipt, refs, inventory = planning.prepare_publication_adoption(
                program, state, args.input
            )
            return {
                "inventory": inventory,
                "decision": receipt,
                "evidence": refs,
                "not_assessed": [
                    "locked ledger bootstrap/import, pending adoption replay and publication"
                ],
            }
        case "publication-reconcile":
            ledger, item, name, result = planning.prepare_publication_reconciliation(
                program, state, args.input
            )
            return {
                "proposed_operation": item,
                "prepared_partial_ledger": ledger,
                "result_path": name,
                "proposed_result": result,
                "not_assessed": [
                    "locked result freezing, final active-to-history movement and ledger publication"
                ],
            }
        case "program-succeed":
            _, marker = phases.prepare_succession(program, state, args)
            return {
                "proposed_marker": marker,
                "not_assessed": ["locked ledger registration and successor sidecars"],
            }
        case "program-succeed-recover":
            _, marker, _, _ = phases.prepare_succession_recovery(program, args)
            return {
                "proposed_marker": marker,
                "not_assessed": ["locked recovery receipt and succession publication"],
            }
        case "relocate":
            target, original, names = core.prepare_relocation(program, state, args)
            return {
                "destination": target.directory.relative_to(program.repo).as_posix(),
                "original_registry_sha256": core.sha(original),
                "copy_inventory": names,
                "not_assessed": [
                    "exclusive destination, copied artifact validation, remapped state and publication"
                ],
            }
        case "relocate-recover":
            _, marker, located = core.prepare_relocation_recovery(program, args)
            return {
                "marker": marker,
                "tracked_operation": located[0] if located else None,
                "not_assessed": [
                    "locked recovery registration, archive and completion publication"
                ],
            }
        case _:
            raise core.ProgramError("Unsupported publication command")


def _preflight(program: Program, prepared: PreparedChange) -> None:
    guard = coverage.publication_guard(program, prepared.state)
    verification_guard = verification.publication_guard(program, prepared.state)
    provider_guard = delivery_publication_guard(program, prepared.state)
    program.prepare_save(prepared.state)
    if prepared.before_publish is not None:
        prepared.before_publish()
    if guard is not None:
        guard()
    if verification_guard is not None:
        verification_guard()
    if provider_guard is not None:
        provider_guard()


def _delta(before: Record, after: Record) -> list[Json]:
    return [
        {"field": key, "before": deepcopy(before.get(key)), "after": deepcopy(after.get(key))}
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    ]


def category_projection(state: Record) -> Record:
    categories = coordination._indexed(coordination._categories(state), "category")
    tasks = coordination._indexed(state["tasks"], "task")
    rows: list[Json] = []
    placement: Record = {}
    for identifier, item in categories.items():
        direct = coordination._texts(item["task_ids"], "category tasks")
        descendants = set(direct)
        for other_id, other in categories.items():
            cursor = other_id
            while categories[cursor].get("parent_id") is not None:
                cursor = str(categories[cursor]["parent_id"])
                if cursor == identifier:
                    descendants.update(coordination._texts(other["task_ids"], "category tasks"))
        for task_id in direct:
            placement[task_id] = identifier
        rows.append(
            {
                "id": identifier,
                "parent_id": item.get("parent_id"),
                "task_ids": direct,
                "direct_count": len(direct),
                "descendant_count": len(descendants),
                "direct_status_counts": dict(Counter(str(tasks[key]["status"]) for key in direct)),
                "descendant_status_counts": dict(
                    Counter(str(tasks[key]["status"]) for key in descendants)
                ),
            }
        )
    explicit = {
        key
        for value in coordination._items(state.get("categories", []), "categories")
        for key in coordination._texts(
            coordination._record(value, "category").get("task_ids", []), "members"
        )
    }
    return {
        "categories": rows,
        "placement": placement,
        "unassigned_to_explicit_category": sorted(set(tasks) - explicit),
        "unassigned_policy": "Canonical workstream-root fallback remains assigned.",
    }


def preview(program: Program, state: Record, args: argparse.Namespace) -> Record:
    core = _core()
    result: Record = {
        "command": args.command,
        "observed_revision": phases.revision(state),
        "advisory": True,
        "observed_at": core.utc(),
        "timestamps": "Proposal timestamps describe this observation, not eventual execution.",
        "predicted_state": None,
        "changes": [],
        "status": "failed",
    }
    try:
        # Recovery uses its own authoritative location/registry validators below.
        if args.command not in {"relocate-recover", "program-succeed-recover"}:
            program.require_current_location()
            program.validate(state)
        input_refs = []
        for field in ("input", "receipt", "review", "revalidation"):
            if hasattr(args, field):
                name = getattr(args, field)
                try:
                    captured = core.sha(core.bounded_bytes(program.path(name), LIMIT))
                except _errors():
                    # The actual preparation owns missing/path/size failure precedence.
                    continue
                input_refs.append({"path": name, "sha256": captured})
        if args.command in PUBLICATION_COMMANDS:
            with coordination.preparation_clock(str(result["observed_at"])):
                preparation = _publication(program, state, args)
            result.update(
                {
                    "status": "not_assessed",
                    "preparation": preparation,
                    "assessment": "partial_publication",
                    "reasons": [],
                }
            )
            core.require(len(core.canonical(result)) <= LIMIT, "Authoring output exceeds 8 MiB")
            with coordination.preparation_clock(str(result["observed_at"])):
                fresh = _publication(program, state, args)
            core.require(
                core.canonical(fresh) == core.canonical(preparation),
                "Publication inputs changed during preview; refresh",
            )
            program.verify_refs(input_refs)
            return result
        head = program.current_head() if args.command in HEAD_COMMANDS else None
        dirty = (
            core.git(program.repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
            if args.command == "checkpoint"
            else ""
        )
        with coordination.preparation_clock(str(result["observed_at"])):
            prepared = prepare(program, state, args, observed_head=head, observed_dirty=dirty)
        _preflight(program, prepared)
        result.update(
            {
                "status": "validated",
                "assessment": "full_state",
                "reasons": [],
                "predicted_state": prepared.state,
                "changes": _delta(state, prepared.state),
                "not_assessed": ["future locked execution and authorization"],
            }
        )
        if args.command == "category-set":
            before, after = category_projection(state), category_projection(prepared.state)
            result["categories"] = {
                "before": before,
                "after": after,
                "moved_tasks": [
                    key
                    for key in before["placement"]
                    if before["placement"][key] != after["placement"][key]
                ],
            }
        if args.command == "delivery":
            result["delivery"] = program.current_delivery(program.task(prepared.state, args.id))
        # A second authoritative read validates sources, input refs and publication observations
        # after projections/output preflight. It does not publish a second event.
        core.require(len(core.canonical(result)) <= LIMIT, "Authoring output exceeds 8 MiB")
        refreshed_dirty = (
            core.git(program.repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
            if args.command == "checkpoint"
            else dirty
        )
        with coordination.preparation_clock(str(result["observed_at"])):
            refreshed = prepare(
                program, state, args, observed_head=head, observed_dirty=refreshed_dirty
            )
        _preflight(program, refreshed)
        core.require(
            core.canonical(refreshed.state) == core.canonical(prepared.state),
            "Prepared state changed during preview; refresh",
        )
        program.verify_refs(input_refs)
        if head is not None:
            core.require(program.current_head() == head, "HEAD changed during preview; refresh")
    except _errors() as exc:
        result.update(
            {
                "status": "failed",
                "reasons": [str(exc)],
                "failure_type": type(exc).__name__,
                "predicted_state": None,
                "changes": [],
            }
        )
        result.pop("categories", None)
        result.pop("delivery", None)
    return result


def _shape(command: str) -> Record:
    """Editable inputs, not a second validation schema. Canonical preparation validates them."""
    shapes: dict[str, Record] = {
        "verification-record": {
            "schema_version": 1,
            "operation": "verification-record",
            "id": None,
            "kind": "request",
            "task_id": None,
            "actor_id": None,
            "expected_revision": None,
            "reason": None,
            "lane": None,
            "verifier_id": None,
            "check_ids": [],
            "input_scope": [],
            "input_complete": False,
            "evidence_destination": None,
            "next_trigger": None,
        },
        "presentation-record": {
            "schema_version": 1,
            "operation": "presentation-record",
            "id": None,
            "program_id": None,
            "actor_id": None,
            "locale": None,
            "reason": None,
            "authors": [],
            "expected_revision": None,
            "operations": [],
            "review": None,
        },
        "category-set": {"categories": [], "reason": None},
        "actor-record": {
            "id": None,
            "role": None,
            "parent_id": None,
            "decision": None,
            "reason": None,
            "provenance": "unknown",
            "model_id": None,
            "reasoning_effort": None,
            "contact": None,
            "observed_status": "unknown",
            "review_wip_limit": None,
        },
        "director-appoint": {
            "actor_id": None,
            "acknowledged": False,
            "authority": None,
            "evidence": [],
        },
        "transfer": {
            "id": None,
            "role": None,
            "from_actor": None,
            "to_actor": None,
            "task_id": None,
            "goal": None,
            "constraints": [],
            "decisions": [],
            "unresolved_findings": [],
            "changed_paths": [],
            "evidence": [],
            "next_action": None,
        },
        "transfer-ack": {"transfer_id": None, "actor_id": None, "summary": None, "evidence": []},
        "transfer-accept": {"transfer_id": None, "actor_id": None, "summary": None, "evidence": []},
        "objective-record": {
            "id": None,
            "title": None,
            "criteria": [],
            "task_ids": [],
            "supplied_by": "user",
            "human_authorization": None,
        },
        "objective-complete": {
            "actor_id": None,
            "objective_id": None,
            "membership_revision": None,
            "summary": None,
            "criteria_results": [],
            "task_acceptance": [],
            "unresolved_findings": ["Acceptance not assessed"],
            "remote_state_checked": False,
            "required_ci": [],
            "ci": [],
            "integrated_review": None,
            "evidence": [],
        },
        "question-issue": {
            "id": None,
            "version": 1,
            "actor_id": None,
            "prompt": None,
            "why": None,
            "requested_decision": None,
            "options": [],
            "affected_task_ids": [],
            "objective_id": None,
        },
        "question-acknowledge": {
            "question_id": None,
            "version": None,
            "submission_id": None,
            "actor_id": None,
            "summary": None,
        },
        "question-resolve": {
            "question_id": None,
            "version": None,
            "submission_id": None,
            "actor_id": None,
            "summary": None,
        },
        "question-withdraw": {
            "question_id": None,
            "version": None,
            "actor_id": None,
            "summary": None,
        },
        "phase-adopt": {"acknowledged": False, "planning_path": None},
        "inventory-freeze": {
            "candidate_path": None,
            "candidate_digest": None,
            "inventory_authors": [],
            "units": [],
        },
        "candidate-review": {
            "inventory_digest": None,
            "candidate_digest": None,
            "units": [],
            "reviewer_role": "independent",
            "verdict": "pending",
            "unresolved_findings": ["Independent review not performed"],
        },
        "execution-accept": {
            "inventory_digest": None,
            "candidate_digest": None,
            "units": [],
            "acceptance_complete": False,
        },
        "coverage-record": {
            "schema_version": 1,
            "operation": "coverage-record",
            "id": None,
            "actor_id": None,
            "reason": None,
            "expected_revision": None,
            "objective_binding": None,
            "links": [],
            "evidence": [],
        },
        "publication-adopt": {
            "obsolete_writers_quiesced": False,
            "inventory": {},
            "uninspected_artifacts": [],
            "ledger_revision": None,
        },
        "publication-reconcile": {
            "action": None,
            "operation_id": None,
            "replacement_id": None,
            "ledger_revision": None,
        },
    }
    if command in planning.COMMANDS:
        shape: Record = {
            "schema_version": 1,
            "operation": command,
            "id": None,
            "program_id": None,
            "objective_id": None,
            "actor_id": None,
            "reason": None,
            "expected_revision": None,
            "before_binding": None,
            "after_binding": None,
            "evidence": [],
            "renewal": None,
        }
        if command in {"task-plan", "task-prioritize"}:
            shape["changes"] = []
        else:
            shape["membership"] = {"before": [], "after": [], "added": [], "removed": []}
        if command in {"task-admit", "task-split"}:
            shape.update(
                {
                    "source": {
                        "path": None,
                        "sha256": None,
                        "task_ids": [],
                        "review_baseline": None,
                        "reference_base": None,
                    },
                    "planning": {"path": None, "sha256": None},
                    "intake_authors": [],
                    "category_placements": [],
                }
            )
        if command in {"task-split", "task-retire"}:
            shape.update(
                {
                    "predecessor": None,
                    "predecessor_before": None,
                    "lineage": {"predecessor": None, "successors": []},
                    "dependency_changes": [],
                }
            )
        return shape
    shape = deepcopy(shapes.get(command, {}))
    if command in phases.COMMANDS:
        shape.update({"expected_revision": None, "actor_id": None, "reason": None, "evidence": []})
    if command in {"publication-adopt", "publication-reconcile"}:
        shape.update(
            {
                "schema_version": 1,
                "program_id": None,
                "actor_id": None,
                "expected_revision": None,
                "binding": None,
                "reason": None,
                "review": None,
                "inventory_authors": [],
                "evidence": [],
            }
        )
    if command in (HEAD_COMMANDS - {"claim", "submit", "complete", "checkpoint"}):
        shape.update({"source_head": None, "source_digest": None, "scope": []})
    return shape


def scaffold(program: Program, state: Record, args: argparse.Namespace) -> Record:
    core = _core()
    program.validate(state)
    kind = (
        args.kind
        if args.operation == "template"
        else {
            "claim": "revalidation",
            "submit": "execution",
            "complete": "review",
            "rework": "review",
            "resume": "resume",
            "delivery": "delivery",
        }.get(args.operation)
    )
    if kind is not None:
        core.require(bool(args.task), "This scaffold requires --task")
        template_args = argparse.Namespace(id=args.task, kind=kind, scope=args.scope)
        head = program.current_head() if kind in {"revalidation", "resume"} else None
        receipt = program.prepare_template(state, template_args, observed_head=head)
        if kind == "review":
            receipt["reviewer"] = None
        if kind == "delivery":
            receipt.update(
                {
                    "commits": [],
                    "tested_commit": None,
                    "ci": [],
                    "cleanup": {
                        "status": "unknown",
                        "reason": None,
                        "merge_identity": None,
                        "actor_id": None,
                        "recorded_at": None,
                        "evidence": [],
                    },
                }
            )
    else:
        core.require(args.operation != "template", "Template scaffold requires --kind")
        receipt = _shape(args.operation)
        for key, value in {
            "program_id": state["program_id"],
            "objective_id": (state.get("objective") or {}).get("id"),
            "expected_revision": phases.revision(state),
        }.items():
            if key in receipt:
                receipt[key] = value
        if "scope" in receipt and args.scope:
            scope = sorted(set(core.safe_relative(path) for path in args.scope))
            receipt.update(
                {
                    "scope": scope,
                    "source_head": program.current_head(),
                    "source_digest": program.source_digest(scope),
                }
            )
        if args.operation == "category-set":
            receipt["categories"] = deepcopy(state.get("categories", []))
        if args.operation == "objective-complete":
            goal = core.record(state.get("objective"), "current objective")
            receipt["membership_revision"] = goal.get("membership_revision")
            receipt["criteria_results"] = [
                {"criterion": value, "status": "not_run", "evidence": []}
                for value in core.strings(goal["criteria"], "criteria")
            ]
            acceptance: list[Json] = []
            for task_id in core.strings(goal["task_ids"], "objective members"):
                task = program.task(state, task_id)
                attempts = core.sequence(task["attempts"], "attempts")
                attempt = core.record(attempts[-1], "latest attempt") if attempts else {}
                submission = core.record(attempt.get("submission") or {}, "submission")
                review = core.record(attempt.get("review") or {}, "review")
                delivery = program.current_delivery(task) if task["status"] == "done" else {}
                acceptance.append(
                    {
                        "task_id": task_id,
                        "attempt": attempt.get("number"),
                        "source_digest": submission.get("source_digest"),
                        "review_sha256": review.get("sha256"),
                        "delivery_sha256": core.record(
                            delivery.get("receipt") or {}, "delivery receipt"
                        ).get("sha256"),
                    }
                )
            receipt["task_acceptance"] = acceptance
        if args.operation in planning.COMMANDS:
            receipt["before_binding"] = phases.planning_binding(state)
            if "membership" in receipt:
                receipt["membership"]["before"] = deepcopy(state["objective"]["task_ids"])
        if args.operation == "coverage-record":
            receipt["objective_binding"] = coverage.objective_binding(state)
            receipt["uncovered"] = []
        if args.operation == "presentation-record":
            receipt["locale"] = state.get("communication_language")
        if args.operation in {"publication-adopt", "publication-reconcile"}:
            receipt["binding"] = phases.planning_binding(state)
        if args.operation in phases.COMMANDS:
            receipt.update(phases.planning_binding(state))
            if args.operation == "phase-adopt":
                receipt["planning_path"] = state.get("planning_path")
    fields = [item for item in support()["commands"] if item["command"] == args.operation]
    arguments = fields[0]["arguments"] if fields else []
    related: Record = {}
    if args.operation == "presentation-record":
        related["independent_review"] = {
            "schema_version": 1,
            "operation": "presentation-review",
            "program_id": state["program_id"],
            "locale": state.get("communication_language"),
            "payload_sha256": None,
            "reviewer_id": None,
            "reviewer_role": "independent",
            "verdict": "pending",
            "unresolved_findings": ["Language review not performed"],
            "summary": None,
        }
    if args.operation == "objective-complete":
        related["integrated_review"] = {
            "objective_id": receipt.get("objective_id"),
            "membership_revision": receipt.get("membership_revision"),
            "criteria": deepcopy(state["objective"]["criteria"]),
            "source_head": receipt.get("source_head"),
            "source_digest": receipt.get("source_digest"),
            "scope": receipt.get("scope"),
            "reviewer": None,
            "reviewer_role": "independent",
            "verdict": "pending",
            "acceptance_complete": False,
            "unresolved_findings": ["Integrated review not performed"],
            "evidence": [],
        }
    if args.operation in {"publication-adopt", "publication-reconcile"}:
        related["review"] = {
            "program_id": state["program_id"],
            "binding": receipt["binding"],
            "reviewer": None,
            "reviewer_role": "independent",
            "verdict": "pending",
            "inventory_digest": None,
            "unresolved_findings": ["Publication review not performed"],
            "evidence": [],
        }
    request_argv: list[Json] = [args.operation]
    missing_arguments: list[Json] = []
    for field in arguments:
        if not field["required"]:
            continue
        name = field["name"]
        value: Json = (
            args.task
            if name == "id"
            else phases.revision(state)
            if name == "expected_revision"
            else None
        )
        values = args.scope if name == "scope" and args.scope else [value]
        for value in values:
            if field["flags"]:
                request_argv.append(field["flags"][0])
            request_argv.append(value)
        if value is None:
            missing_arguments.append("arguments." + name)
    return {
        "operation": args.operation,
        "kind": kind,
        "receipt": receipt,
        "related_inputs": related,
        "request": {"argv": request_argv} if args.operation != "template" else None,
        "arguments": arguments,
        "status": "not_assessed",
        "acceptance_complete": False,
        "missing_fields": [*_missing(receipt), *missing_arguments],
        "observed_revision": phases.revision(state),
        "limitations": [
            "Editable scaffold only; supply observed facts and run preview."
            " CLI arguments and any receipt must be supplied separately."
        ],
    }


def _missing(value: Json, prefix: str = "") -> list[Json]:
    missing: list[Json] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if item is None or item == []:
                missing.append(path)
            elif isinstance(item, (dict, list)):
                missing.extend(_missing(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            missing.extend(_missing(item, f"{prefix}[{index}]"))
    return missing


def validate_cleanup(program: Program, task: Record, attempt: Record, receipt: Record) -> None:
    """Optional recorded facts; never decide cleanup eligibility or affect CI status."""
    value = receipt.get("cleanup")
    if value is None:
        return
    cleanup = coordination._record(value, "cleanup observation")
    coordination._choice(
        cleanup.get("status"), {"cleaned", "deferred", "unknown"}, "cleanup status"
    )
    for field in ("reason", "actor_id", "merge_identity"):
        coordination._text(cleanup.get(field), f"cleanup {field}")
    coordination._timestamp(cleanup.get("recorded_at"), "cleanup recording timestamp")
    _core().require(
        cleanup.get("merge_identity") == receipt.get("merge_commit"),
        "Cleanup must bind the recorded exact merge identity",
    )
    program.evidence(
        str(task["id"]),
        int(attempt["number"]),
        coordination._texts(cleanup.get("evidence"), "cleanup evidence"),
    )


def provider_observation(program: Program, value: Json, budget: ReadBudget | None = None) -> Record:
    """The one bounded provider-observation reader used by authoring and real delivery."""
    core = _core()
    ref = coverage.reference(value)
    reader = budget if budget is not None else core.ReadBudget(LIMIT)
    captured = reader.capture(program.directory, str(ref["path"]), LIMIT)
    core.require(captured.sha256 == ref["sha256"], "Frozen provider observation changed")
    return reader.json(captured)


def delivery_evidence_references(state: Record) -> list[Json]:
    refs: list[Json] = []
    for task in coordination._items(state["tasks"], "tasks"):
        for delivery in coordination._items(
            coordination._record(task, "task").get("deliveries", []), "deliveries"
        ):
            metadata = coordination._record(
                coordination._record(delivery, "delivery").get("metadata"), "delivery metadata"
            )
            if metadata.get("provider_observation") is not None:
                refs.append(coverage.reference(metadata["provider_observation"]))
    return refs


def delivery_publication_guard(program: Program, future: Record) -> Callable[[], None] | None:
    """Recheck newly supplied provider reliance after the ordinary publication preflight."""
    core = _core()
    path = program.path("backlog.json")
    if not _core().observed_is_file(path):
        return None
    initial = program.read_registry_observation()
    original = core.parse_registry_state(initial, path=path)
    captured = initial.storage_sha256
    existing = {
        core.canonical(core.record(item, "delivery").get("receipt"))
        for task in core.sequence(original["tasks"], "tasks")
        for item in core.sequence(core.record(task, "task").get("deliveries", []), "deliveries")
    }
    refs: list[Json] = []
    for task in core.sequence(future["tasks"], "tasks"):
        task_record = core.record(task, "task")
        for item in core.sequence(task_record.get("deliveries", []), "deliveries"):
            delivery = core.record(item, "delivery")
            metadata = core.record(delivery["metadata"], "delivery metadata")
            if (
                core.canonical(delivery.get("receipt")) not in existing
                and metadata.get("provider_observation") is not None
            ):
                refs.extend(
                    [
                        coverage.reference(delivery["receipt"]),
                        coverage.reference(metadata["provider_observation"]),
                    ]
                )
                context = metadata.get("provider_context")
                if context is not None:
                    policies = set(provider_policy_paths(core.record(context, "provider context")))
                    refs.extend(
                        ref
                        for ref in core.sequence(
                            program.current_attempt(task_record)["evidence"], "attempt evidence"
                        )
                        if core.record(ref, "evidence reference").get("path") in policies
                    )
    if not refs:
        return None

    def recheck() -> None:
        program.require_current_location()
        core.require(
            program.read_registry_observation().storage_sha256 == captured,
            "Registry changed before provider reliance publication",
        )
        program.verify_refs(refs)

    return recheck


def provider_execution(
    program: Program, receipt: Record, budget: ReadBudget | None = None
) -> Record:
    """Assess explicit frozen provider execution without remote access or inferred policy."""
    core = _core()
    reader = budget if budget is not None else core.ReadBudget(64 * 1024 * 1024)
    ref = receipt.get("provider_observation")
    expected = receipt.get("provider_context")
    if ref is None:
        core.require(
            "provider_context" not in receipt, "Provider context requires a frozen observation"
        )
        return {"status": "not_assessed", "adopted": False, "remote_verified_by_tool": False}
    provider = provider_observation(program, ref, reader)
    execution = provider.get("execution")
    if execution is None and "execution" not in provider and "provider_context" not in receipt:
        return {"status": "not_assessed", "adopted": False, "remote_verified_by_tool": False}
    core.require(expected is not None, "Provider execution requires explicit current context")
    context = core.record(expected, "provider context")
    pr = core.record(context.get("pr"), "provider PR context")
    core.require(
        pr.get("url") == receipt.get("pr_url")
        and pr.get("base_ref") == receipt.get("base")
        and pr.get("tested_sha") == receipt.get("tested_commit")
        and pr.get("head_sha") in core.strings(receipt.get("commits"), "delivery commits"),
        "Provider context differs from ordinary delivery identity",
    )
    captured_execution = core.record(execution, "provider execution")
    policy_paths = provider_policy_paths(context)
    core.require(
        set(policy_paths) <= set(core.strings(receipt.get("evidence"), "delivery evidence")),
        "Provider exclusion policy must be ordinary frozen delivery evidence",
    )
    for name in policy_paths:
        core.require(
            name.startswith(f"evidence/{receipt['task_id']}/attempt-{receipt['attempt']}/"),
            "Provider policy evidence must bind the individual task attempt",
        )
        reader.read(program.path(name), LIMIT)
    if captured_execution.get("merge") is not None:
        core.require(
            core.record(captured_execution["merge"], "provider merge").get("squash_sha")
            == receipt.get("merge_commit"),
            "Provider squash differs from ordinary delivery merge identity",
        )
    return {**provider_delivery.assess_execution(execution, expected), "adopted": True}


def provider_policy_paths(context: Record) -> list[str]:
    """Resolve explicitly declared policy paths; a missing job cannot create policy."""
    core = _core()
    paths: set[str] = set()
    for stage in core.sequence(context.get("stages"), "provider stages"):
        for check in core.sequence(
            core.record(stage, "provider stage").get("checks"), "provider checks"
        ):
            selection = core.record(check, "provider check")
            if selection.get("mode") in {"policy_skip", "not_applicable"}:
                paths.update(
                    core.strings(selection.get("policy_evidence"), "provider policy evidence")
                )
    return sorted(paths)


def early_provider_execution(value: Json, reference: Record) -> Record:
    """Project already assessed current lanes without a delivery or provider read."""
    core = _core()
    if value is None:
        return {"status": "not_assessed", "remote_verified_by_tool": False}
    task = core.record(value, "verification task")
    lanes = core.record(task["lanes"], "verification lanes")
    executions: list[Record] = []
    stages: list[Json] = []
    total = 0
    for item in core.sequence(lanes["items"], "verification lanes"):
        lane = core.record(item, "verification lane")
        provider = lane.get("provider_execution")
        if provider is None:
            continue
        execution = core.record(provider, "lane provider execution")
        if execution.get("adopted") is not True:
            continue
        executions.append(execution)
        rows = core.sequence(execution.get("stages", []), "provider stages")
        total += int(execution.get("total", len(rows)))
        for row in rows:
            stages.append(
                {
                    **deepcopy(core.record(row, "provider stage")),
                    "request_id": lane["request_id"],
                    "lane": lane["lane"],
                    "reference": deepcopy(
                        execution.get(
                            "reference",
                            lane.get("observation_reference") or lane["request_reference"],
                        )
                    ),
                    "provider_reference": deepcopy(execution.get("provider_reference")),
                }
            )
    if not executions:
        return {"status": "not_assessed", "remote_verified_by_tool": False}
    statuses = {str(item.get("status", "not_assessed")) for item in executions}
    applicability = {str(item.get("applicability", "unknown")) for item in executions}
    status = next(
        (
            candidate
            for candidate in ("failed", "pending", "unavailable", "not_assessed")
            if candidate in statuses
        ),
        "passed" if statuses == {"passed"} else "not_assessed",
    )
    if lanes["omitted"] and status == "passed":
        status = "not_assessed"
    return {
        "adopted": True,
        "basis": "recorded_verification_lanes",
        "status": status,
        "applicability": next(
            (
                candidate
                for candidate in ("unavailable", "stale", "unknown")
                if candidate in applicability
            ),
            "current",
        ),
        "remote_verified_by_tool": False,
        "reference": deepcopy(reference),
        "stages": stages[:32],
        "total": total,
        "shown": min(32, len(stages)),
        "omitted": total - min(32, len(stages)),
        "omitted_lanes": lanes["omitted"],
    }


def provider_preparation(program: Program, request: Record, command: str) -> Record:
    """Prepare a provider decision from exact caller-captured bytes; never authorize it."""
    ref = coverage.reference(request.get("provider"))
    provider = provider_observation(program, ref)
    core = _core()
    context = core.record(request.get("provider_context"), "provider context")
    policy_refs = [
        {"path": name, "sha256": core.sha(core.bounded_bytes(program.path(name), LIMIT))}
        for name in provider_policy_paths(context)
    ]
    assessor = (
        provider_delivery.assess_premerge
        if command == "premerge-inputs"
        else provider_delivery.assess_cleanup
    )
    result = assessor(provider.get("execution"), context)
    program.verify_refs([ref, *policy_refs])
    return {**result, "provider": ref, "policy_evidence": policy_refs}


def validate_delivery_history(
    program: Program, state: Record, *, verify_artifacts: bool, budget: ReadBudget | None = None
) -> None:
    """Verify optional observations through each immutable task receipt; legacy remains valid."""
    core = _core()
    for task_value in core.sequence(state["tasks"], "tasks"):
        task = core.record(task_value, "task")
        attempts = core.sequence(task["attempts"], "attempts")
        for value in core.sequence(task.get("deliveries", []), "deliveries"):
            delivery = core.record(value, "delivery")
            metadata = core.record(delivery.get("metadata"), "delivery metadata")
            if (
                metadata.get("provider_observation") is None
                and "provider_context" not in metadata
                and metadata.get("cleanup") is None
            ):
                continue
            number = metadata.get("attempt")
            core.require(
                type(number) is int and 0 < number <= len(attempts),
                "Delivery observation attempt mismatch",
            )
            attempt = core.record(attempts[number - 1], "delivery attempt")
            receipt_ref = coverage.reference(delivery.get("receipt"))
            core.require(
                receipt_ref in attempt["evidence"], "Delivery observation lacks frozen task receipt"
            )
            if metadata.get("provider_observation") is not None:
                coverage.reference(metadata["provider_observation"])
            if verify_artifacts:
                program.verify_refs([receipt_ref], budget)
                captured = core.read_json(program.path(str(receipt_ref["path"])), budget=budget)
                core.require(
                    core.canonical(captured) == core.canonical(metadata),
                    "Frozen delivery observation metadata mismatch",
                )
                program.validate_delivery_metadata(task, attempt, metadata, budget=budget)


def delivery_inputs(program: Program, state: Record, request: Record) -> Record:
    core = _core()
    return _delivery_inputs_from_checked(program, state, request, program.validate(state))


def _delivery_inputs_from_checked(
    program: Program, state: Record, request: Record, checked: _ValidatedState
) -> Record:
    """Assemble receipts from one strict initial-state assessment; later guards stay fresh."""
    core = _core()
    core.require(checked.state is state, "Delivery initial-state observation differs")
    ref = coverage.reference(request.get("provider"))
    provider_budget = core.ReadBudget(64 * 1024 * 1024)
    provider = provider_observation(program, ref, provider_budget)
    outputs: list[Json] = []
    bindings = core.sequence(request.get("tasks"), "individual task bindings")
    core.require(bool(bindings), "Delivery authoring requires explicit task bindings")
    seen: set[str] = set()
    for value in bindings:
        binding = core.record(value, "task binding")
        task_id = core.string(binding.get("task_id"), "task ID")
        core.require(task_id not in seen, "Duplicate delivery task binding")
        seen.add(task_id)
        task = program.task(state, task_id)
        core.require(task["status"] == "done", "Delivery metadata requires reviewed completion")
        attempt = program.current_attempt(task)
        submission = core.record(attempt["submission"], "submission")
        core.require(
            binding.get("attempt") == attempt["number"]
            and binding.get("submission_sha256")
            == core.record(submission["receipt"], "receipt")["sha256"]
            and binding.get("reviewed_source_digest") == submission["source_digest"],
            "Delivery input must bind exact individual attempt, submission and reviewed source",
        )
        receipt = {key: deepcopy(provider.get(key)) for key in PROVIDER_FIELDS}
        receipt.update(
            {
                "task_id": task_id,
                "attempt": attempt["number"],
                "reviewed_source_digest": submission["source_digest"],
                "required_ci": deepcopy(binding.get("required_ci")),
                "evidence": deepcopy(binding.get("evidence", [])),
                "provider_observation": deepcopy(ref),
            }
        )
        if "provider_context" in binding:
            receipt["provider_context"] = deepcopy(binding["provider_context"])
        if "cleanup" in binding:
            receipt["cleanup"] = deepcopy(binding["cleanup"])
        status, provider_assessment = program._assess_delivery_metadata(
            task, attempt, receipt, budget=provider_budget
        )
        program.verify_refs(core.sequence(attempt["evidence"], "attempt evidence"))
        program.evidence(
            task_id, int(attempt["number"]), core.strings(receipt["evidence"], "delivery evidence")
        )
        outputs.append(
            {
                "task_id": task_id,
                "receipt": receipt,
                "transition_metadata": "validated",
                "ci_status": status,
                "delivery_accepted": False,
                "remote_verified_by_tool": False,
                "cleanup": deepcopy(receipt.get("cleanup", {"status": "unknown"})),
                "provider_execution": provider_assessment
                if provider_assessment is not None
                else {"status": "not_assessed", "adopted": False, "remote_verified_by_tool": False},
            }
        )
    program.verify_refs([ref])
    return {
        "status": "not_assessed",
        "inputs": outputs,
        "provider": ref,
        "limitations": [
            "Caller-supplied frozen observations; authentication and remote state unverified.",
            "No receipt was published. Actual delivery revalidates its frozen receipt.",
        ],
    }


def recovery_readiness(program: Program, state: Record) -> Record:
    core = _core()
    findings: list[Json] = []
    try:
        program.require_current_location()
        program.validate(state)
    except _errors() as exc:
        findings.append(
            {
                "kind": "authoritative_validation",
                "reason": str(exc),
                "failure_type": type(exc).__name__,
            }
        )
    observed_refs: list[Json] = []
    absent: list[str] = []

    def observe(name: str, limit: int) -> Json:
        try:
            path = program.path(name)
            if not _core().observed_exists(path):
                absent.append(name)
                return None
            raw = core.bounded_bytes(path, limit)
            observed_refs.append({"path": name, "sha256": core.sha(raw)})
            return core.read_json(path, limit=limit, budget=core.ReadBudget(cached={path: raw}))
        except _errors() as exc:
            observation = {
                "status": "unavailable",
                "reason": str(exc),
                "failure_type": type(exc).__name__,
            }
            findings.append({"kind": "unavailable_artifact", "path": name, **observation})
            return observation

    lock_observation = observe(".feature-program.lock", 64 * 1024)
    pending_markers: Record = {}
    for name in sorted(planning.MARKERS | {"RELOCATED.json", "SUCCEEDED.json", "PREDECESSOR.json"}):
        observed = observe(name, LIMIT)
        if observed is not None:
            pending_markers[name] = observed
    # Once structural validation fails, do not invent derived facts from malformed state.
    structural = coverage.diagnostics(
        program, state, argparse.Namespace(operation=None, input=None), observed_head=None
    )["structural_validation"]
    result: Record = {
        "status": "attention" if findings else "observed",
        "findings": findings,
        "lock": lock_observation,
        "markers": pending_markers,
        "liveness": "unknown",
        "lock_stealable": "not_assessed",
        "limitations": [
            "Saved PID/time and contacts do not prove liveness or takeover authority.",
            "First authoritative validation failure only; no repair or completeness claim.",
        ],
        "structural_validation": structural,
    }
    if structural["status"] != "failed":
        for task in state["tasks"]:
            if task["status"] != "review":
                continue
            try:
                program.verify_submission(program.current_attempt(task))
            except _errors() as exc:
                findings.append(
                    {
                        "kind": "stale_submission",
                        "task_id": task["id"],
                        "reason": str(exc),
                        "failure_type": type(exc).__name__,
                    }
                )
        result["status"] = "attention" if findings else "observed"
        result["pending_questions"] = [
            value
            for value in questions.snapshot_questions(state)
            if value["status"] in {"open", "answered", "acknowledged"}
        ]
        result["pending_transfers"] = [
            deepcopy(value)
            for value in state.get("handoffs", [])
            if value.get("acceptance") is None
        ]
        result["ownership"] = program.status(state)
        active = [task for task in state["tasks"] if task["status"] in core.OWNED_STATES]
        result["active_collisions"] = [
            {"left": left["id"], "right": right["id"], "reasons": reasons}
            for index, left in enumerate(active)
            for right in active[index + 1 :]
            if (reasons := program.collisions(left, right))
        ]
        result["coverage"] = coverage.projection(program, state)
    core.require(len(core.canonical(result)) <= LIMIT, "Authoring output exceeds 8 MiB")
    program.verify_refs(observed_refs)
    core.require(
        all(not _core().observed_exists(program.path(name)) for name in absent),
        "Recovery metadata changed during observation; refresh",
    )
    return result


def flow_read(
    program: Program, state: Record, args: argparse.Namespace, refs: list[Json]
) -> Record:
    """Read finite flow inputs; recorded plans and method text remain inert."""
    core = _core()

    def frozen(name: str) -> Record:
        value, captured = core.read_json_frozen(program.path(name), limit=LIMIT)
        refs.append({"path": name, "sha256": captured})
        return value

    program.require_current_location()
    if args.command == "review-queue":
        return core.read_snapshot(program.repo, program.directory)["review_queue"]
    checked_initial = program.validate(state)
    match args.command:
        case "inputs-prepare":
            return core.flow_module("inputs").prepare_manifest(
                args.check_id, args.path, complete=args.complete
            )
        case "inputs-capture":
            producer = core.flow_module("inputs")
            manifest = producer.normalize_manifest(frozen(args.manifest))
            environment, method = frozen(args.environment), frozen(args.method)
            if args.git_revision is not None:
                return producer.capture_git_inputs(
                    program,
                    manifest,
                    revision=args.git_revision,
                    environment=environment,
                    method=method,
                )
            capture = producer.capture_inputs(
                program, manifest, environment=environment, method=method
            )
            refreshed = producer.capture_inputs(
                program, manifest, environment=environment, method=method
            )
            core.require(
                capture["input_digest"] == refreshed["input_digest"],
                "Declared inputs changed during capture; refresh",
            )
            return capture
        case "inputs-compare":
            return core.flow_module("inputs").compare_inputs(
                frozen(args.before), frozen(args.after)
            )
        case "review-queue":
            return core.read_snapshot(program.repo, program.directory)["review_queue"]
        case "review-assignment":
            return core.flow_module("queue").prepare_assignment(program, state, frozen(args.input))
        case "verification-environment":
            live_argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            core.require(bool(live_argv), "Runtime identity requires an explicit executable")
            declared = frozen(args.environment) if args.environment is not None else None
            return core.flow_module("execution").observe_environment(program, live_argv, declared)
        case "verification-observation":
            return core.flow_module("execution").prepare_observation(
                program, state, frozen(args.capture), frozen(args.request)
            )
        case "closure-readiness":
            return core.flow_module("closure")._readiness_from_checked(
                program, state, checked_initial
            )
        case "closure-prepare":
            request = frozen(args.input)
            host = ClosureHost(program, _checked_initial=checked_initial)
            try:
                result = core.flow_module("closure").prepare_inputs(program, state, request, host)
            except _errors() as exc:
                if args.result_path is None:
                    raise
                result = {
                    "schema_version": 1,
                    "kind": "closure_preparation_failure",
                    "status": "failed",
                    "initial_revision": phases.revision(state),
                    "producer": host.identity(),
                    "refs": deepcopy(refs),
                    "failure": {"reason": str(exc), "failure_type": type(exc).__name__},
                    "accepted": False,
                    "remote_verified_by_tool": False,
                }
            if args.result_path is not None:
                name = core.safe_relative(args.result_path)
                valid = name.startswith("evidence/_coordination/attempt-1/") or any(
                    task["attempts"]
                    and name.startswith(
                        f"evidence/{task['id']}/attempt-{task['attempts'][-1]['number']}/"
                    )
                    for task in state["tasks"]
                )
                core.require(
                    valid, "Closure result must be current task/attempt or coordination evidence"
                )
                program.verify_refs(refs)
                core.require(
                    phases.revision(program.load()) == phases.revision(state),
                    "Registry changed during result capture",
                )
                encoded = core.canonical(result)
                core.require(len(encoded) <= LIMIT, "Closure result exceeds 8 MiB")
                path = program.path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                if _core().observed_exists(path):
                    core.require(
                        _core().observed_is_file(path)
                        and core.bounded_bytes(path, LIMIT) == encoded,
                        "Immutable closure result already exists with different bytes",
                    )
                else:
                    with path.open("xb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
            return result
        case _:
            raise core.ProgramError("Unsupported flow read operation")


@dataclass(slots=True)
class _ClosureInspection:
    """Mutable IO observations for one immutable closure inspection, followed by fresh checks."""

    program: Program
    state: Record
    _refs: ReadBudget = field(default_factory=lambda: _core().ReadBudget(64 * 1024 * 1024))
    _views: ReadBudget | None = None
    _absent: set[str] = field(default_factory=set)
    _drift: bool | None = None
    _revision: str | None = None

    def inspect_stage(self, checked: _StageInspection) -> Record:
        core = _core()
        stage = checked.stage
        if checked.status == "committed":
            self.program.verify_refs(
                core.sequence(stage.get("refs", []), "closure stage refs"), self._refs
            )
            if self._drift is None:
                views = self.program.views(self.state)
                self._views = core.ReadBudget(LIMIT * len(views))
                self._drift = False
                for name, text in views.items():
                    path = self.program.path(name)
                    if not _core().observed_is_file(path):
                        self._absent.add(name)
                        self._drift = True
                    else:
                        captured = self._views.capture(self.program.directory, name, LIMIT)
                        self._drift = self._drift or captured.content != text.encode("utf-8")
            return {"status": "committed", "view_recovery_required": self._drift}
        if self._revision is None:
            self._revision = phases.revision(self.state)
        if self._revision == stage.get("before_revision"):
            return {"status": "not_started"}
        return {"status": "conflict", "reason": "Canonical record is not an exact planned stage"}

    def recheck(self) -> None:
        self._refs.recheck(self.program.directory)
        if self._views is not None:
            self._views.recheck(self.program.directory)
        _core().require(
            not any(_core().observed_is_file(self.program.path(name)) for name in self._absent),
            "Unavailable closure view appeared during inspection; refresh",
        )


@dataclass(frozen=True, slots=True)
class ClosureHost:
    """Use the normal guarded publisher for detached closure stages and exact recovery."""

    program: Program
    source_binding: Record | None = None
    frozen_refs: list[Json] = field(default_factory=list)
    identity_budget: ReadBudget | None = None
    publication_base: Record | None = None
    _parser_catalog: dict[str, argparse.ArgumentParser] = field(default_factory=dict, compare=False)
    _checked_initial: _ValidatedState | None = None

    def _validate_initial(self, state: Record) -> _ValidatedState:
        if self._checked_initial is not None and self._checked_initial.state is state:
            return self._checked_initial
        return self.program.validate(state)

    def _inspection_context(self, state: Record, refs: list[Json]) -> _ClosureInspection:
        context = _ClosureInspection(self.program, state)
        self.program.verify_refs(refs, context._refs)
        return context

    def _operation_parser(self, operation: str) -> argparse.ArgumentParser:
        if not self._parser_catalog:
            self._parser_catalog.update(_parsers())
        return deepcopy(self._parser_catalog[operation])

    def revision(self, state: Record) -> str:
        return phases.revision(state)

    def load(self) -> Record:
        return self.program.load()

    def identity(self) -> Record:
        core = _core()
        names = (
            "feature_program.py",
            "feature_program_authoring.py",
            "feature_program_inputs.py",
            "feature_program_execution.py",
            "feature_program_queue.py",
            "feature_program_closure.py",
            "feature_program_verification.py",
            "feature_program_coverage.py",
            "feature_program_coordination.py",
            "feature_program_phases.py",
            "feature_program_planning.py",
            "feature_program_delivery.py",
            "feature_program_review_handoffs.py",
            "feature_program_evidence_reuse.py",
            "feature_program_batch.py",
            "feature_program_timing.py",
            "feature_program_storage.py",
            "feature_program_read_model.py",
        )
        return {
            "schema_version": 1,
            "producer": "feature_program_flow",
            "protocol_version": 1,
            "source_hashes": {
                name: (
                    self.identity_budget.capture(Path(__file__).parent, name, LIMIT).sha256
                    if self.identity_budget is not None
                    else core.sha(core.bounded_bytes(Path(__file__).with_name(name), LIMIT))
                )
                for name in names
            },
        }

    def publisher_authority(self, state: Record, payload: Record, owner: str) -> None:
        """Apply the same current task/director publisher check in preview and publication."""
        task_id = payload.get("id")
        task_owner = (
            self.program.task(state, str(task_id)).get("owner")
            if task_id is not None and any(item["id"] == task_id for item in state["tasks"])
            else None
        )
        verification._task_authority(state, owner, task_owner)

    def prepare(
        self,
        state: Record,
        operation: str,
        payload: Record,
        observed_at: str,
        *,
        owner: str | None = None,
    ) -> PreparedChange:
        core = _core()
        core.require(
            operation in COMMANDS - PUBLICATION_COMMANDS
            and (
                operation in core.flow_module("closure").OPERATIONS
                or operation == "review-handoff-record"
            ),
            "Unsupported closure stage operation",
        )
        parser = self._operation_parser(operation)
        parser.exit_on_error = False
        parser.error = lambda message: (_ for _ in ()).throw(core.ProgramError(message))
        actions = {action.dest: action for action in parser._actions if action.dest != "help"}
        core.require(set(payload) <= set(actions), "Unknown closure stage payload fields")
        core.require(
            "outcome_path" not in payload, "Batch/closure publication uses its own result retention"
        )
        if owner is not None:
            self.publisher_authority(state, payload, owner)
        argv: list[str] = []
        for field, action in actions.items():
            if field not in payload:
                continue
            value = payload[field]
            values = (
                core.sequence(value, field)
                if isinstance(action, argparse._AppendAction)
                else [value]
            )
            for item in values:
                if isinstance(action, argparse._StoreTrueAction):
                    core.require(type(item) is bool, "Closure boolean argument must be explicit")
                    if item:
                        argv.append(action.option_strings[0])
                else:
                    core.require(
                        isinstance(item, (str, int, float)) and not isinstance(item, bool),
                        "Invalid closure argument value",
                    )
                    if action.option_strings:
                        argv.append(action.option_strings[0])
                    argv.append(str(item))
        try:
            args = parser.parse_args(argv)
        except argparse.ArgumentError as exc:
            raise core.ProgramError(str(exc)) from exc
        args.command = operation
        head = self.program.current_head() if operation in HEAD_COMMANDS else None
        dirty = (
            core.git(self.program.repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
            if operation == "checkpoint"
            else ""
        )
        with coordination.preparation_clock(observed_at):
            return prepare(
                self.program,
                state,
                args,
                observed_head=head,
                observed_dirty=dirty,
                publication_base=self.publication_base,
            )

    def preflight(self, prepared: PreparedChange) -> None:
        # Detached predecessor states cannot satisfy a real-registry mutation guard.
        # Semantic preparation and normal save validation still inspect all frozen facts.
        self.program.prepare_save(prepared.state)

    def inspect_stage(self, state: Record, stage: Record) -> Record:
        core = _core()
        events = core.sequence(state.get("events"), "canonical events")
        length = stage.get("event_prefix_length")
        core.require(type(length) is int and length >= 0, "Invalid closure event prefix length")
        appended = core.sequence(stage.get("appended_events"), "closure appended events")
        core.require(
            core.digest(appended) == stage.get("appended_events_sha256"),
            "Closure event digest changed",
        )
        if core.digest(events[:length]) != stage.get("event_prefix_sha256"):
            return {"status": "conflict", "reason": "Canonical predecessor event prefix changed"}
        if events[length : length + len(appended)] == appended and len(events) >= length + len(
            appended
        ):
            self.program.verify_refs(core.sequence(stage.get("refs", []), "closure stage refs"))
            views = self.program.views(state)
            drift = any(
                not _core().observed_is_file(self.program.path(name))
                or core.bounded_bytes(self.program.path(name), LIMIT) != text.encode("utf-8")
                for name, text in views.items()
            )
            return {"status": "committed", "view_recovery_required": drift}
        if phases.revision(state) == stage.get("before_revision"):
            return {"status": "not_started"}
        return {"status": "conflict", "reason": "Canonical record is not an exact planned stage"}

    def publish(self, stage: Record, expected_revision: str, owner: str) -> Record:
        core = _core()
        with self.program.lock():
            current = self.program.load()
            core.require(
                phases.revision(current) == expected_revision == stage.get("before_revision"),
                "Closure current revision changed; reprepare remaining stages",
            )
            core.require(
                self.inspect_stage(current, stage)["status"] == "not_started",
                "Closure stage already committed or conflicting",
            )
            if self.source_binding is not None:
                core.require(
                    self.program.current_head() == self.source_binding.get("source_head")
                    and self.program.source_digest(
                        core.strings(self.source_binding.get("scope"), "closure source scope")
                    )
                    == self.source_binding.get("source_digest"),
                    "Closure source or HEAD changed before publication",
                )
            self.program.verify_refs(self.frozen_refs)
            operation = core.string(stage.get("operation"), "closure operation")
            payload = core.record(stage.get("payload"), "closure payload")
            self.publisher_authority(current, payload, owner)
            self.program.verify_refs(core.sequence(stage.get("refs", []), "closure refs"))
            prepared = self.prepare(
                current,
                operation,
                payload,
                core.string(stage.get("observed_at"), "stage timestamp"),
            )
            _preflight(self.program, prepared)
            core.require(
                phases.revision(prepared.state) == stage.get("after_revision"),
                "Closure stage preparation changed; reprepare remaining stages",
            )
            try:
                self.program.save(prepared.state, before_publish=prepared.before_publish)
            except core.ProgramError as exc:
                committed = core.parse_registry_state(
                    self.program.read_registry_observation(), path=self.program.path("backlog.json")
                )
                if phases.revision(committed) != stage.get("after_revision"):
                    raise
                return {
                    "status": "committed_view_failed",
                    "revision": phases.revision(committed),
                    "reason": str(exc),
                }
            return {"status": "committed", "revision": phases.revision(prepared.state)}


@dataclass(frozen=True, slots=True)
class BatchHost(ClosureHost):
    """Collect mandatory projected checks; mutation is limited to the guard accumulator."""

    stage_specs: list[Record] = field(default_factory=list)
    _publishing: bool = False

    def prepare(
        self,
        state: Record,
        operation: str,
        payload: Record,
        observed_at: str,
        *,
        owner: str | None = None,
    ) -> PreparedChange:
        core = _core()
        core.require(
            self.publication_base is not None, "Batch requires the captured original state"
        )
        if owner is not None and operation in {"verification-record", "review-handoff-record"}:
            control = core.read_json(self.program.path(str(payload["input"])))
            task_id = control.get("task_id") or core.record(
                control.get("subject", {}), "subject"
            ).get("task_id")
            task_owner = self.program.task(state, str(task_id)).get("owner")
            verification._task_authority(state, owner, task_owner)
            prepared = super(BatchHost, self).prepare(state, operation, payload, observed_at)
        else:
            prepared = super(BatchHost, self).prepare(
                state, operation, payload, observed_at, owner=owner
            )
        guard = verification.publication_guard(
            self.program,
            prepared.state,
            predecessor=state,
            publication_base=self.publication_base,
        )
        self.stage_specs.append(
            {
                "operation": operation,
                "payload": deepcopy(payload),
                "observed_at": observed_at,
                "owner": owner,
                "after_revision": phases.revision(prepared.state),
            }
        )
        checks = tuple(item for item in (prepared.before_publish, guard) if item is not None)

        def recheck() -> None:
            for check in checks:
                check()

        return core.PreparedChange(prepared.state, recheck)

    def freeze_control(self, state: Record, operation: Record, document: Record) -> None:
        """Freeze actual supplied facts; derive only their transaction predecessor."""
        core = _core()
        name = core.safe_relative(core.string(document["path"], "control path"))
        prefix = (
            "evidence/_verification/attempt-1/"
            if operation["operation"] == "verification-record"
            else "evidence/"
        )
        core.require(name.startswith(prefix), "Control uses the wrong evidence namespace")
        value = deepcopy(core.record(document["value"], "actual control facts"))
        revision = phases.revision(state)
        core.require(
            value.get("expected_revision") in {None, revision},
            "Explicit control predecessor differs",
        )
        value["expected_revision"] = revision
        freeze_document(self.program, name, value)

    def preflight_batch(
        self, original: Record, final: Record, stages: list[Record]
    ) -> PreparedChange:
        core = _core()
        core.require(
            self.publication_base is not None
            and phases.revision(original) == phases.revision(self.publication_base),
            "Batch original state mismatch",
        )
        core.require(bool(stages), "Batch has no stages")
        if not self._publishing:
            self.program.prepare_save(final)
        # Locked publication always performs this same final save preflight in _publish.
        expected = phases.revision(original)
        specs = tuple(deepcopy(self.stage_specs))

        def recheck() -> None:
            self.program.require_current_location()
            current = core.parse_registry_state(
                self.program.read_registry_observation(), path=self.program.path("backlog.json")
            )
            core.require(phases.revision(current) == expected, "Batch original revision changed")
            self.program.verify_refs(self.frozen_refs)
            if self.source_binding is not None:
                core.require(
                    self.program.current_head() == self.source_binding.get("source_head")
                    and self.program.source_digest(
                        core.strings(self.source_binding.get("scope"), "batch scope")
                    )
                    == self.source_binding.get("source_digest"),
                    "Batch source or HEAD changed before publication",
                )
            # Reconstruct one predecessor at a time instead of retaining callbacks
            # that close over N complete intermediate registries.
            proposal = original
            self.stage_specs.clear()
            observations = core._SourceObservations()
            token = core._source_observations.set(observations)
            try:
                for spec in specs:
                    prepared = self.prepare(
                        proposal,
                        str(spec["operation"]),
                        core.record(spec["payload"], "stage payload"),
                        str(spec["observed_at"]),
                        owner=spec["owner"],
                    )
                    core.require(
                        phases.revision(prepared.state) == spec["after_revision"],
                        "Projected callback predecessor changed",
                    )
                    if prepared.before_publish is not None:
                        prepared.before_publish()
                    proposal = prepared.state
                observations.verify()
            finally:
                core._source_observations.reset(token)
            reader = core.ReadBudget(64 * 1024 * 1024)
            refs = list(coordination.evidence_references(final)) + verification.evidence_references(
                final
            )
            for task in core.sequence(final["tasks"], "tasks"):
                for attempt in core.sequence(core.record(task, "task")["attempts"], "attempts"):
                    refs += core.sequence(core.record(attempt, "attempt")["evidence"], "evidence")
            if "review_handoffs" in final:
                refs += core.flow_module("review_handoffs").evidence_references(final)
            refs += core.flow_module("batch").evidence_references(final)
            self.program.verify_refs(refs, reader)
            core.verify_provenance(self.program, final, reader)
            core.require(
                phases.revision(
                    core.parse_registry_state(
                        self.program.read_registry_observation(),
                        path=self.program.path("backlog.json"),
                    )
                )
                == expected,
                "Batch original revision changed during final checks",
            )

        return core.PreparedChange(final, recheck)

    def prepare_stage(
        self,
        state: Record,
        operation: str,
        payload: Record,
        observed_at: str,
        *,
        owner: str | None = None,
    ) -> tuple[PreparedChange, str]:
        prepared = self.prepare(state, operation, payload, observed_at, owner=owner)
        return prepared, str(self.stage_specs[-1]["after_revision"])

    def inspect_views(self, state: Record) -> bool:
        core = _core()
        return all(
            _core().observed_is_file(self.program.path(name))
            and core.bounded_bytes(self.program.path(name), LIMIT) == value.encode("utf-8")
            for name, value in self.program.views(state).items()
        )

    def publish_batch(self, plan: Record, owner: str, expected_revision: str) -> Record:
        core = _core()
        return self._publish_checked_batch(
            core.flow_module("batch")._check_plan(plan), owner, expected_revision
        )

    def _publish_checked_batch(
        self, checked: _CheckedPlan, owner: str, expected_revision: str
    ) -> Record:
        core = _core()
        producer = core.flow_module("batch")
        plan = checked.value
        with self.program.lock():
            current = self.program.load()
            inspection = producer._inspect_checked(self.program, current, checked, self)
            if inspection["status"] in {"already_committed", "conflict"}:
                return inspection
            core.require(
                phases.revision(current) == expected_revision == plan["base_revision"],
                "Batch original revision changed; reprepare",
            )
            host = BatchHost(
                self.program,
                source_binding=core.record(plan["source_binding"], "batch source"),
                frozen_refs=core.sequence(plan["refs"], "batch refs"),
                publication_base=current,
                _publishing=True,
            )
            prepared = producer._reconstruct_checked(self.program, current, checked, host)
            core.require(
                phases.revision(prepared.state) == plan["final_revision"],
                "Batch reconstruction changed",
            )
            try:
                self.program._publish(prepared.state, prepared.before_publish)
            except core.ProgramError as exc:
                actual = core.parse_registry_state(
                    self.program.read_registry_observation(), path=self.program.path("backlog.json")
                )
                if phases.revision(actual) != plan["final_revision"]:
                    raise
                return producer.result_for(
                    plan,
                    "committed_view_failed",
                    phases.revision(actual),
                    view_recovery_required=True,
                    reason_code="render_required",
                )
            return producer.result_for(plan, "committed", phases.revision(prepared.state))


def freeze_document(program: Program, name: str, document: Record) -> Record:
    """Materialize inert exact immutable bytes; this never publishes canonical state."""
    core = _core()
    name = core.safe_relative(name)
    core.require(name.startswith("evidence/"), "Frozen document must be evidence")
    encoded = core.canonical(document)
    core.require(0 < len(encoded) <= LIMIT, "Frozen document exceeds budget")
    path = program.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _core().observed_exists(path):
        core.require(
            _core().observed_is_file(path) and core.bounded_bytes(path, LIMIT) == encoded,
            "Immutable document already exists with different bytes",
        )
    else:
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    return {"path": name, "sha256": core.sha(encoded)}


def assess_review_inputs(
    program: Program,
    value: Json,
    *,
    source_budget: ReadBudget | None = None,
    evidence_budget: ReadBudget | None = None,
) -> Record:
    """Bind explicitly declared broader source bytes and frozen refs through existing readers."""
    core = _core()
    inputs = deepcopy(core.record(value, "review inputs"))
    scope = sorted(set(core.strings(inputs.get("scope", []), "review input scope")))
    refs = core.sequence(inputs.get("evidence_refs", []), "review input evidence refs")
    for value in refs:
        verification._ref(core.record(value, "review input reference"))
    program.verify_refs(refs, evidence_budget)
    inputs.pop("source_binding", None)
    if scope:
        inputs["source_binding"] = {
            "scope": scope,
            "source_digest": core.digest(program.source_snapshot(scope, source_budget)),
        }
    return inputs


def verify_review_input_refs(program: Program, state: Record, budget: ReadBudget) -> None:
    """Retain only explicit typed packet evidence references, never sniff arbitrary JSON."""
    core = _core()
    records = core.sequence(
        core.record(state.get("review_handoffs", {}), "handoffs").get("records", []), "records"
    )
    checked: set[str] = set()
    for value in records:
        item = core.record(value, "handoff")
        if item["kind"] != "intent":
            continue
        ref = core.record(item["packet_ref"], "packet reference")
        key = core.digest(ref)
        if key in checked:
            continue
        program.verify_refs([ref], budget)
        packet = core.read_json(program.path(str(ref["path"])), budget=budget)
        refs = core.sequence(
            core.record(packet["review_inputs"], "review inputs").get("evidence_refs", []),
            "review input refs",
        )
        for frozen in refs:
            verification._ref(core.record(frozen, "review input ref"))
        program.verify_refs(refs, budget)
        checked.add(key)


def recheck_new_handoff_inputs(program: Program, original: Record | None, state: Record) -> None:
    """Keep new current handoffs bound to their immutable packet through final publication."""
    core = _core()
    prior = core.sequence(
        core.record((original or {}).get("review_handoffs", {}), "handoffs").get("records", []),
        "records",
    )
    records = core.sequence(
        core.record(state.get("review_handoffs", {}), "handoffs").get("records", []), "records"
    )
    changed = [
        core.record(value, "handoff")
        for value in records[len(prior) :]
        if core.record(value, "handoff")["kind"] != "terminal"
        and core.record(core.record(value, "handoff").get("details", {}), "details").get(
            "recording_mode"
        )
        != "historical"
    ]
    if not changed:
        return
    reader = core.ReadBudget(64 * 1024 * 1024)
    sources = core.ReadBudget(128 * 1024 * 1024)
    observations = core._SourceObservations()
    token = core._source_observations.set(observations)
    try:
        checked: set[str] = set()
        for item in changed:
            ref = core.record(item["packet_ref"], "packet reference")
            key = core.digest(ref)
            if key in checked:
                continue
            program.verify_refs([ref], reader)
            packet = core.read_json(program.path(str(ref["path"])), budget=reader)
            core.require(
                assess_review_inputs(
                    program, packet["review_inputs"], source_budget=sources, evidence_budget=reader
                )
                == packet["review_inputs"],
                "Broader review inputs changed before publication",
            )
            checked.add(key)
        observations.verify()
    finally:
        core._source_observations.reset(token)


def handoff_inputs(program: Program, state: Record, request: Record) -> Record:
    """Assess current native-handoff subject; recorded staffing never proves capacity."""
    core = _core()
    task_id = core.string(request.get("task_id"), "handoff task")
    context = verification.packet(program, state, task_id)["context"]
    task = program.task(state, task_id)
    actor = request.get("actor_id", task.get("owner"))
    coordination.require_task_authority(
        state, core.string(actor, "handoff actor"), task.get("owner")
    )
    reviewer = request.get("reviewer_id")
    # The domain crosschecks actual actor records and independence. Host never promotes a role label.
    registered = any(
        core.record(value, "actor").get("id") == reviewer
        and core.record(value, "actor").get("staffing_status") != "retired"
        for value in core.sequence(state.get("actors", []), "actors")
    )
    eligible = (
        registered
        and reviewer is not None
        and reviewer
        not in {
            coordination.current_director_id(state),
            *[attempt.get("owner") for attempt in core.sequence(task["attempts"], "attempts")],
        }
    )
    return {
        **deepcopy(request),
        "task_id": task_id,
        "actor_id": actor,
        "context": context,
        "review_inputs": assess_review_inputs(program, request.get("review_inputs", {})),
        "eligibility": {
            "status": "eligible" if eligible else "not_assessed",
            "reasons": [] if eligible else ["independent_reviewer_not_assessed"],
        },
        "recorded_at": core.utc(),
        "evidence_refs": deepcopy(request.get("evidence_refs", [])),
    }


def prepare_submission_handoff(
    program: Program,
    state: Record,
    args: argparse.Namespace,
    *,
    materialize: bool = False,
) -> Record:
    core = _core()
    request, captured = core.read_json_frozen(program.path(args.handoff), limit=LIMIT)
    core.require(
        set(request) <= {"reviewer_id", "review_inputs", "evidence_refs"},
        "Unknown submission handoff fields",
    )
    request.update(
        {
            "task_id": args.id,
            "actor_id": args.owner,
            "receipt_ref": {"path": args.handoff, "sha256": captured},
        }
    )
    result = core.flow_module("review_handoffs").prepare_handoff(
        state,
        handoff_inputs(program, state, request),
    )
    if materialize:
        ref = freeze_document(program, str(result["packet_ref"]["path"]), result["packet_document"])
        core.require(ref == result["packet_ref"], "Handoff packet digest mismatch")
    return result


def assess_evidence_receipt(
    program: Program,
    state: Record,
    operation: str,
    receipt: Record,
    *,
    owner: str | None,
) -> Record:
    """Freeze inert bytes, then ask the existing authoritative ordinary validator."""
    core = _core()
    task_id = receipt.get("task_id")
    prefix = (
        f"evidence/{task_id}/attempt-{receipt.get('attempt')}/"
        if task_id is not None
        else "evidence/_coordination/attempt-1/"
    )
    path = prefix + "reuse-" + core.digest(receipt) + ".json"
    freeze_document(program, path, receipt)
    try:
        match operation:
            case "submission":
                payload = {"id": task_id, "owner": owner, "receipt": path}
                command = "submit"
            case "review":
                payload = {"id": task_id, "review": path}
                command = "complete" if receipt.get("verdict") == "accepted" else "rework"
            case "completion":
                payload, command = {"input": path}, "objective-complete"
            case "delivery":
                payload, command = {"id": task_id, "receipt": path}, "delivery"
            case "observation":
                path = "evidence/_verification/attempt-1/reuse-" + core.digest(receipt) + ".json"
                freeze_document(program, path, receipt)
                payload, command = {"input": path}, "verification-record"
            case "integrated_review":
                goal = core.record(state.get("objective"), "objective")
                scaffolded = scaffold(
                    program,
                    state,
                    argparse.Namespace(
                        operation="objective-complete",
                        kind=None,
                        task=None,
                        scope=receipt["scope"],
                    ),
                )
                completion = core.record(scaffolded["receipt"], "completion")
                completion.update(
                    {"actor_id": coordination.current_director_id(state), "integrated_review": path}
                )
                implementers = {
                    str(core.record(attempt, "attempt")["owner"])
                    for task in core.sequence(state["tasks"], "tasks")
                    for attempt in core.sequence(core.record(task, "task")["attempts"], "attempts")
                }
                coordination._integrated_review(
                    program, state, goal, completion, implementers, program.current_head()
                )
                return {"status": "ready", "missing_fields": [], "reasons": []}
            case _:
                raise core.ProgramError("Unsupported evidence receipt operation")
        prepared = ClosureHost(program).prepare(state, command, payload, core.utc())
        _preflight(program, prepared)
    except _errors() as exc:
        return {"status": "incomplete", "missing_fields": [], "reasons": [str(exc)]}
    return {"status": "ready", "missing_fields": [], "reasons": []}


@dataclass(frozen=True, slots=True)
class EvidenceReceiptAdapter:
    """Expose the existing receipt assessor through the domain's narrow protocol."""

    def assess(
        self, program: Program, state: Record, operation: str, receipt: Record, *, owner: str | None
    ) -> Record:
        return assess_evidence_receipt(program, state, operation, receipt, owner=owner)


def assess_handoffs(
    program: Program, state: Record, *, source_budget: ReadBudget, evidence_budget: ReadBudget
) -> Record:
    """Assess complete current handoff inputs within the caller's existing read epochs."""
    core = _core()
    assessed: Record = {}
    context_reads = verification._reads(evidence_budget)
    for value in core.sequence(
        core.record(state.get("review_handoffs", {}), "review handoffs").get("records", []),
        "handoff records",
    ):
        item = core.record(value, "handoff record")
        if item["kind"] != "intent":
            continue
        subject = core.record(item["subject"], "subject")
        try:
            context = verification._context(program, state, str(subject["task_id"]), context_reads)
            packet_ref = core.record(item["packet_ref"], "packet ref")
            program.verify_refs([packet_ref], evidence_budget)
            packet = core.read_json(program.path(str(packet_ref["path"])), budget=evidence_budget)
            assessed[str(item["handoff_id"])] = {
                "context": context,
                "review_inputs": assess_review_inputs(
                    program,
                    packet.get("review_inputs", {}),
                    source_budget=source_budget,
                    evidence_budget=evidence_budget,
                ),
                "evidence_status": "observed",
            }
        except (core.ProgramError, OSError):
            assessed[str(item["handoff_id"])] = {"context": None, "evidence_status": "unavailable"}
    return core.flow_module("review_handoffs").project_handoffs(state, assessed, limit=None)


def _handoff_read(
    program: Program, state: Record, command: str, request: Record, refs: list[Json]
) -> Record:
    core = _core()
    queue = core.flow_module("queue")
    handoffs = core.flow_module("review_handoffs")
    sources, evidence = core.ReadBudget(64 * LIMIT), core.ReadBudget(64 * LIMIT)
    head = program.current_head()
    if command in {"review-capacity", "reviewer-allocation"}:
        core.require(set(request) <= {"policy", "runtime_facts"}, "Unknown capacity request fields")
        runtime = request.get("runtime_facts")
        if runtime is not None:
            for actor in core.record(
                core.record(runtime, "runtime facts").get("actors"), "runtime actors"
            ).values():
                refs.extend(
                    core.sequence(
                        core.record(actor, "runtime actor").get("evidence_refs"), "runtime evidence"
                    )
                )
        full = assess_handoffs(program, state, source_budget=sources, evidence_budget=evidence)
        verification_facts = verification.projection(program, state, budget=evidence, limit=None)
        if command == "review-capacity":
            result = queue.project_capacity(
                state, full, verification_facts, policy=request.get("policy"), runtime_facts=runtime
            )
        else:
            result = queue.prepare_reviewer_allocation(
                state, full, verification_facts, policy=request.get("policy"), runtime_facts=runtime
            )
    elif command == "handoff-action":
        core.require(
            set(request) <= {"request", "runtime_contact"}, "Unknown action preparation fields"
        )
        action = core.record(request.get("request"), "handoff action request")
        task_id = core.string(action.get("task_id"), "handoff action task")
        packet_ref = core.record(action.get("packet_ref"), "exact task packet reference")
        program.verify_refs([packet_ref], evidence)
        refs.append(packet_ref)
        if action.get("kind") == "worker_assignment":
            core.require(
                packet_ref["path"] == f"tasks/{task_id}.md",
                "Worker action needs its ordinary task packet",
            )
            captured = evidence.capture(program.directory, str(packet_ref["path"]), LIMIT)
            core.require(
                captured.content == program.packet(state, program.task(state, task_id)).encode(),
                "Worker task packet is stale; recover derived views",
            )
            review_inputs = {}
        else:
            packet = core.read_json(program.path(str(packet_ref["path"])), budget=evidence)
            review_inputs = assess_review_inputs(
                program,
                packet.get("review_inputs", {}),
                source_budget=sources,
                evidence_budget=evidence,
            )
        assessed = {
            "context": verification.packet(program, state, task_id, observed_head=head)["context"],
            "packet_ref": packet_ref,
            "review_inputs": review_inputs,
        }
        contact = request.get("runtime_contact")
        if contact is not None:
            refs.extend(
                core.sequence(
                    core.record(contact, "runtime contact").get("evidence_refs"),
                    "runtime contact evidence",
                )
            )
        result = queue.prepare_handoff_action(
            state, action, assessed_packet=assessed, runtime_contact=contact
        )
    else:
        core.require(
            set(request) == {"action_ref", "report_ref"},
            "Report preparation needs exact action/report references",
        )
        documents = []
        for field in ("action_ref", "report_ref"):
            ref = core.record(request[field], field)
            program.verify_refs([ref], evidence)
            refs.append(ref)
            documents.append(core.read_json(program.path(str(ref["path"])), budget=evidence))
        result = handoffs.prepare_report_records(state, documents[0], documents[1])
        refs.extend(core.sequence(documents[1].get("evidence_refs"), "actual report evidence"))
    program.verify_refs(refs, evidence)
    sources.recheck(program.repo)
    evidence.recheck(program.directory)
    core.require(program.current_head() == head, "HEAD changed during handoff preparation")
    return result


def throughput_read(
    program: Program, state: Record, args: argparse.Namespace, refs: list[Json]
) -> Record:
    """Finite inert preparations and actual retained results, never captured-command execution."""
    core = _core()
    program.require_current_location()
    program.validate(state)
    name = args.plan if args.command == "batch-inspect" else args.input
    request, captured = core.read_json_frozen(program.path(name), limit=LIMIT)
    request_ref = {"path": name, "sha256": captured}
    refs.append(request_ref)
    match args.command:
        case (
            "handoff-action" | "handoff-report-prepare" | "review-capacity" | "reviewer-allocation"
        ):
            result = _handoff_read(program, state, args.command, request, refs)
        case "review-handoff-prepare":
            result = core.flow_module("review_handoffs").prepare_handoff(
                state,
                handoff_inputs(program, state, request),
            )
            if args.materialize:
                freeze_document(
                    program, str(result["packet_ref"]["path"]), result["packet_document"]
                )
            result = {key: value for key, value in result.items() if key != "state"}
        case "evidence-prepare":
            producer = core.flow_module("evidence_reuse")
            match request.get("kind"):
                case "plan":
                    result = producer.plan_evidence(
                        program, state, core.record(request.get("selection"), "selection")
                    )
                case "materialize":
                    result = producer.materialize_evidence(
                        program, state, core.record(request.get("plan"), "plan")
                    )
                case "receipt":
                    result = producer.prepare_receipt(
                        program,
                        state,
                        core.string(request.get("operation"), "receipt operation"),
                        core.record(request.get("explicit_inputs"), "explicit inputs"),
                        adapter=EvidenceReceiptAdapter(),
                    )
                case _:
                    raise core.ProgramError("Unsupported evidence preparation kind")
        case "batch-prepare" | "records-prepare":
            try:
                prepare_batch = (
                    core.flow_module("batch").prepare_records
                    if args.command == "records-prepare"
                    else core.flow_module("batch").prepare_batch
                )
                result = prepare_batch(
                    program,
                    state,
                    request,
                    BatchHost(program, publication_base=state),
                )
            except _errors() as exc:
                if args.result_path is None:
                    raise
                actual = core.parse_registry_state(
                    program.read_registry_observation(), path=program.path("backlog.json")
                )
                result = {
                    "schema_version": 1,
                    "kind": "batch_preparation_failure",
                    "request_ref": request_ref,
                    "program_id": state["program_id"],
                    "base_revision": phases.revision(state),
                    "failure": {"reason": str(exc), "failure_type": type(exc).__name__},
                    "preparation": {"status": "failed"},
                    "publication": {
                        "status": "not_committed"
                        if phases.revision(actual) == phases.revision(state)
                        else "unknown"
                    },
                    "accepted": False,
                }
        case "batch-inspect":
            result = core.flow_module("batch").project_selection(
                program, state, request, BatchHost(program)
            )
        case _:
            raise core.ProgramError("Unsupported throughput read")
    if getattr(args, "result_path", None) is not None:
        core.require(
            phases.revision(program.load()) == phases.revision(state),
            "Registry changed during result retention",
        )
        freeze_document(program, args.result_path, result)
    return result


def publish_batch_command(program: Program, args: argparse.Namespace) -> Record:
    """Publish an exact plan and optionally retain the actual outcome without replay."""
    core = _core()
    plan, captured = core.read_json_frozen(program.path(args.plan), limit=LIMIT)
    ref = {"path": args.plan, "sha256": captured}
    try:
        outcome = core.flow_module("batch").publish_batch(
            program,
            plan,
            BatchHost(program),
            args.owner,
            args.expected_revision,
        )
    except _errors() as exc:
        if args.result_path is None:
            raise
        actual = core.parse_registry_state(
            program.read_registry_observation(), path=program.path("backlog.json")
        )
        try:
            inspection = core.flow_module("batch").inspect_batch(
                program, actual, plan, BatchHost(program)
            )
            if inspection["status"] == "already_committed":
                status = (
                    "committed_view_failed"
                    if inspection.get("view_recovery_required")
                    else "already_committed"
                )
            else:
                status = "not_committed" if inspection["status"] == "not_started" else "unknown"
        except _errors():
            inspection = {}
            status = "unknown"
        outcome = core.flow_module("batch").result_for(
            plan,
            status,
            phases.revision(actual),
            view_recovery_required=bool(inspection.get("view_recovery_required")),
            reason_code="publication_failed",
        )
        outcome["failure"] = {"reason": str(exc), "failure_type": type(exc).__name__}
    program.verify_refs([ref])
    if args.result_path is not None:
        result = {
            "schema_version": 1,
            "kind": "batch_publication_result",
            "plan_ref": ref,
            "outcome": outcome,
        }
        freeze_document(program, args.result_path, result)
        return result
    return outcome


def prepare_handoff_record(
    program: Program,
    state: Record,
    name: str,
    *,
    materialize: bool = False,
) -> Record:
    core = _core()
    request, captured = core.read_json_frozen(program.path(name), limit=LIMIT)
    request["receipt_ref"] = {"path": name, "sha256": captured}
    request["recorded_at"] = core.utc()
    if request.get("kind") == "intent":
        core.require(
            request.get("schema_version") == 1
            and request.get("operation") == "review-handoff-record",
            "Invalid intent operation",
        )
        result = core.flow_module("review_handoffs").prepare_handoff(
            state, handoff_inputs(program, state, request)
        )
        proposal = core.record(result["state"], "proposed intent state")
        if result["status"] == "prepared":
            core.require(
                request.get("expected_revision") == phases.revision(state),
                "Stale handoff registry revision",
            )
            if materialize:
                freeze_document(
                    program, str(result["packet_ref"]["path"]), result["packet_document"]
                )
            program.event(
                proposal,
                "review-handoff-record",
                {
                    "id": request["task_id"],
                    "actor": request["actor_id"],
                    "receipt": request["receipt_ref"],
                    "handoff_id": result["handoff_id"],
                },
            )
        return proposal
    subject = core.record(request.get("subject"), "handoff subject")
    task_id = core.string(subject.get("task_id"), "handoff task")
    historical = request.get("recording_mode") == "historical"
    if not historical:
        request["context"] = verification.packet(program, state, task_id)["context"]
    packet_ref = core.record(request.get("packet_ref"), "packet reference")
    program.verify_refs([packet_ref])
    packet = core.read_json(program.path(str(packet_ref["path"])))
    if "review_inputs" in request:
        core.require(
            request["review_inputs"] == packet["review_inputs"],
            "Handoff broader review inputs changed",
        )
    if historical:
        original_inputs = core.record(packet["review_inputs"], "original review inputs")
        program.verify_refs(
            core.sequence(original_inputs.get("evidence_refs", []), "original review evidence")
        )
        request["review_inputs"] = deepcopy(original_inputs)
    else:
        request["review_inputs"] = assess_review_inputs(program, packet["review_inputs"])
    producer = core.flow_module("review_handoffs")
    prepare_observation = (
        producer.prepare_dispatch_claim
        if request.get("kind") == "dispatch_claimed"
        else producer.prepare_observation
    )
    result = prepare_observation(state, request)
    proposal = core.record(result["state"], "proposed handoff state")
    if result["status"] == "prepared":
        core.require(
            request.get("expected_revision") == phases.revision(state),
            "Stale handoff registry revision",
        )
        program.event(
            proposal,
            "review-handoff-record",
            {
                "id": task_id,
                "actor": request["actor_id"],
                "receipt": request["receipt_ref"],
                "handoff_id": request["handoff_id"],
            },
        )
    return proposal


def resume_closure(program: Program, args: argparse.Namespace) -> Record:
    """Resume exact immutable record stages without invoking any captured command."""
    core = _core()
    plan, captured = core.read_json_frozen(program.path(args.plan), limit=LIMIT)
    program.verify_refs([{"path": args.plan, "sha256": captured}])
    result = core.flow_module("closure").resume_plan(
        program,
        plan,
        ClosureHost(
            program,
            core.record(plan.get("source_binding"), "closure source binding"),
            [
                *core.sequence(plan.get("refs", []), "closure refs"),
                {"path": args.plan, "sha256": captured},
            ],
        ),
        args.owner,
        args.expected_revision,
    )
    core.require(len(core.canonical(result)) <= LIMIT, "Closure result exceeds 8 MiB")
    return result


def run_verification(program: Program, args: argparse.Namespace) -> Record:
    """Guard one explicit live invocation, release the lock, then capture actual execution."""
    core = _core()
    execution = core.flow_module("execution")
    live_argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    core.require(
        getattr(args, "live_delimited", False) and bool(live_argv),
        "verification-run requires explicit live argv after --",
    )
    with program.lock():
        state = program.load()
        binding, captured = core.read_json_frozen(program.path(args.request), limit=LIMIT)
        core.require(
            binding.get("task_id") == args.id
            and binding.get("actor_id") == args.owner
            and binding.get("expected_revision") == args.expected_revision
            and phases.revision(state) == args.expected_revision,
            "Run task, actor or current revision does not match explicit binding",
        )
        program.verify_refs([{"path": args.request, "sha256": captured}])
        invocation = {
            "argv": live_argv,
            "cwd": args.cwd,
            "execution_kind": args.execution_kind,
            "timeout_seconds": args.timeout_seconds,
            "stream_limit_bytes": args.stream_limit_bytes,
            "capture_dir": args.capture_dir,
            "artifacts": args.artifact,
        }
        prepared = execution.prepare_run(program, state, invocation, binding)
        program.verify_refs([{"path": args.request, "sha256": captured}])
        core.require(
            phases.revision(program.load()) == args.expected_revision,
            "Registry changed before explicit execution",
        )
    # The producer alone executes live argv. No canonical lock survives this boundary.
    result = execution.execute_run(prepared)
    result["publication"] = "pending"
    result["acceptance_complete"] = False
    try:
        current = program.load()
        result["observation"] = execution.prepare_observation(
            program, current, result["capture_ref"], binding
        )
        result["observation_status"] = "prepared"
    except _errors() as exc:
        result["observation_status"] = "unavailable"
        result["observation_reasons"] = [str(exc)]
    core.require(len(core.canonical(result)) <= LIMIT, "Execution result exceeds 8 MiB")
    return result


def read_command(program: Program, args: argparse.Namespace) -> Record:
    core = _core()
    if args.command == "authoring-support":
        return support()
    if args.command == "review-queue":
        return core.read_snapshot(program.repo, program.directory)["review_queue"]
    initial = program.read_registry_observation()
    state = core.parse_registry_state(initial, path=program.path("backlog.json"))
    refs: list[Json] = []
    observed_head: str | None = None
    if args.command in {
        "review-handoff-prepare",
        "evidence-prepare",
        "batch-prepare",
        "batch-inspect",
        "records-prepare",
        "handoff-action",
        "handoff-report-prepare",
        "review-capacity",
        "reviewer-allocation",
    }:
        result = throughput_read(program, state, args, refs)
    elif args.command in {
        "inputs-prepare",
        "inputs-capture",
        "inputs-compare",
        "review-queue",
        "review-assignment",
        "verification-observation",
        "verification-environment",
        "closure-readiness",
        "closure-prepare",
    }:
        result = flow_read(program, state, args, refs)
    elif args.command == "preview":
        command, ref = _request(program, args.request)
        refs.append(ref)
        result = preview(program, state, command)
        result["input"] = ref
    elif args.command == "recovery-readiness":
        result = recovery_readiness(program, state)
    else:
        program.require_current_location()
        if args.command == "scaffold":
            result = scaffold(program, state, args)
        elif args.command == "verification-packet":
            program.validate(state)
            observed_head = program.current_head()
            result = verification.packet(program, state, args.id, observed_head=observed_head)
        else:
            request, captured = core.read_json_frozen(program.path(args.input), limit=LIMIT)
            refs.append({"path": args.input, "sha256": captured})
            if args.command == "submission-inputs":
                program.validate(state)
                result = verification.submission_inputs(program, state, request)
            elif args.command in {"premerge-inputs", "cleanup-inputs"}:
                program.validate(state)
                result = provider_preparation(program, request, args.command)
            else:
                result = delivery_inputs(program, state, request)
    core.require(len(core.canonical(result)) <= LIMIT, "Authoring output exceeds 8 MiB")
    if args.command == "scaffold":
        core.require(
            core.canonical(scaffold(program, state, args)) == core.canonical(result),
            "Scaffold inputs changed during observation; refresh",
        )
    program.verify_refs(refs)
    if observed_head is not None:
        core.require(
            program.current_head() == observed_head, "HEAD changed during packet preparation"
        )
    core.require(
        program.read_registry_observation().raw_bytes == initial.raw_bytes,
        "Registry changed during authoring observation; refresh",
    )
    return result
