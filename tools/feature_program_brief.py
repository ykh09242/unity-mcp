"""Bounded advisory views of one captured program observation.

This module performs no filesystem, Git, clock, lock or mutation operations. The
caller owns observation integrity and budgets; this module owns presentation bounds.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.feature_program import Program, Record

PROGRAM_BYTES = 32768
TASK_BYTES = 16384
ATTENTION_ROWS = 50
PROSE_POINTS = 512


def _core():
    if __package__:
        from tools import feature_program
    else:
        import feature_program
    return feature_program


def _throughput_brief(value: dict | None, family: str) -> dict | None:
    """Keep concise recorded facts and full denominators within the recovery byte budget."""
    if value is None:
        return None
    result = {
        key: deepcopy(value.get(key))
        for key in (
            "schema_version",
            "status",
            "counts",
            "coverage",
            "preparation",
            "publication",
            "applicability",
            "next_actor_id",
            "next_action",
            "projection_scope",
            "program_coverage",
        )
        if key in value
    }
    rows = value.get(family)
    if isinstance(rows, dict):
        items = rows.get("items", [])[:5]
        result[family] = {
            "total": rows["total"],
            "shown": len(items),
            "omitted": rows["total"] - len(items) if rows["total"] is not None else None,
            "items": [
                {
                    key: deepcopy(item.get(key))
                    for key in (
                        "id",
                        "task_id",
                        "attempt",
                        "submission_round",
                        "phase",
                        "state",
                        "currentness",
                        "kind",
                        "round",
                        "canonical_phase",
                        "observation_phase",
                        "applicability",
                        "waiting_since",
                        "age",
                        "interval_ids",
                        "interval_id",
                        "start",
                        "end",
                        "boundary_label",
                        "closed_seconds",
                        "reasons",
                        "references",
                        "cause",
                        "resolver_id",
                        "next_actor_id",
                        "next_action",
                        "next_trigger",
                        "reference",
                    )
                    if key in item
                }
                for item in items
            ],
            "reference": deepcopy(
                rows.get("reference", {"path": "backlog.json", "pointer": "/" + family})
            ),
        }
    if family == "current" and "intervals" in value:
        result["intervals"] = _throughput_brief(value, "intervals")["intervals"]
    return result


def _task_timing(value: dict | None, task_id: str) -> dict | None:
    """Keep task facts from the same bounded observation without inferring a task denominator."""
    if value is None:
        return None
    selected = {
        "schema_version": value["schema_version"],
        "projection_scope": "task_subset_of_bounded_program_projection",
        "program_coverage": deepcopy(value["coverage"]),
    }
    for family in ("current", "intervals"):
        rows = value[family]
        items = [deepcopy(item) for item in rows["items"] if item.get("task_id") == task_id]
        total = len(items) if rows["omitted"] == 0 else None
        selected[family] = {
            "items": items,
            "total": total,
            "shown": len(items),
            "omitted": 0 if total is not None else None,
        }
    actual_boundary = any(
        item.get("waiting_since") is not None or item.get("age") is not None
        for item in selected["current"]["items"]
    ) or any(
        item.get("closed_seconds") is not None or item.get("state") == "open"
        for item in selected["intervals"]["items"]
    )
    if not actual_boundary and all(
        value[family]["omitted"] == 0 for family in ("current", "intervals")
    ):
        return None
    return _throughput_brief(selected, "current")


def collection(items: list, reference: dict, limit: int = ATTENTION_ROWS) -> dict:
    """Counts describe the complete inspected collection, including zero shown."""
    return {
        "total": len(items),
        "shown": min(limit, len(items)),
        "omitted": max(0, len(items) - limit),
        "items": deepcopy(items[:limit]),
        "reference": deepcopy(reference),
    }


def _prose(value: dict, fields: tuple[str, ...]) -> dict:
    result = deepcopy(value)
    result["truncated_fields"] = []
    for field in fields:
        text = result.get(field)
        if isinstance(text, str) and len(text) > PROSE_POINTS:
            result[field] = text[:PROSE_POINTS]
            result["truncated_fields"].append(field)
    return result


def _acceptance(value: dict, reference: dict) -> dict:
    return {
        **_prose(
            {
                key: value.get(key)
                for key in (
                    "status",
                    "basis",
                    "head_freshness",
                    "source_freshness",
                    "review",
                    "receipt",
                )
            },
            ("basis",),
        ),
        "reasons": collection(
            [_prose({"reason": reason}, ("reason",)) for reason in value.get("reasons", [])],
            reference,
        ),
        "reference": reference,
    }


def _provider_execution(value: dict | None, reference: dict) -> dict:
    """Retain outcomes through collections that obey the existing brief byte bound."""
    value = value or {"status": "not_assessed", "remote_verified_by_tool": False}
    stages = []
    for stage in value.get("stages", []):
        stages.append(
            {
                **{
                    key: deepcopy(stage.get(key))
                    for key in (
                        "id",
                        "kind",
                        "status",
                        "current",
                        "reported_outcome",
                        "source",
                        "timing",
                    )
                },
                "checks": collection(
                    [
                        {
                            key: deepcopy(check.get(key))
                            for key in (
                                "id",
                                "status",
                                "applicability",
                                "mode",
                                "reported_outcome",
                                "run_id",
                                "run_attempt",
                                "job_id",
                            )
                        }
                        for check in stage.get("checks", [])
                    ],
                    reference,
                ),
                "history_total": stage.get("history_total", 0),
                "reference": deepcopy(reference),
            }
        )
    return {
        **{
            key: deepcopy(value.get(key))
            for key in (
                "status",
                "adopted",
                "remote_verified_by_tool",
                "task_id",
                "provider_reference",
            )
        },
        "stages": collection(stages, reference),
        "reference": deepcopy(reference),
    }


def _collections(value):
    if isinstance(value, dict):
        if {"total", "shown", "omitted", "items", "reference"} <= value.keys():
            yield value
        for item in value.values():
            yield from _collections(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collections(item)


def bounded(value: dict, limit: int) -> dict:
    """Remove display rows only; essential identity and condition totals survive."""
    core = _core()
    result = deepcopy(value)

    def counts():
        if "attention_totals_by_kind" in result:
            shown = Counter(item["kind"] for item in result["attention"]["items"])
            result["attention_counts_by_kind"] = {
                kind: {"total": total, "shown": shown[kind], "omitted": total - shown[kind]}
                for kind, total in result["attention_totals_by_kind"].items()
            }

    counts()
    while len(core.canonical(result)) > limit:
        task = result.get("task", {})
        timing = task.get("timing")
        if isinstance(timing, dict) and timing.get("status") != "omitted":
            # New advisory details must not evict established current verification facts.
            task["timing"] = {
                "status": "omitted",
                "reason": "brief_byte_bound",
                "projection_scope": timing["projection_scope"],
                **{
                    family: {
                        "total": timing[family]["total"],
                        "shown": 0,
                        "omitted": timing[family]["total"],
                        "reference": deepcopy(task["reference"]),
                    }
                    for family in ("current", "intervals")
                },
            }
            continue
        verification = task.get("verification") or {}
        contexts = [
            lane
            for lane in verification.get("lanes", {}).get("items", [])
            if isinstance(lane.get("context"), dict) and lane["context"].get("status") != "omitted"
        ]
        if contexts:
            # Context bytes are available through each exact request ref. Keep the
            # current outcome, unavailable reason and recovery action before detail.
            lane = max(contexts, key=lambda item: len(core.canonical(item["context"])))
            lane["context"] = {
                "status": "omitted",
                "reason": "brief_byte_bound",
                "sha256": core.digest(lane["context"]),
            }
            continue
        choices = [item for item in _collections(result) if item["items"]]
        core.require(bool(choices), f"Brief essential envelope exceeds {limit} encoded bytes")
        # Stable largest removable tail first; high-priority attention stays at the front.
        selected = max(choices, key=lambda item: len(core.canonical(item["items"][-1])))
        selected["items"].pop()
        selected["shown"] -= 1
        if selected["omitted"] is not None:
            selected["omitted"] += 1
        counts()
    return result


def project(program: Program, state: Record, snapshot: Record, observations: dict) -> dict:
    """Project canonical factors, never a hypothetical permission to execute."""
    core = _core()
    revision = snapshot["revision"]

    def ref(pointer: str, **identity) -> dict:
        return {"path": "backlog.json", "pointer": pointer, "revision": revision, **identity}

    tasks = state["tasks"]
    task_index = core._TaskIndex.capture(tasks)
    by_id = task_index.tasks
    indexes = task_index.positions
    candidates = {
        item["id"]: item for item in program.status(state, _index=task_index)["candidates"]
    }
    coverage_tasks = {item["task_id"]: item for item in snapshot["coverage"]["tasks"]}
    snapshot_tasks = {item["id"]: item for item in snapshot["tasks"]}
    attention = []
    task_views = {}
    director = snapshot.get("director")
    director_id = director.get("actor_id") if director else None

    def add(kind, entity, priority, reason, reference, actor=None, action=None, task_ids=None):
        attention.append(
            _prose(
                {
                    "kind": kind,
                    "entity_id": entity,
                    "priority": priority,
                    "reason": reason,
                    "responsible_actor_id": actor,
                    "next_action": action,
                    "task_ids": task_ids or [],
                    "reference": reference,
                },
                ("reason", "next_action"),
            )
        )

    for index, warning in enumerate(snapshot["integrity"]["warnings"]):
        add(
            "integrity",
            str(index),
            0,
            warning,
            ref(""),
            director_id,
            "Inspect original records and artifacts",
        )
    for item in observations.get("recovery", []):
        add(
            "recovery",
            item["path"],
            0,
            item["reason"],
            {"path": item["path"]},
            director_id,
            "Inspect recovery-readiness; lock existence does not establish liveness or takeover authority",
        )

    for task in tasks:
        identifier = task["id"]
        pointer = f"/tasks/{indexes[identifier]}"
        original = ref(pointer, task_id=identifier)
        attempts = task["attempts"]
        attempt = attempts[-1] if attempts else None
        candidate = candidates.get(identifier)
        dependencies = (
            candidate["unmet_dependencies"]
            if candidate
            else [item for item in task["depends_on"] if by_id[item]["status"] != "done"]
        )
        conflicts = (
            candidate["scope_conflicts"]
            if candidate
            else task_index.conflicts(identifier)
            if task["status"] in core.OWNED_STATES
            else []
        )
        conflict_rows = [
            {
                "task_id": conflict["id"],
                "owner": by_id[conflict["id"]]["owner"],
                "reasons": collection(
                    [_prose({"reason": reason}, ("reason",)) for reason in conflict["reasons"]],
                    ref(f"/tasks/{indexes[conflict['id']]}", task_id=conflict["id"]),
                ),
                "reference": ref(f"/tasks/{indexes[conflict['id']]}", task_id=conflict["id"]),
                "scope_reference": ref(
                    f"/tasks/{indexes[conflict['id']]}/attempts", task_id=conflict["id"]
                ),
                "selected_scope_reference": ref(
                    pointer
                    + ("/attempts" if task["status"] in core.OWNED_STATES else "/candidate_paths"),
                    task_id=identifier,
                ),
            }
            for conflict in conflicts
        ]
        readiness = bounded(
            {
                "claim": {
                    "status": "not_assessed",
                    "reason": "Actual actor, fresh revalidation receipt and execution metadata are required; recorded factors do not authorize a claim.",
                },
                "unmet_dependencies": collection(
                    dependencies, ref(pointer + "/depends_on", task_id=identifier)
                ),
                "collisions": collection(conflict_rows, original),
                "required_revalidation": "fresh_receipt_required"
                if candidate is not None
                else "not_applicable"
                if task["status"] == "planned"
                else "not_assessed",
                "canonical_candidate": candidate is not None,
                "submission": observations.get("submissions", {}).get(
                    identifier, {"status": "not_assessed"}
                ),
                "peer_agreement": {
                    "status": "unknown",
                    "reason": "Authored notes are context, not machine-assessed agreement.",
                },
                "reference": original,
            },
            4096,
        )
        current = None
        if attempt:
            current = _prose(
                {
                    key: attempt.get(key)
                    for key in (
                        "number",
                        "owner",
                        "status",
                        "next_action",
                        "revalidation_stale",
                    )
                },
                ("next_action",),
            )
            current["scope"] = collection(
                attempt["scope"], ref(pointer + "/attempts", task_id=identifier)
            )
            current["reference"] = ref(
                pointer + f"/attempts/{len(attempts) - 1}", task_id=identifier
            )
            current["submission"] = deepcopy(readiness["submission"])
            if identifier in observations.get("blocks", {}):
                current["block_binding"] = _prose(observations["blocks"][identifier], ("reason",))
                binding = current["block_binding"]
                if binding["status"] == "observed":
                    event = state["events"][binding["block"]["event_index"]]
                    binding.update(_prose({"reason": event.get("reason")}, ("reason",)))
                    binding["reference"] = ref(
                        f"/events/{binding['block']['event_index']}", task_id=identifier
                    )
            rounds = snapshot_tasks[identifier]["attempts"][-1]["rounds"]
            latest = rounds[-1] if rounds else {}
            receipt = latest.get("receipt_record", {})
            review = latest.get("review_record", {})
            current["receipt_metadata_status"] = (
                "observed"
                if "receipt_record" in latest
                else "unavailable"
                if latest.get("receipt")
                else "absent"
            )
            current["review_metadata_status"] = (
                "observed"
                if "review_record" in latest
                else "unavailable"
                if latest.get("review")
                else "absent"
            )
            current["findings_assessment"] = (
                "recorded_metadata_only" if receipt or review else "not_assessed"
            )
            current["checks"] = collection(
                [
                    _prose(
                        {
                            key: check.get(key)
                            for key in ("id", "status", "command", "execution_kind")
                        }
                        | {"reference": current["reference"]},
                        ("command",),
                    )
                    for check in receipt.get("checks", [])
                ],
                current["reference"],
            )
            current["findings"] = collection(
                [
                    _prose(
                        {"kind": key, "reason": reason, "reference": current["reference"]},
                        ("reason",),
                    )
                    for record in (receipt, review)
                    for key in (
                        "unresolved_findings",
                        "unresolved_acceptance",
                        "unresolved_blockers",
                        "limitations",
                    )
                    for reason in record.get(key, [])
                ],
                current["reference"],
            )
            current["receipt_reference"] = latest.get("receipt")
            current["review_reference"] = latest.get("review")
            current["review_verdict"] = review.get("verdict", "unknown")
        notes = []
        for note_index in range(len(task["notes"]) - 1, -1, -1):
            note = task["notes"][note_index]
            notes.append(
                _prose(
                    {
                        **{
                            key: note.get(key)
                            for key in ("actor", "reason", "next_action", "timestamp")
                        },
                        "evidence": collection(
                            note.get("evidence", []),
                            ref(pointer + f"/notes/{note_index}/evidence", task_id=identifier),
                        ),
                        "reference": ref(pointer + f"/notes/{note_index}", task_id=identifier),
                        "interpretation": "authored_context_only",
                    },
                    ("reason", "next_action"),
                )
            )
        criterion_links = []
        for criterion in snapshot["coverage"]["criteria"]:
            for link in criterion["links"]:
                if link.get("task_id") == identifier:
                    criterion_links.append(
                        {
                            "criterion": criterion["criterion"],
                            "task_id": identifier,
                            "records": collection(
                                link.get("records", []),
                                ref("/coverage_annotations", task_id=identifier),
                            ),
                            "reference": ref("/coverage_annotations", task_id=identifier),
                        }
                    )
        source = core.planning.source_for_task(state, task)
        acceptance = coverage_tasks.get(identifier, {}).get(
            "recorded_acceptance",
            {
                "status": "unknown",
                "reasons": [],
                "basis": "Task is not assessed by current objective acceptance",
            },
        )
        delivery = (
            program.current_delivery(task)
            if task["status"] == "done"
            else {"status": "unrecorded", "remote_verified_by_tool": False}
        )
        view = _prose(
            {
                "id": identifier,
                "title": task["source"]["title"],
                "status": task["status"],
                "phase": task["phase"],
                "owner": task["owner"],
                "actor_contact": "unknown",
                "actor_liveness": "unknown",
                "disposition": task.get("disposition"),
                "expected_result": task.get("expected_result", task["source"].get("text")),
                "next_action": task["next_action"],
                "readiness": readiness,
                "current_attempt": current,
                "scope": collection(
                    attempt["scope"]
                    if attempt and task["status"] in core.OWNED_STATES
                    else task["candidate_paths"],
                    original,
                ),
                "required_checks": collection(
                    task["required_checks"], ref(pointer + "/required_checks", task_id=identifier)
                ),
                "related_to": collection(
                    task["related_to"], ref(pointer + "/related_to", task_id=identifier)
                ),
                "coordination_notes": collection(
                    notes, ref(pointer + "/notes", task_id=identifier)
                ),
                "criterion_links": collection(
                    criterion_links, ref("/coverage_annotations", task_id=identifier)
                ),
                "recorded_acceptance": _acceptance(acceptance, original),
                "verification": deepcopy(snapshot_tasks[identifier].get("verification")),
                "timing": _task_timing(snapshot.get("timing"), identifier),
                "review_handoff": (
                    {
                        key: deepcopy(value)
                        for key, value in snapshot_tasks[identifier]["review_handoff"].items()
                        if key
                        in {
                            "handoff_id",
                            "task_id",
                            "attempt",
                            "submission_round",
                            "phase",
                            "currentness",
                            "cause",
                            "next_actor_id",
                            "next_action",
                            "next_trigger",
                            "reference",
                        }
                    }
                    if snapshot_tasks[identifier].get("review_handoff") is not None
                    else None
                ),
                "provider_execution": _provider_execution(
                    snapshot_tasks[identifier].get("provider_execution"),
                    ref(pointer + "/deliveries", task_id=identifier),
                ),
                "delivery": {
                    key: delivery.get(key)
                    for key in ("status", "remote_verified_by_tool", "receipt")
                },
                "history": {
                    "attempts_total": len(attempts),
                    "reference": ref(pointer + "/attempts", task_id=identifier),
                },
                "source_reference": {
                    "path": source["path"],
                    "sha256": source["sha256"],
                    "reference_base": source["reference_base"],
                    "line": task["source"]["line"],
                    "section_sha256": task["source"]["sha256"],
                },
                "reference": original,
            },
            ("title", "expected_result", "next_action"),
        )
        for field in ("timing", "review_handoff"):
            if view.get(field) is None:
                view.pop(field, None)
        task_views[identifier] = view
        actor = task["owner"] or director_id
        if task["status"] == "blocked":
            add(
                "blocked",
                identifier,
                2,
                "Current attempt is blocked; the recorded blocker and resolution guards still apply",
                original,
                actor,
                task["next_action"],
                [identifier],
            )
        for conflict in conflicts:
            add(
                "collision",
                identifier + ":" + conflict["id"],
                2,
                "; ".join(conflict["reasons"]),
                original,
                actor,
                task["next_action"],
                [identifier, conflict["id"]],
            )
        if acceptance["status"] == "failed":
            add(
                "acceptance",
                identifier,
                2,
                "; ".join(acceptance["reasons"]),
                original,
                director_id,
                "Inspect the exact submission/review binding",
                [identifier],
            )
        if task["status"] == "review":
            add(
                "review",
                identifier,
                3,
                "Submission awaits independent review; source freshness is a separate observation",
                original,
                director_id,
                task["next_action"],
                [identifier],
            )
        if task["status"] == "done" and view["delivery"]["status"] != "ci_recorded_success":
            add(
                "delivery",
                identifier,
                3,
                "Recorded task acceptance and delivery/CI are separate: "
                + view["delivery"]["status"],
                original,
                director_id,
                "Inspect frozen delivery inputs and provider facts",
                [identifier],
            )
        if dependencies:
            add(
                "dependency",
                identifier,
                4,
                "Required predecessors are not done",
                original,
                actor,
                task["next_action"],
                [identifier],
            )
        if candidate is not None:
            add(
                "revalidation",
                identifier,
                4,
                "A fresh actual revalidation receipt is required; claim eligibility is not assessed",
                original,
                actor,
                task["next_action"],
                [identifier],
            )

    questions = []
    goal = state.get("objective")
    pending = {
        item["id"]
        for item in core.questions.pending_questions(
            state, goal["id"] if goal else None, goal["task_ids"] if goal else None
        )
    }
    for index, item in enumerate(snapshot["questions"]):
        question = _prose(
            {
                key: item.get(key)
                for key in (
                    "id",
                    "version",
                    "status",
                    "actor_id",
                    "objective_id",
                    "prompt",
                    "why",
                    "requested_decision",
                    "issued_at",
                )
            },
            ("prompt", "why", "requested_decision"),
        )
        question["reference"] = ref(f"/questions/items/{index}", question_id=item["id"])
        question["saved_answer"] = item.get("answer") is not None
        question["recorded_acknowledgement"] = item.get("acknowledgement") is not None
        question["recorded_outcome"] = item.get("outcome") is not None
        question["affected_task_ids"] = collection(
            item.get("affected_task_ids", []), question["reference"]
        )
        questions.append(question)
        question["current_goal_attention"] = item["id"] in pending
        if item["id"] in pending:
            add(
                "question",
                item["id"],
                1,
                "Recorded decision state: " + item["status"],
                question["reference"],
                director_id,
                "Inspect the saved answer, acknowledgement and application separately",
                item.get("affected_task_ids", []),
            )
    transfers = []
    for index, item in enumerate(snapshot["handoffs"]):
        stage = (
            "accepted"
            if item.get("acceptance") is not None
            else "acknowledged"
            if item.get("acknowledgement") is not None
            else "proposed"
        )
        transfer = {
            key: item.get(key)
            for key in (
                "id",
                "role",
                "task_id",
                "attempt",
                "from_actor",
                "to_actor",
                "outgoing_ref",
                "ack_ref",
                "accept_ref",
                "acknowledged_at",
                "accepted_at",
            )
        }
        transfer.update(
            {"stage": stage, "kind": "succession", "reference": ref(f"/handoffs/{index}")}
        )
        transfers.append(transfer)
        if stage != "accepted":
            add(
                "transfer",
                item["id"],
                3,
                "Succession is " + stage + "; it does not replace release and fresh claim",
                transfer["reference"],
                item["to_actor"] if stage == "proposed" else director_id,
                "Follow the recorded succession workflow",
                [item["task_id"]] if item.get("task_id") else [],
            )

    coverage_history = []
    for index, item in enumerate(snapshot["coverage"]["history"]):
        reference = ref(f"/coverage_annotations/{index}")
        coverage_history.append(
            {
                "id": item["id"],
                "freshness": item["freshness"],
                "receipt": item["receipt"],
                "reference": reference,
            }
        )
        if item["freshness"] != "current":
            add(
                "coverage",
                item["id"],
                0,
                "; ".join(item["reasons"]),
                reference,
                director_id,
                "Inspect exact criterion and current membership/attempt bindings",
            )
    objective = snapshot.get("objective")
    objective_view = None
    if objective:
        objective_view = _prose(
            {
                key: objective.get(key)
                for key in (
                    "id",
                    "title",
                    "status",
                    "next_action_owner",
                    "next_action",
                    "supplied_by",
                    "decision_owner",
                )
            },
            ("title", "next_action"),
        )
        objective_view["reference"] = ref("/objective")
        objective_view["criteria"] = collection(
            [
                _prose(
                    {
                        **criterion["criterion"],
                        "text": criterion["text"],
                        "linkage": criterion["linkage"],
                        "knowledge": criterion["knowledge"],
                        "contributors": collection(
                            [link["task_id"] for link in criterion["links"]],
                            ref("/coverage_annotations"),
                        ),
                        "uncovered": collection(
                            [
                                _prose(
                                    {
                                        "reason": item["reason"],
                                        "reference": ref("/coverage_annotations"),
                                    },
                                    ("reason",),
                                )
                                for item in criterion["uncovered"]
                            ],
                            ref("/coverage_annotations"),
                        ),
                        "reference": ref(f"/objective/criteria/{index}"),
                    },
                    ("text",),
                )
                for index, criterion in enumerate(snapshot["coverage"]["criteria"])
            ],
            ref("/objective/criteria"),
        )
        objective_view["task_ids"] = collection(objective["task_ids"], ref("/objective/task_ids"))
        integrated = snapshot["coverage"]["integrated_review"]
        if integrated.get("status") != "accepted":
            add(
                "objective",
                objective["id"],
                1,
                "Objective completion and independent integrated review require actual current inputs",
                ref("/objective"),
                director_id,
                objective.get("next_action", "Inspect the objective-completion prerequisites"),
                objective["task_ids"],
            )
    integrated = snapshot["coverage"]["integrated_review"]
    for row in attention:
        if row["kind"] == "objective" and integrated.get("next_action"):
            row["responsible_actor_id"] = integrated.get("next_actor_id")
            row["next_action"] = integrated["next_action"]
            row["next_trigger"] = integrated.get("next_trigger")
        elif row["kind"] == "review":
            handoff = next(
                (item.get("review_handoff") for item in tasks if item["id"] == row["entity_id"]),
                None,
            )
            if handoff and handoff.get("currentness") == "current":
                row["responsible_actor_id"] = handoff.get("next_actor_id")
                row["next_action"] = handoff.get("next_action")
                row["next_trigger"] = handoff.get("next_trigger")
    attention.sort(key=lambda item: (item["priority"], item["kind"], item["entity_id"]))
    for row in attention:
        row["actionability"] = "actionable"
        if (
            row["kind"] == "objective"
            and objective
            and objective.get("status") == "active"
            and any(by_id[identifier]["status"] != "done" for identifier in objective["task_ids"])
        ):
            row["attention_kind"] = "completion_reminder"
            row["actionability"] = "routine"
        row["criterion_impact"] = collection(
            [
                criterion["criterion"]
                for criterion in snapshot["coverage"]["criteria"]
                if any(link["task_id"] in row["task_ids"] for link in criterion["links"])
            ],
            ref("/coverage_annotations"),
        )
        row["criterion_impact_assessment"] = "explicit_links_only"
        row["observed_revision"] = revision
    kinds = Counter(item["kind"] for item in attention)
    phases = state.get("phases")
    envelope = {
        "brief_version": 1,
        "program_id": snapshot["program_id"],
        "revision": revision,
        "advisory": True,
        "execution_authority": "not_assessed",
        "objective": objective_view,
        "director": {
            "actor_id": director["actor_id"],
            "authority": _prose(director["authority"], ("basis", "authorized_by")),
            "acknowledged_at": director.get("acknowledged_at"),
            "reference": ref("/director"),
        }
        if director
        else None,
        "communication_language": snapshot.get("communication_language"),
        "membership": {
            "total": len(objective["task_ids"]) if objective else None,
            "dispositions": dict(
                sorted(Counter(task.get("disposition", "current") for task in tasks).items())
            ),
            "reference": ref("/objective/task_ids"),
        },
        "publication": {
            "location_guard": "observed_passed",
            "recovery_execution": "not_assessed",
            "records": collection(observations.get("publication", []), ref("")),
        },
        "original_constraints": collection(
            [
                {
                    "path": source["path"],
                    "sha256": source["sha256"],
                    "reference_base": source["reference_base"],
                }
                for source in core.planning.descriptors(state)
            ],
            ref("/source"),
        ),
        "stage": {
            "adopted": phases is not None,
            "inventory_recorded": bool(phases and phases.get("inventory")),
            "candidate_review_recorded": bool(phases and phases.get("review")),
            "execution_acceptance_recorded": bool(phases and phases.get("acceptance")),
            "reference": ref("/phases"),
        },
        "integrity": {
            "status": snapshot["integrity"]["status"],
            "warnings_total": len(snapshot["integrity"]["warnings"]),
            "reference": ref(""),
        },
        "limitations": [
            "Recorded actor IDs do not establish contact or liveness.",
            "No Git, claim, native execution, CI or future transition is performed.",
            "Unknown and omitted conditions do not mean absent conditions.",
        ],
        "bounds": {
            "max_bytes": PROGRAM_BYTES,
            "attention_rows": ATTENTION_ROWS,
            "prose_code_points": PROSE_POINTS,
            "includes_json_escaping_and_newline": True,
        },
        "reference": ref(""),
    }
    queue = snapshot.get("review_queue")
    queue_view = None
    if queue is not None:
        queue_view = {
            key: deepcopy(queue.get(key))
            for key in ("schema_version", "queue_version", "status", "observed_revision", "counts")
        }
        queue_rows = queue["rows"]
        queue_view["rows"] = {
            **{
                key: deepcopy(queue_rows[key]) for key in ("total", "shown", "omitted", "reference")
            },
            "items": [
                _prose(
                    {
                        key: deepcopy(row.get(key))
                        for key in (
                            "row_id",
                            "kind",
                            "task_id",
                            "request_id",
                            "action",
                            "next_actor_id",
                            "next_action",
                            "reference",
                        )
                    },
                    ("next_action",),
                )
                for row in queue_rows["items"]
            ],
        }
    closure = snapshot.get("closure")
    closure_view = None
    if closure is not None:
        closure_ref = ref("/objective")
        closure_view = {
            key: deepcopy(closure.get(key))
            for key in (
                "schema_version",
                "status",
                "accepted",
                "objective_id",
                "next_actor_id",
                "next_action",
                "task_counts",
                "plan",
            )
        }
        closure_view["requirements"] = {
            **deepcopy(closure["requirements"]),
            "reference": closure_ref,
        }
        closure_view["reference"] = closure_ref
    program_view = {
        **envelope,
        "counts": snapshot["counts"],
        "attention": collection(attention, ref("")),
        "attention_totals_by_kind": dict(sorted(kinds.items())),
        "assessment_totals": {
            "claim_not_assessed": len(tasks),
            "peer_agreement_unknown": len(tasks),
            "integrated_review_not_assessed": int(integrated.get("status") == "not_assessed"),
        },
        "coverage_history": collection(coverage_history, ref("/coverage_annotations")),
        "review_queue": queue_view,
        "review_handoffs": _throughput_brief(snapshot.get("review_handoffs"), "rows"),
        "reviewer_load": _throughput_brief(snapshot.get("reviewer_load"), "rows"),
        "timing": _throughput_brief(snapshot.get("timing"), "current"),
        "evidence_preparation": deepcopy(snapshot.get("evidence_preparation")),
        "record_batch": _throughput_brief(snapshot.get("record_batch"), "operations"),
        "closure": closure_view,
        "verification": {
            "status": snapshot.get("verification", {}).get("status", "absent"),
            "history": deepcopy(snapshot.get("verification", {}).get("history")),
            "reference": ref("/verification"),
        },
        "questions": collection(questions, ref("/questions")),
        "succession": collection(transfers, ref("/handoffs")),
        "checkpoint": {
            "status": "historical" if snapshot.get("checkpoint") else "absent",
            "reference": ref("/checkpoint"),
        },
        "integrated_review": {
            **{
                key: deepcopy(integrated.get(key))
                for key in (
                    "schema_version",
                    "status",
                    "currentness",
                    "reason_codes",
                    "counts",
                    "next_actor_id",
                    "next_action",
                    "next_trigger",
                    "remote_verified_by_tool",
                )
            },
            "basis": {
                key: deepcopy(integrated["basis"].get(key))
                for key in (
                    "identity",
                    "completion_ref",
                    "integrated_review_ref",
                    "source_head",
                    "source_digest",
                )
            }
            if integrated.get("basis")
            else None,
            "projection_scope": "summary",
            "requirements": collection(integrated.get("requires", []), ref("/objective")),
            "recorded_acceptance": _acceptance(
                snapshot["coverage"]["recorded_objective_acceptance"], ref("/objective")
            ),
        },
    }
    return {
        "envelope": envelope,
        "program": bounded(program_view, PROGRAM_BYTES),
        "tasks": task_views,
        "attention": attention,
    }


def selected(projection: dict, task_id: str) -> dict:
    core = _core()
    core.require(task_id in projection["tasks"], f"Unknown task: {task_id}")
    value = deepcopy(projection["envelope"])
    value["bounds"]["max_bytes"] = TASK_BYTES
    value["task"] = projection["tasks"][task_id]
    value["attention"] = collection(
        [
            item
            for item in projection["attention"]
            if task_id in item["task_ids"] or item["priority"] == 0
        ],
        value["task"]["reference"],
    )
    value["attention_totals_by_kind"] = dict(
        sorted(
            Counter(
                item["kind"]
                for item in projection["attention"]
                if task_id in item["task_ids"] or item["priority"] == 0
            ).items()
        )
    )
    return bounded(value, TASK_BYTES)
