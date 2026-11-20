"""Collect hosted pytest observations and replay the published historical PR inventory."""

import argparse
import json
import os
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import TypedDict
from xml.etree import ElementTree


class Observation(TypedDict):
    name: str
    seconds: float
    outcome: str
    reason: str | None
    properties: dict[str, str]


def read_junit(path: Path) -> tuple[list[Observation], float]:
    if not path.is_file():
        return [], 0.0
    root = ElementTree.parse(path).getroot()
    cases: list[Observation] = []
    for row in root.iter("testcase"):
        detail = next(
            (child for child in row if child.tag in {"failure", "error", "skipped"}), None
        )
        cases.append(
            {
                "name": f"{row.attrib['classname']}::{row.attrib['name']}",
                "seconds": float(row.attrib["time"]),
                "outcome": detail.tag if detail is not None else "passed",
                "reason": detail.get("message") if detail is not None else None,
                "properties": {
                    p.attrib["name"]: p.attrib["value"]
                    for p in row.findall("./properties/property")
                },
            }
        )
    return cases, sum(float(suite.attrib["time"]) for suite in root.iter("testsuite"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("cli", type=Path)
    args = parser.parse_args()
    evidence = args.evidence
    env = os.environ
    inventory = Path("docs/development/verification-pr-inventory.json")
    inventory_content = inventory.read_bytes()
    result = evidence / "historical-replay.json"
    (evidence / "historical-inventory.json").write_bytes(inventory_content)
    command = [
        str(args.cli.resolve()),
        "--no-project-context",
        "--format",
        "json",
        "verification-evidence",
        "budget-replay",
        str(inventory),
        "--output",
        str(result),
    ]
    started = perf_counter()
    with (
        (evidence / "historical-stdout.txt").open("wb") as stdout,
        (evidence / "historical-stderr.txt").open("wb") as stderr,
    ):
        replay = subprocess.run(command, stdout=stdout, stderr=stderr, check=False, timeout=45)
    replay_seconds = round(perf_counter() - started, 6)
    historical = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
    candidate = (evidence / "candidate-sha.txt").read_text(encoding="utf-8").strip()

    cases, suite_seconds = read_junit(evidence / "pytest-junit.xml")
    outcomes = Counter(case["outcome"] for case in cases)
    receipts = Counter(key for case in cases for key in case["properties"])
    expected_receipts = {
        "uninterrupted_inventory_sha256": 1,
        "resumed_inventory_sha256": 1,
        "dependency_diff_observation": 1,
        "merge_observation": 2,
        "checkout_observation": 3,
        "paired_replay_observation": 1,
    }
    fixture_outcome = env["FIXTURE_OUTCOME"]
    fixtures_ok = (
        fixture_outcome == "success"
        and len(cases) > 0
        and outcomes["passed"] == len(cases)
        and all(receipts[key] == count for key, count in expected_receipts.items())
    )
    historical_prs = historical.get("prs", [])
    historical_modes = historical.get("summary", {})
    history_ok = (
        replay.returncode == 2
        and historical.get("schema") == "verification_pr_replay_result@1"
        and historical.get("cohort_kind") == "historical"
        and historical.get("state") == "incomplete"
        and historical.get("total_prs") == 3
        and historical.get("paired_prs") == 0
        and historical.get("unpaired_prs") == 3
        and len(historical_prs) == 3
        and all(
            row["state"] == "unavailable" and "full" in row["missing"] for row in historical_prs
        )
        and all(
            historical_modes[mode]["timed_prs"] == 0
            and historical_modes[mode]["seconds_saved"] is None
            for mode in ("automatic", "manual")
        )
    )
    record = {
        "schema": "hosted_change_review_observations@1",
        "candidate_sha": candidate,
        "event_sha": env["GITHUB_SHA"],
        "event": env["GITHUB_EVENT_NAME"],
        "repository": env["GITHUB_REPOSITORY"],
        "run_id": env["GITHUB_RUN_ID"],
        "run_attempt": env["GITHUB_RUN_ATTEMPT"],
        "fixtures": {
            "source": "generated graph pages, Git repositories and NUnit captures",
            "step_outcome": fixture_outcome,
            "total_cases": len(cases),
            "outcomes": outcomes,
            "seconds": suite_seconds,
            "expected_receipts": expected_receipts,
            "observed_receipts": {key: receipts[key] for key in expected_receipts},
            "state": "matched" if fixtures_ok else "failed",
            "cases": cases,
        },
        "historical_replay": {
            "inventory_sha256": sha256(inventory_content).hexdigest(),
            "exit_code": replay.returncode,
            "seconds": replay_seconds,
            "total_prs": historical.get("total_prs"),
            "paired_prs": historical.get("paired_prs"),
            "unpaired_prs": historical.get("unpaired_prs"),
            "state": "unavailable" if history_ok else "unexpected_result",
        },
    }
    (evidence / "observations.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    with Path(env["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        print(f"## Change review observations for `{candidate}`", file=summary)
        print(
            f"\n- Generated fixtures: {outcomes['passed']}/{len(cases)} passed in {suite_seconds:.3f}s.",
            file=summary,
        )
        print(
            f"- Historical replay: {historical.get('paired_prs')}/{historical.get('total_prs')} paired in {replay_seconds:.3f}s (exit {replay.returncode}).",
            file=summary,
        )
        if history_ok:
            print(
                "- The three historical PRs lack retained independent full-run controls.",
                file=summary,
            )
        else:
            print(
                "- Unexpected historical replay result. Open the retained CLI output and observations.",
                file=summary,
            )
        if not fixtures_ok:
            print(
                "- Fixture execution or expected receipts failed. Open the retained pytest JUnit report.",
                file=summary,
            )
    return 0 if candidate == env["GITHUB_SHA"] and fixtures_ok and history_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
