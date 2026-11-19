"""Establish role attribution and comparable controls before scheduling model turns."""

from copy import copy
import json


def pilot_reasons(case):
    reasons = []
    actor = case.get("actor_conditions") or {}
    if (
        case.get("runner_status") != "finished"
        or not case.get("turn_completed")
        or case.get("role_status") != "confirmed"
        or not case.get("first_business_call")
    ):
        reasons.append("actor_business_not_verified")
    if case["role"] == "subagent" and len(case.get("actor_receipt", {}).get("children", [])) != 1:
        reasons.append("child_link_not_verified")
    if (
        not actor.get("project_verified")
        or actor.get("inventory_status") != "observed"
        or not all(
            actor.get(key)
            for key in (
                "host_binary_digest",
                "namespace",
                "server_commit",
                "tool_schema_digest",
                "competitor_digest",
            )
        )
    ):
        reasons.append("actor_surface_not_verified")
    permissions = actor.get("permissions") or {}
    if (
        not permissions.get("digest")
        or permissions.get("sandbox") not in ("readOnly", "read-only")
        or permissions.get("approval")
        != (
            "on-request"
            if case.get("approval_policy") == "auto-review"
            else case.get("approval_policy")
        )
    ):
        reasons.append("actor_permissions_not_verified")
    if not case.get("editor_start_state"):
        reasons.append("editor_start_state_not_verified")
    if not case.get("editor_end_state"):
        reasons.append("editor_end_state_not_verified")
    return reasons


def pair_controls(left, right):
    issues = []
    for key in ("namespace", "permissions", "host_binary_digest", "competitor_digest"):
        values = [(case.get("actor_conditions") or {}).get(key) for case in (left, right)]
        if not all(values):
            issues.append("unverified:actor_" + key)
        elif values[0] != values[1]:
            issues.append("different:actor_" + key)
    commits = [(case.get("actor_conditions") or {}).get("server_commit") for case in (left, right)]
    if not all(commits):
        issues.append("unverified:actor_server_source")
    elif commits[0] == commits[1]:
        issues.append("identical:actor_server_source")
    if not all(
        (case.get("actor_conditions") or {}).get("project_verified") for case in (left, right)
    ):
        issues.append("unverified:actor_project")
    if not left.get("editor_start_state") or not right.get("editor_start_state"):
        issues.append("unverified:editor_start_state")
    elif left["editor_start_state"] != right["editor_start_state"]:
        issues.append("different:editor_start_state")
    return issues


def run_pilots(arms, output, roles, run_case, score_case):
    """A pilot tests capture of business activity, independent of which tool the model chose."""
    results = {}
    for role in roles:
        attempts = []
        for attempt in (1, 2):
            cases = {}
            reasons = []
            for arm, selected in arms.items():
                pilot = copy(selected)
                pilot.output = output / "preflight" / f"attempt-{attempt}" / arm
                pilot.output.mkdir(parents=True, exist_ok=True)
                pilot.case_metadata = {"study": selected.study, "phase": "pilot", "arm": arm}
                run_case(pilot, "game-view", "en", role, 1)
                case = score_case(pilot.output / f"game-view-en-{role}-01")
                cases[arm] = case
                reasons.extend(arm + ":" + reason for reason in pilot_reasons(case))
            controls = pair_controls(cases["baseline"], cases["candidate"])
            attempts.append(
                {
                    "attempt": attempt,
                    "reasons": reasons + controls,
                    "arms": {
                        arm: {
                            key: case.get(key)
                            for key in (
                                "role_status",
                                "actor_receipt",
                                "first_business_call",
                                "actor_conditions",
                                "editor_start_state",
                                "child_lookup",
                            )
                        }
                        for arm, case in cases.items()
                    },
                }
            )
            if (
                reasons
                or not controls
                or any(not reason.startswith("different:") for reason in controls)
            ):
                break
        results[role] = {"supported": not attempts[-1]["reasons"], "attempts": attempts}
        (output / "preflight.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8"
        )
    return results
