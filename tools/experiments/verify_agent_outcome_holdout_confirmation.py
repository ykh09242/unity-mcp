"""Verify the ordinary-client holdout repeat and sealed reserve confirmation."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.verify_agent_outcome_holdout import (
    FULL_SEAL,
    PROFILES,
    canonical,
    digest,
    is_hash,
    require,
    schedule,
    summary,
    verify_row,
)
from tools.experiments.verify_agent_outcome_holdout import (
    PINS as FIRST_PINS,
)
from tools.experiments.verify_agent_outcome_holdout import (
    verify as verify_first,
)

BASELINE_SHA256 = "7e7fff5ea87167f19a1439ac90969cbd4d6dc544bc9f795b03a27d623352ce8b"
RESERVE_SPEC_SHA256 = "998c8da6bc941f2e91d07552969efa1291c21c5c4687f747024faf5996ab2a40"
CORRECTION_PINS = {
    "package": "e599e7bae0eed7ba7a24b9666a38002d10abd8c6",
    "runtime": "e221c9366dc87e6053f48d6d36f2239b50f2f452",
    "skill": "96ec78009148a1f47aca17c0595faa00712db214",
}
STRICT_ZERO = (
    "domain_errors",
    "mcp_errors",
    "unsuccessful_public_replies",
    "missing_public_replies",
    "host_errors",
    "pre_delivery_not_ready",
)


def baseline(path):
    raw = path.read_bytes()
    require(digest(raw) == BASELINE_SHA256, "The immutable primary result changed")
    first = json.loads(raw)
    verify_first(first)
    return first


def reserve_schedule(specs):
    require(
        canonical(specs) == RESERVE_SPEC_SHA256,
        "The private reserve schedule differs from its pre-existing seal projection",
    )
    require(len(specs) == 4, "Wrong reserve cohort size")
    require(Counter(case["effect"] for case in specs) == {"read": 2, "edit": 2}, "Reserve split")
    expected = {}
    for spec in specs:
        original = spec["original_sha256"]
        require(is_hash(original), "Invalid sealed original digest")
        for profile in PROFILES:
            key = digest((profile + "\0" + original).encode("ascii"))[:16]
            require(key not in expected, "Duplicate reserve assignment")
            expected[key] = (spec, profile, original)
    return expected


def transitions(first, repeat):
    earlier = {row["id"]: row for row in first}
    labels = {
        (True, True): "completed_both",
        (True, False): "completed_first_only",
        (False, True): "completed_repeat_only",
        (False, False): "incomplete_both",
    }
    result = {}
    for profile in PROFILES:
        counts = dict.fromkeys(labels.values(), 0)
        for row in repeat:
            if row["profile"] == profile:
                prior = earlier[row["id"]]
                change = (
                    prior["verdict"] == "completed",
                    row["verdict"] == "completed",
                )
                counts[labels[change]] += 1
        result[profile] = counts
    return result


def get_catalogs(data):
    catalogs = data["catalogs"]
    require(
        set(catalogs) == set(PROFILES)
        and catalogs["core"]["counts"]["tools"] < catalogs["full"]["counts"]["tools"]
        and all(
            is_hash(catalog["sha256"]) and is_hash(catalog["tools_sha256"])
            for catalog in catalogs.values()
        ),
        "Catalog identities are invalid or indistinguishable",
    )
    return catalogs


def verify_panel(panel, expected, catalogs):
    rows = panel["cases"]
    require(len(rows) == len(expected), "Scheduled slots missing or duplicated")
    require({row["id"] for row in rows} == expected.keys(), "The panel schedule differs")
    for row in rows:
        require(
            row["model_turns_started"] in {0, 1}
            and row["model_turns_completed"] in {0, 1}
            and row["catalog_child_turns_started"] == 0,
            "A slot used more than the single original-only ordinary turn",
        )
        verify_row(row, expected[row["id"]], catalogs[row["profile"]])
    totals = summary(rows)
    require(totals == panel["summary"], "Published totals differ from the scheduled panel")
    return totals


def verify(data, first):
    require(data["schema"] == 1, "Invalid confirmation schema")
    require(data["first_primary_artifact_sha256"] == BASELINE_SHA256, "Missing immutable baseline")
    require(data["whole_preseal_sha256"] == FULL_SEAL, "Whole preseal differs")
    require(data["reserve_spec_sha256"] == RESERVE_SPEC_SHA256, "Missing reserve projection")
    pins = data["product_pins"]
    require(
        set(pins) == set(FIRST_PINS)
        and all(is_hash(value, 40) for value in pins.values())
        and pins["package"] != FIRST_PINS["package"]
        and pins["runtime"] != FIRST_PINS["runtime"],
        "Expected the corrected package and runtime",
    )
    require(
        pins == CORRECTION_PINS,
        "Corrected immutable revisions differ from the authorized revisions",
    )
    require(data["actor"] == first["actor"], "Actor executable or configuration differs")
    require(data["unity_editor"] == first["unity_editor"], "The Editor version differs")
    require(
        data["native_observer_sha256"] == first["native_observer_sha256"],
        "Native observer bytes differ",
    )
    prefs = data["shared_preferences"]
    require(
        prefs["before_sha256"] == prefs["after_sha256"]
        and is_hash(prefs["before_sha256"])
        and prefs["private_opt_in_count"] == first["shared_preferences"]["private_opt_in_count"],
        "Shared preferences drifted during the run or the private tool scope changed",
    )
    catalogs = get_catalogs(data)
    panels = data["panels"]
    require(set(panels) == {"primary_repeat", "reserve_confirmation"}, "Panels differ")
    repeated = schedule(first["primary_seal"])
    reserve = reserve_schedule(data["reserve_spec"])
    require(set(repeated).isdisjoint(reserve), "Repeat and reserve schedules overlap")
    totals = {
        "primary_repeat": verify_panel(panels["primary_repeat"], repeated, catalogs),
        "reserve_confirmation": verify_panel(panels["reserve_confirmation"], reserve, catalogs),
    }
    require(
        transitions(first["cases"], panels["primary_repeat"]["cases"])
        == data["paired_primary_transitions"],
        "The matched earlier-to-corrected transitions differ",
    )
    return totals


def is_perfect(totals):
    return all(
        count["scheduled"] == count["prompt_reached_model"] == count["completed"] == size
        and count["model_turns_started"] == count["model_turns_completed"] == size
        and all(count[key] == 0 for key in STRICT_ZERO)
        for panel, size in (("primary_repeat", 8), ("reserve_confirmation", 4))
        for count in totals[panel].values()
    )


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        default=directory / "agent_outcome_holdout_confirmation.json",
    )
    parser.add_argument(
        "--baseline", type=Path, default=directory / "agent_outcome_holdout_results.json"
    )
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.report.read_bytes())
    totals = verify(data, baseline(args.baseline))
    print(json.dumps(totals, indent=2, sort_keys=True))
    if args.require_perfect and not is_perfect(totals):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
