"""Verify sanitized held-out first-turn observations and optionally require perfect outcomes."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

FULL_SEAL = "e9cce7494467d219f8bb36042547a18d336ded8b9e2cb436c9da3496a0fb454d"
PRIMARY_SEAL = "5cefaebed0cfc665a64a0bb928cbdc31fb695d6f903f828436c9b6554e8f46c9"
PROFILES = ("core", "full")
PINS = {
    "package": "658e6070b8f1dabfd2a4ee8ad16cc14f003f7031",
    "runtime": "7e5e1a89739b6859ef789b71e0ca61a080ce8cda",
    "skill": "96ec78009148a1f47aca17c0595faa00712db214",
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return digest(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def is_hash(value, length=64):
    return isinstance(value, str) and bool(re.fullmatch(f"[0-9a-f]{{{length}}}", value))


def schedule(seal):
    observed = {}
    queries = set()
    for case in seal["cases"]:
        original = digest(case["query"].encode("utf-8"))
        require(original not in queries, "An original was duplicated")
        queries.add(original)
        for profile in PROFILES:
            identity = digest((profile + "\0" + original).encode("ascii"))[:16]
            observed[identity] = (case, profile, original)
    return observed


def summary(rows):
    output = {}
    for profile in PROFILES:
        catalog = [row for row in rows if row["profile"] == profile]
        calls = [call for row in catalog for call in row["calls"]]
        counts = {
            "scheduled": len(catalog),
            "prompt_reached_model": sum(row["prompt_reached_model"] for row in catalog),
            "model_turns_started": sum(row["model_turns_started"] for row in catalog),
            "model_turns_completed": sum(row["model_turns_completed"] for row in catalog),
            "completed": sum(row["verdict"] == "completed" for row in catalog),
            "read_completed": sum(
                row["verdict"] == "completed" and row["effect"] == "read" for row in catalog
            ),
            "edit_completed": sum(
                row["verdict"] == "completed" and row["effect"] == "edit" for row in catalog
            ),
            "native_state_observed": sum(row["observed_native_state"] for row in catalog),
            "native_edit_targets_reached": sum(
                row["effect"] == "edit" and row["observed_target_matches"] for row in catalog
            ),
            "absent_host_final": sum(not row["host_final_present"] for row in catalog),
            "incorrect_or_incomplete_host_final": sum(
                row["host_final_present"] and not row["host_final_correct"] for row in catalog
            ),
            "absent_or_incorrect_host_final": sum(
                not row["host_final_present"] or not row["host_final_correct"] for row in catalog
            ),
            "zero_calls_after_initial_inventory": sum(
                not row["calls"] and row["actor_first_tools_list_sha256"] is not None
                for row in catalog
            ),
            "public_calls": len(calls),
            "domain_errors": sum(call["domain_error"] for call in calls),
            "mcp_errors": sum(call["mcp_error"] for call in calls),
            "unsuccessful_public_replies": sum(call["reply"] == "failure" for call in calls),
            "missing_public_replies": sum(call["reply"] == "missing" for call in calls),
            "unsuccessful_tool_replies": sum(
                call["reply"] == "failure" and call["method"] == "tools/call" for call in calls
            ),
            "unsuccessful_resource_replies": sum(
                call["reply"] == "failure" and call["method"] == "resources/read" for call in calls
            ),
            "unreturned_tool_requests": sum(
                call["reply"] == "missing" and call["method"] == "tools/call" for call in calls
            ),
            "unreturned_resource_requests": sum(
                call["reply"] == "missing" and call["method"] == "resources/read" for call in calls
            ),
            "host_errors": sum(len(row["host_errors"]) for row in catalog),
            "host_stderr_error_records": sum(
                event.get("stream") == "stderr" for row in catalog for event in row["host_errors"]
            ),
            "host_router_errors_without_public_request": sum(
                event["class"] == "host_router_without_public_request"
                for row in catalog
                for event in row["host_errors"]
            ),
            "approval_errors": sum(
                event["class"] == "approval" for row in catalog for event in row["host_errors"]
            ),
            "pre_delivery_not_ready": sum(
                row["verdict"] == "commissioning_not_ready" for row in catalog
            ),
            "verdicts": dict(Counter(row["verdict"] for row in catalog)),
        }
        output[profile] = counts
    return output


def verify_row(row, scheduled, catalog):
    case, profile, expected = scheduled
    for key, value in (
        ("task", case["task"]),
        ("effect", case["effect"]),
        ("profile", profile),
        ("original_sha256", expected),
    ):
        require(row[key] == value, f"Scheduled {key} differs for {row['id']}")
    require(type(row["attempt_count"]) is int and 0 <= row["attempt_count"] <= 1, "Repeated case")
    require(type(row["prompt_reached_model"]) is bool, "Unknown prompt delivery")
    if row["prompt_reached_model"]:
        require(
            row["prompt_sha256"] == expected and row["attempt_count"] == 1,
            "Submitted prompt and attempt are unbound",
        )
    calls = row["calls"]
    for index, call in enumerate(calls):
        reply = call["reply"]
        require(
            call["index"] == index
            and isinstance(call["binding"], str)
            and bool(call["binding"])
            and is_hash(call["sha256"]),
            "Invalid public call identity",
        )
        require(reply in {"success", "failure", "missing"}, "Invalid public reply disposition")
        require(call["method"] in {"tools/call", "resources/read"}, "Unrecognized public method")
        require(
            type(call["domain_error"]) is bool and type(call["mcp_error"]) is bool,
            "Invalid public error markers",
        )
        if reply == "failure":
            require(call["domain_error"] or call["mcp_error"], "Unclassified public error")
        else:
            require(not call["domain_error"] and not call["mcp_error"], "Invalid public error")
    if row["verdict"] == "completed":
        require(
            row["prompt_reached_model"]
            and row["model_turns_started"] == row["model_turns_completed"] == 1
            and row["catalog_child_turns_started"] == 0
            and row["host_final_present"]
            and row["host_final_correct"]
            and row["observed_native_state"]
            and row["observed_target_matches"]
            and is_hash(row["answer_sha256"])
            and row["answer_sha256"] != digest(b"")
            and is_hash(row["receipt_sha256"])
            and is_hash(row["before_sha256"])
            and is_hash(row["after_sha256"])
            and row["before_facts"] == case["before"]
            and row["after_facts"] == case["after"]
            and row["protected_changes"] == []
            and row["persisted_scope_valid"] is True,
            "Completion is missing one or more delivery, answer, or native preservation proofs",
        )
        startup = row["startup_inventory"]
        require(
            startup["ready"]
            and startup["expected_catalog_sha256"] == catalog["sha256"]
            and (
                not row["actor_first_tools_list_complete"]
                or row["actor_first_tools_list_sha256"] == catalog["tools_sha256"]
            ),
            "Completed case was not commissioned against this catalog",
        )
        review = row["review"]
        evidence = review["evidence"]
        require(
            review["answer_accurate"] is True
            and review["constraints_respected"] is True
            and review["fingerprint"] == row["fingerprint"]
            and is_hash(row["fingerprint"])
            and evidence
            and len({item["index"] for item in evidence}) == len(evidence),
            "A completed case has no bound semantic review",
        )
        require(
            all(
                type(item["index"]) is int
                and 0 <= item["index"] < len(calls)
                and calls[item["index"]]["reply"] == "success"
                and calls[item["index"]]["sha256"] == item["sha256"]
                for item in evidence
            ),
            "Supporting receipt is not a successful call from this attempt",
        )


def verify(data):
    require(
        data["schema"] == 1 and is_hash(data["candidate_product"], 40), "Invalid release identity"
    )
    require(
        data["candidate_product"] == PINS["package"] and data["product_pins"] == PINS,
        "Frozen package, runtime, or skill revision differs",
    )
    seal = data["primary_seal"]
    require(
        data["whole_preseal_sha256"] == FULL_SEAL
        and data["primary_preseal_sha256"] == PRIMARY_SEAL
        and canonical(seal) == PRIMARY_SEAL,
        "The primary suite differs from its preseal",
    )
    cases = seal["cases"]
    require(
        len(cases) == 8 and all(row["panel"] == "primary" for row in cases), "Wrong primary cohort"
    )
    require(
        len({row["language"] for row in cases}) == 4
        and len({row["family"] for row in cases}) == 4
        and Counter(row["effect"] for row in cases) == {"read": 4, "edit": 4},
        "Primary coverage differs from preseal",
    )
    require(data["reserve_disclosed"] is False, "Untouched reserve leaked into a primary report")
    actor = data["actor"]
    require(
        actor["model"] == seal["actor"]["model"]
        and actor["reasoning_effort"] == seal["actor"]["reasoning"]
        and actor["host"] == "ordinary-codex-exec"
        and is_hash(actor["sha256"]),
        "The actor differs from the presealed first-turn condition",
    )
    prefs = data["shared_preferences"]
    require(
        prefs["before_sha256"] == prefs["after_sha256"]
        and is_hash(prefs["before_sha256"])
        and prefs["private_opt_in_count"] == 1,
        "Shared and permitted private tool preferences differ",
    )
    expected = schedule(seal)
    rows = data["cases"]
    require(len(rows) == 16 and {row["id"] for row in rows} == expected.keys(), "Schedule differs")
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
    for row in rows:
        verify_row(row, expected[row["id"]], catalogs[row["profile"]])
    actual = summary(rows)
    require(actual == data["summary"], "Published totals differ from case observations")
    return actual


def is_perfect(totals):
    return all(
        counts["scheduled"] == counts["prompt_reached_model"] == counts["completed"] == 8
        and counts["model_turns_started"] == counts["model_turns_completed"] == 8
        and all(
            counts[key] == 0
            for key in (
                "domain_errors",
                "mcp_errors",
                "unsuccessful_public_replies",
                "missing_public_replies",
                "host_errors",
                "pre_delivery_not_ready",
            )
        )
        for counts in totals.values()
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("agent_outcome_holdout_results.json"),
    )
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    totals = verify(json.loads(args.report.read_bytes()))
    print(json.dumps(totals, indent=2, sort_keys=True))
    if args.require_perfect and not is_perfect(totals):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
