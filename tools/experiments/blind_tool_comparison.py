"""Join trial matrices without dropping missing, incomparable or unattributed trials."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from tools.experiments.blind_tool_trace import score_directory

AXES = ("task", "language", "role", "repetition")
CONDITIONS = (
    "model",
    "effort",
    "approval_policy",
    "delegation",
    "backend",
    "graph_asset",
    "project",
    "instance",
    "prompt_digest",
    "answer_protocol",
    "editor_start_state",
)
METRICS = (
    "runner_status",
    "turn_completed",
    "role_status",
    "first_business_call",
    "first_shell_purpose",
    "first_observation_attempt",
    "first_observation_attempt_suitable",
    "preparatory_calls_before_observation",
    "first_suitable_business_call",
    "calls_before_first_suitable",
    "first_successful_suitable_call",
    "calls_before_first_success",
    "calls_before_window_absence",
    "declared_graph_limit",
    "calls_before_declared_graph_limit",
    "wire_evidence",
    "task_evidence",
    "live_evidence",
    "saved_graph_evidence",
    "approval_blocks",
    "approval_visibility",
    "repeat_suitable_calls",
    "retry_suitable_calls",
    "repeats_after_complete_evidence",
    "raycast_before_size",
    "backend_identity",
    "host_mode",
    "competitor_digest",
    "observed_server_commit",
    "evidence_transport",
    "selection_diagnosis",
    "actor_receipt",
    "actor_conditions",
    "role_attribution",
    "prepared_pair",
    "pair_reasons",
    "fixture_availability",
    "graph_data_state",
    "unsaved_graph_observed",
    "final_answer",
)


def paired_schedule(cells):
    """Balance AB/BA within each task, locale and role across successive repetitions."""
    strata = {}
    result = []
    for cell in cells:
        stratum = tuple(cell[:3])
        offset = strata.setdefault(stratum, len(strata))
        order = ("baseline", "candidate") if (offset + cell[3]) % 2 else ("candidate", "baseline")
        result.extend((arm, cell) for arm in order)
    return result


def summarize(cases: list[dict]) -> dict:
    attempted = len(cases)
    cases = [case for case in cases if case.get("turn_completed") is True]
    return {
        "attempted_cases": attempted,
        "observed": len(cases),
        "role_status": dict(Counter(c["role_status"] for c in cases)),
        "first_shell_purpose": dict(
            Counter(c["first_shell_purpose"] for c in cases if c.get("first_shell_purpose"))
        ),
        "first_suitable_on_first_business_call": sum(
            c["calls_before_first_suitable"] == 0 for c in cases
        ),
        "complete_wire_evidence": sum(c["wire_evidence"] == "complete" for c in cases),
        "complete_task_evidence": sum(c["task_evidence"] == "complete" for c in cases),
        "approval_blocks": sum(c["approval_blocks"] for c in cases),
        "repeats_after_complete_evidence": sum(
            c["repeats_after_complete_evidence"] or 0 for c in cases
        ),
        "repeat_timing_measured_cases": sum(
            c["repeats_after_complete_evidence"] is not None for c in cases
        ),
        "raycast_before_size": sum(c["raycast_before_size"] or 0 for c in cases),
        "raycast_timing_measured_cases": sum(c["raycast_before_size"] is not None for c in cases),
        "final_answers": dict(Counter(c["final_answer"]["status"] for c in cases)),
    }


def study_stratum(pairs):
    eligible = [pair for pair in pairs if pair["eligible"]]
    result = {"planned_pairs": len(pairs), "eligible_pairs": len(eligible), "arms": {}}
    for arm in ("baseline", "candidate"):
        observed = [pair[arm] for pair in pairs if pair[arm]]
        answered = [pair[arm] for pair in eligible]
        # The open-window task measures restraint. Its declared readers only observe a saved file.
        positive = [
            pair[arm]
            for pair in eligible
            if pair["task"] != "graph-open"
            and (
                pair["task"] not in {"compile-outcome", "pointer-order"}
                or all(
                    pair[name].get("fixture_availability") == "positive"
                    for name in ("baseline", "candidate")
                )
            )
        ]
        captures = Counter(
            (case.get("selection_diagnosis") or {}).get("search_capture", "unknown")
            for case in positive
        )
        ranked = [
            case["selection_diagnosis"]["first_search_top3_suitable"]
            for case in positive
            if (case.get("selection_diagnosis") or {}).get("search_capture") == "ranked"
        ]
        direct = [
            case
            for case in positive
            if (case.get("selection_diagnosis") or {}).get("search_capture") == "no_search"
        ]

        def ratio(hits, total):
            return {"numerator": hits, "denominator": total}

        result["arms"][arm] = {
            "attempted_cases": len(observed),
            "observed_turns": sum(c.get("turn_completed") is True for c in observed),
            "confirmed_actors": sum(
                c.get("turn_completed") is True and c["role_status"] == "confirmed"
                for c in observed
            ),
            "complete_wire": ratio(
                sum(c["wire_evidence"] == "complete" for c in positive), len(positive)
            ),
            "first_success_on_first_business_call": ratio(
                sum(c["calls_before_first_success"] == 0 for c in positive), len(positive)
            ),
            "first_observation_attempt_suitable": ratio(
                sum(c.get("first_observation_attempt_suitable") is True for c in positive),
                sum(c.get("first_observation_attempt_suitable") is not None for c in positive),
            ),
            "first_success_without_search": ratio(
                sum(c["calls_before_first_success"] == 0 for c in direct), len(direct)
            ),
            "natural_search_capture": dict(captures),
            "fixture_availability": dict(
                Counter(
                    c["fixture_availability"] for c in observed if c.get("fixture_availability")
                )
            ),
            "suitable_in_first_search_top3": ratio(
                sum(value is True for value in ranked), sum(value is not None for value in ranked)
            ),
            "answer_status": dict(Counter(c["final_answer"]["status"] for c in answered)),
            "valid_answer_format": ratio(
                sum(c["final_answer"].get("format_valid") is True for c in answered),
                sum("format_valid" in c["final_answer"] for c in answered),
            ),
            **{
                key: ratio(
                    sum(c["final_answer"].get(key, {}).get("matched", 0) for c in answered),
                    sum(c["final_answer"].get(key, {}).get("measured", 0) for c in answered),
                )
                for key in (
                    "fact_accuracy",
                    "source_accuracy",
                    "channel_accuracy",
                    "provenance_accuracy",
                )
            },
        }
    return result


def compare_directories(baseline: Path, candidate: Path) -> dict:
    reports = [
        score_directory(path) if path.is_dir() else {"cases": [], "unrun_cases": []}
        for path in (baseline, candidate)
    ]
    indices = []
    planned = set()
    for report in reports:
        index = {tuple(case[k] for k in AXES): case for case in report["cases"]}
        if len(index) != len(report["cases"]):
            raise ValueError("Duplicate task/language/role/repetition in matrix")
        planned.update(index)
        planned.update(map(tuple, report["unrun_cases"]))
        indices.append(index)
    pairs = []
    pilot_path = baseline.parent / "preflight.json"
    pilots = (
        json.loads(pilot_path.read_text(encoding="utf-8"))
        if baseline.parent == candidate.parent and pilot_path.is_file()
        else {}
    )
    for key in sorted(planned):
        left, right = (index.get(key) for index in indices)
        reasons = []
        order = None
        if left is None or right is None:
            reasons.append("missing_arm")
            pilot = pilots.get(key[2], {})
            if pilot.get("supported") is False and pilot.get("attempts"):
                reasons.extend("preflight:" + why for why in pilot["attempts"][-1]["reasons"])
        else:
            if left.get("pair_mode") == "prepared" or right.get("pair_mode") == "prepared":
                from tools.experiments.blind_tool_pair import preparation_issues

                reasons.extend(preparation_issues(left, right))
                for arm, case in (("baseline", left), ("candidate", right)):
                    reasons.extend(arm + ":" + issue for issue in case.get("pair_reasons", []))
                    if case.get("role") == "subagent":
                        receipt = case.get("actor_receipt") or {}
                        if len(receipt.get("children", [])) != 1 or not all(
                            receipt.get(k)
                            for k in (
                                "child_business_observed",
                                "child_final_observed",
                                "child_turn_completed",
                            )
                        ):
                            reasons.append("unverified:" + arm + "_child_turn")
            host_modes = [c.get("host_mode", "isolated") for c in (left, right)]
            if host_modes[0] != host_modes[1]:
                reasons.append("different:host_mode")
            if "configured" in host_modes:
                if any(c.get("competition_observed") is not True for c in (left, right)):
                    reasons.append("unverified:real_competition")
                if not all(c.get("observed_server_commit") for c in (left, right)):
                    reasons.append("unverified:server_source")
                if not all(c.get("competitor_digest") for c in (left, right)):
                    reasons.append("unverified:competing_tools")
                elif left["competitor_digest"] != right["competitor_digest"]:
                    reasons.append("different:competing_tools")
            if left.get("study") or right.get("study") or left.get("pair_mode") == "prepared":
                from tools.experiments.blind_tool_preflight import pair_controls

                if left.get("study") != right.get("study"):
                    reasons.append("different:study")
                reasons.extend(pair_controls(left, right))
                for arm, case in (("baseline", left), ("candidate", right)):
                    actor = case.get("actor_conditions") or {}
                    if actor.get("inventory_status") != "observed" or not actor.get(
                        "tool_schema_digest"
                    ):
                        reasons.append("unverified:" + arm + "_actor_inventory")
                    if not case.get("editor_end_state"):
                        reasons.append("unverified:" + arm + "_editor_end_state")
                    elif case["editor_end_state"] != case.get("editor_start_state"):
                        reasons.append("changed:" + arm + "_editor_state")
            for name in CONDITIONS:
                if (
                    name not in left
                    or name not in right
                    or (name != "graph_asset" and (not left[name] or not right[name]))
                ):
                    reasons.append(f"unverified:{name}")
                elif left[name] != right[name]:
                    reasons.append(f"different:{name}")
            if left["role_status"] != "confirmed" or right["role_status"] != "confirmed":
                reasons.append("unconfirmed_role")
            if any(
                c.get("runner_status") != "finished" or not c["turn_completed"]
                for c in (left, right)
            ):
                reasons.append("incomplete_run")
            if not left.get("backend_identity") or not right.get("backend_identity"):
                reasons.append("unverified:treatment")
            elif left["backend_identity"] == right["backend_identity"]:
                reasons.append("identical_backend_identity")
            if (
                left.get("schedule_id")
                and left["schedule_id"] == right.get("schedule_id")
                and type(left.get("execution_order")) is int
                and type(right.get("execution_order")) is int
                and left["execution_order"] != right["execution_order"]
            ):
                order = "AB" if left["execution_order"] < right["execution_order"] else "BA"
            if order is None:
                reasons.append("unverified:execution_order")
        pair = {
            **dict(zip(AXES, key, strict=True)),
            "eligible": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
            "order": order,
        }
        for arm, case in (("baseline", left), ("candidate", right)):
            pair[arm] = {k: case.get(k) for k in METRICS} if case else None
        pairs.append(pair)
    eligible = [p for p in pairs if p["eligible"]]
    summaries = {}
    observed_by_role = {}
    for arm in ("baseline", "candidate"):
        summaries[arm] = summarize([pair[arm] for pair in eligible])
        observed_by_role[arm] = {
            role: summarize([pair[arm] for pair in pairs if pair["role"] == role and pair[arm]])
            for role in sorted({pair["role"] for pair in pairs})
        }
    return {
        "schema": "unity-mcp/blind-tool-comparison@1",
        "planned_pairs": len(pairs),
        "eligible_pairs": len(eligible),
        "eligible_order_counts": dict(Counter(p["order"] for p in eligible)),
        "eligible_summary": summaries,
        "observed_by_requested_role": observed_by_role,
        "coverage": {
            "overall": study_stratum(pairs),
            **{
                axis: {
                    value: study_stratum([pair for pair in pairs if pair[axis] == value])
                    for value in sorted({pair[axis] for pair in pairs})
                }
                for axis in ("task", "language", "role")
            },
        },
        "pairs": pairs,
    }
