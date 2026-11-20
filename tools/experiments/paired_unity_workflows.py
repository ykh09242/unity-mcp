"""Compare independent native workflow receipts from the fork and Unity CLI MCP."""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from statistics import median

TASKS = ("asset-move", "blocked-click", "interrupted-edit")
MODES = ("mcp-only", "shell-enabled")
ARMS = ("fork", "official")
DIGEST = re.compile("[0-9a-f]{64}")


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def count(value):
    return type(value) is int and value >= 0


def digest(value):
    return isinstance(value, str) and bool(DIGEST.fullmatch(value))


def verdict(task, facts):
    """Verify case-specific facts emitted by the independent fixture observer."""
    match task:
        case "asset-move":
            before = facts.get("guid_before")
            return (
                isinstance(before, str)
                and re.fullmatch("[0-9a-f]{32}", before) is not None
                and before == facts.get("guid_after")
                and facts.get("original_ready") is True
                and facts.get("moved_old_key_ready") is False
                and facts.get("repaired_key_ready") is True
                and facts.get("witness_environment") == "player"
            )
        case "blocked-click":
            return (
                facts.get("input_mode") == "pointer"
                and facts.get("blocked_target_first") is False
                and facts.get("repaired_target_first") is True
                and count(facts.get("blocked_transitions"))
                and facts["blocked_transitions"] == 0
                and count(facts.get("repaired_transitions"))
                and facts["repaired_transitions"] == 1
            )
        case "interrupted-edit":
            return (
                facts.get("interruption_observed") is True
                and facts.get("created_guid") == facts.get("registered_guid")
                and isinstance(facts.get("created_guid"), str)
                and re.fullmatch("[0-9a-f]{32}", facts["created_guid"]) is not None
                and all(
                    count(facts.get(key)) and facts[key] == 1
                    for key in ("assets", "entries", "creation_effects", "registration_effects")
                )
            )
    return False


def run_score(task, mode, arm, run):
    """Keep partial, simulated and unavailable trials outside every denominator."""
    if not isinstance(run, dict):
        return None, "missing"
    if run.get("status") != "completed":
        return None, run.get("reason") or "native_run_unavailable"
    for key in ("model", "effort", "host", "fixture_run_id", "product_identity"):
        if not isinstance(run.get(key), str) or not run[key]:
            return None, "missing:" + key
    if run.get("execution") != "native" or not str(run.get("unity_version", "")).startswith(
        "6000."
    ):
        return None, "unsupported_native_environment"
    if arm == "official" and not all(
        isinstance(run.get(key), str) and run[key] for key in ("unity_cli", "pipeline_package")
    ):
        return None, "missing_official_cli_or_pipeline_identity"
    for key in ("fixture_digest", "permission_digest", "prompt_digest", "observer_digest"):
        if not digest(run.get(key)):
            return None, "missing:" + key
    if not all(count(run.get(key)) for key in ("mcp_calls", "shell_calls", "duplicate_writes")):
        return None, "missing_call_or_write_counts"
    if mode == "mcp-only" and run["shell_calls"] != 0:
        return None, "shell_used_in_mcp_only"
    if type(run.get("actor_claimed_success")) is not bool or not number(run.get("elapsed_seconds")):
        return None, "missing_actor_outcome_or_timing"
    if run.get("tokens") is not None and not count(run["tokens"]):
        return None, "invalid_token_count"
    witness = run.get("independent") or {}
    if (
        witness.get("fixture_run_id") != run["fixture_run_id"]
        or not digest(witness.get("artifact_digest"))
        or not isinstance(witness.get("facts"), dict)
        or witness.get("coverage") != "complete"
    ):
        return None, "independent_witness_unavailable"
    passed = verdict(task, witness["facts"]) and run["duplicate_writes"] == 0
    return {
        "independent_pass": passed,
        "false_pass": run["actor_claimed_success"] and not passed,
        "duplicate_writes": run["duplicate_writes"],
        "mcp_calls": run["mcp_calls"],
        "shell_calls": run["shell_calls"],
        "elapsed_seconds": run["elapsed_seconds"],
        "tokens": run.get("tokens"),
    }, None


def compare(pairs):
    """Score only same-control pairs and keep each mode's sample count explicit."""
    results = []
    identities = set()
    eligible = []
    fixture_uses = Counter()
    artifact_uses = Counter()
    for pair in pairs:
        task, mode, repetition = pair["task"], pair["mode"], pair["repetition"]
        identity = (task, mode, repetition)
        if (
            task not in TASKS
            or mode not in MODES
            or not count(repetition)
            or identity in identities
        ):
            raise ValueError("Invalid or duplicate task, mode or repetition.")
        identities.add(identity)
        arms = {}
        reasons = {}
        for arm in ARMS:
            arms[arm], reason = run_score(task, mode, arm, pair.get(arm))
            if reason:
                reasons[arm] = reason
        if not reasons:
            fork, official = (pair[arm] for arm in ARMS)
            controls = (
                "unity_version",
                "host",
                "model",
                "effort",
                "fixture_digest",
                "permission_digest",
                "prompt_digest",
                "observer_digest",
            )
            for field in controls:
                if fork[field] != official[field]:
                    reasons[field] = "control_mismatch"
            if fork["fixture_run_id"] == official["fixture_run_id"]:
                reasons["fixture_run_id"] = "shared_fixture"
            if fork["independent"]["artifact_digest"] == official["independent"]["artifact_digest"]:
                reasons["artifact_digest"] = "shared_witness"
        result = {
            "task": task,
            "mode": mode,
            "repetition": repetition,
            "scores": None if reasons else arms,
            "unscored_reasons": reasons,
        }
        results.append(result)
        if not reasons:
            eligible.append((pair, result))
            fixture_uses.update(pair[arm]["fixture_run_id"] for arm in ARMS)
            artifact_uses.update(pair[arm]["independent"]["artifact_digest"] for arm in ARMS)
    for pair, result in eligible:
        reasons = result["unscored_reasons"]
        if any(fixture_uses[pair[arm]["fixture_run_id"]] > 1 for arm in ARMS):
            reasons["fixture_run_id"] = "reused_across_pairs"
        if any(artifact_uses[pair[arm]["independent"]["artifact_digest"]] > 1 for arm in ARMS):
            reasons["artifact_digest"] = "reused_across_pairs"
        if reasons:
            result["scores"] = None
    summary = {}
    for mode in MODES:
        paired = [item for item in results if item["mode"] == mode]
        scored = [item["scores"] for item in paired if item["scores"] is not None]
        summary[mode] = {"planned": len(paired), "scored": len(scored), "arms": None}
        if scored:
            arms = {arm: [pair[arm] for pair in scored] for arm in ARMS}
            summary[mode]["arms"] = {
                arm: {
                    "samples": len(runs),
                    "successes": sum(run["independent_pass"] for run in runs),
                    "false_passes": sum(run["false_pass"] for run in runs),
                    "duplicate_writes": sum(run["duplicate_writes"] for run in runs),
                    "mcp_calls": sum(run["mcp_calls"] for run in runs),
                    "shell_calls": sum(run["shell_calls"] for run in runs),
                    "elapsed_seconds_median": median(run["elapsed_seconds"] for run in runs),
                    "token_samples": sum(run["tokens"] is not None for run in runs),
                    "tokens": sum(run["tokens"] for run in runs if run["tokens"] is not None)
                    if any(run["tokens"] is not None for run in runs)
                    else None,
                }
                for arm, runs in arms.items()
            }
    return {"schema_version": 1, "summary": summary, "pairs": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path, help="Private JSON array of paired run receipts")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(json.loads(args.receipts.read_bytes()))
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
