"""Verify the checked-in paired results for the 71 original Unity requests."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_trial as trial


BASELINE = trial.HERE / "live_agent_followup_results.json"
RESULT = trial.HERE / "catalog_outcome_comparison_results.json"
SHIPPING_RESULT = trial.HERE / "catalog_outcome_shipping_results.json"
BASELINE_SHA256 = "2dd7c80483e2fa802f36d92ca56526ef64981d2c42eafaa82bfc21619fc59888"
CANDIDATE = "d1ba9475b5b29016f3744969a5c149673d3cd21d"
SHIPPING = "e3c0c6450b39cf8c5523dfa8bec22f25991f559d"
SHIPPING_SERVER = "5f0466871529c7d209133cb342f357c505c6b87b"


def call_counts(rows):
    summary = {}
    for profile in ("core", "full"):
        calls = [call for row in rows if row["profile"] == profile for call in row["calls"]]
        problems = {}
        for call in calls:
            if call["ok"] is True:
                continue
            binding = call["binding"].split("?", 1)[0]
            binding = re.sub(r"(/scene/gameobject/)-?\d+(?=/|$)", r"\1{id}", binding)
            counts = problems.setdefault(binding, {"false": 0, "no_reply": 0})
            counts["false" if call["ok"] is False else "no_reply"] += 1
        summary[profile] = {
            "total": len(calls),
            "false": sum(call["ok"] is False for call in calls),
            "no_reply": sum(call["ok"] is None for call in calls),
            "failures_by_operation": dict(sorted(problems.items())),
        }
    return summary


def verify(result, baseline, *, expected_product=CANDIDATE):
    old = {case["id"]: case for case in baseline["cases"]}
    schedule = {case["id"]: case for pair in trial.pairs() for case in pair}
    rows = result["cases"]
    if len(rows) != len(schedule) or {row["id"] for row in rows} != schedule.keys():
        raise ValueError("Incomplete or duplicated original request")
    if (
        result["baseline_evidence_sha256"] != BASELINE_SHA256
        or hashlib.sha256(BASELINE.read_bytes()).hexdigest() != BASELINE_SHA256
        or expected_product not in {CANDIDATE, SHIPPING}
        or result["candidate_product"] != expected_product
        or result["baseline_product"] != baseline["product_tree"]
        or result["actor"] != baseline["actor"]
        or result["turn_seconds"] != trial.TURN_SECONDS
    ):
        raise ValueError("The baseline evidence or actor configuration differs")
    for row in rows:
        spec = schedule[row["id"]]
        prior = old[row["id"]]
        if (
            any(row[key] != spec[key] for key in ("profile", "panel", "task", "language", "seed"))
            or (row["delivered"] and row["prompt_sha256"] != prior["prompt_sha256"])
            or row["previous"] != prior["outcome"]
            or row["fixture"] != ("registry" if spec["task"] == "installed_package" else "regular")
        ):
            raise ValueError(f"Original case identity differs for {row['id']}")
    if expected_product == SHIPPING:
        if (
            result["version"] != 2
            or result.get("server_source_pin") != SHIPPING_SERVER
            or "transport_sensitivity" in result
        ):
            raise ValueError("The shipping result must contain the first-attempt cohort only")
        for row in rows:
            attempts = row["attempts"]
            if (
                len(attempts) != 1
                or any(
                    attempts[0].get(key) != row[key] for key in ("status", "seconds", "delivered")
                )
                or row["turn_started"] is not True
                or row["prompt_sha256"] != old[row["id"]]["prompt_sha256"]
                or row["turn_completed"] != (row["status"] == "completed")
            ):
                raise ValueError(f"Expected one recorded shipping attempt for {row['id']}")
        replays = []
    else:
        replays = result["transport_sensitivity"]["cases"]
    for row in [*rows, *replays]:
        if row["outcome"] not in {"pass", "fail", "incomplete", "unobserved"}:
            raise ValueError(f"Unresolved grade for {row['id']}")
        if row["observed"] != (row["outcome"] in {"pass", "fail"}):
            raise ValueError(f"Inconsistent observation for {row['id']}")
        if row["outcome"] == "pass" and not (
            row["delivered"] and row["positive_receipts"] and row["stage"] == "complete"
        ):
            raise ValueError(f"Missing positive evidence for {row['id']}")
        for receipt in row["positive_receipts"]:
            index = receipt["index"]
            if (
                not 0 <= index < len(row["calls"])
                or row["calls"][index]["ok"] is not True
                or not re.fullmatch("[0-9a-f]{64}", receipt["sha256"])
            ):
                raise ValueError(f"Invalid positive call for {row['id']}")
    for profile in ("core", "full"):
        cohort = [row for row in rows if row["profile"] == profile]
        counts = Counter(row["outcome"] for row in cohort)
        transitions = Counter(f"{row['previous']}_to_{row['outcome']}" for row in cohort)
        expected = dict(
            scheduled=71, results=len(cohort), outcomes=counts, paired_changes=transitions
        )
        if result["summary"][profile] != expected:
            raise ValueError(f"Published {profile} summary differs from paired cases")
    if result["public_calls"] != {
        "before": call_counts(baseline["cases"]),
        "after": call_counts(rows),
    }:
        raise ValueError("Public call totals or failure groups differ from per-case records")
    flags = result["native_enablement_preferences"]
    if flags["before_sha256"] != flags["after_sha256"] or flags["terrain_enabled"] is not False:
        raise ValueError("Native tool enablement changed")
    for name, size in (("regular", 4), ("registry", 1)):
        identities = result["fixture_identity"][name]
        if len(identities) != size or any(
            identity["revision"] != result["candidate_product"]
            or identity["unchanged_settings"] != 22
            or identity["unchanged_packages"] != 44
            or identity["manage_terrain"] != identity["manage_terrain_after"]
            or identity["manage_terrain"] != "native_disabled"
            for identity in identities
        ):
            raise ValueError(f"Unexpected {name} fixture identity")
    if expected_product == SHIPPING:
        return
    diagnostic = result["transport_sensitivity"]
    primary = {row["id"]: row for row in rows}
    eligible = sorted(
        row["id"]
        for row in rows
        if (row.get("model_stream") or {}).get("eligibility") == "early_stream_loss"
    )
    if (
        sorted(row["id"] for row in replays) != eligible
        or diagnostic["eligible_ids"] != eligible
        or diagnostic["counts"] != Counter(row["outcome"] for row in replays)
        or diagnostic["fixture_identity"] != result["fixture_identity"]["regular"][0]
    ):
        raise ValueError("Independent transport replay coverage or fixture differs")
    for repeat in replays:
        original = primary[repeat["id"]]
        if (
            original["outcome"] != repeat["primary_outcome"]
            or original["outcome"] not in {"incomplete", "unobserved"}
            or any(original[key] != repeat[key] for key in ("profile", "prompt_sha256", "seed"))
            or repeat["original_stream_evidence"] != original["model_stream"]
            or repeat["outcome"] not in {"pass", "fail", "incomplete", "unobserved"}
        ):
            raise ValueError(f"Invalid diagnostic replay for {repeat['id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shipping", action="store_true", help="Also verify and report the final package cohort."
    )
    args = parser.parse_args()
    baseline = json.loads(BASELINE.read_bytes())
    candidate = json.loads(RESULT.read_bytes())
    verify(candidate, baseline)
    if args.shipping:
        candidate = json.loads(SHIPPING_RESULT.read_bytes())
        verify(candidate, baseline, expected_product=SHIPPING)
    print(json.dumps(candidate["summary"], indent=2))
