"""Bind independent answer adjudication to the public trace and native observations."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_outcomes as prior
from tools.experiments.tool_outcome_trial import FIELDS, TARGETS, schedule


def classify(row, review):
    before, after = row.get("before", {}), row.get("after", {})
    if not row.get("delivered"):
        return "undelivered"
    if not before or not after or set(before.get("facts", {})) != FIELDS[row["task"]]:
        return "unverifiable"
    if row.get("other_integrations"):
        return "unverifiable"
    expected = dict(before["facts"])
    change = TARGETS.get(row["task"])
    if change:
        field, value = change
        if expected[field] == value:
            return "unverifiable"
        expected[field] = value
    if after.get("facts") != expected:
        return "incomplete_native_target_or_facts"
    if any(
        before.get(key) != after.get(key) for key in before.keys() | after.keys() if key != "facts"
    ):
        return "incomplete_native_preservation"
    if not review or review.get("fingerprint") != prior._review_key(row):
        return "awaiting_review"
    if not review["answer_accurate"]:
        return "incomplete_answer"
    evidence = review["evidence"]
    if not evidence:
        return "incomplete_evidence"
    if not all(
        type(i) is int and 0 <= i < len(row["calls"]) and prior._eligible(row["calls"][i])
        for i in evidence
    ):
        raise ValueError("Invalid supporting observation for " + row["id"])
    return "completed"


def summarize(root, reviews):
    results = []
    for pair in schedule():
        for original in pair:
            case = original["id"]
            path = root / "receipts" / case / "row.json"
            if not path.exists():
                results.append(original | {"outcome": "missing_receipt"})
                continue
            data = path.read_bytes()
            row = json.loads(data)
            if any(original[k] != row[k] for k in original):
                raise ValueError("Receipt differs from sealed assignment")
            review = reviews.get(case)
            results.append(
                original
                | {
                    "outcome": classify(row, review),
                    "answer": row.get("answer"),
                    "status": row["status"],
                    "expected": row.get("before", {}).get("facts"),
                    "observed": row.get("after", {}).get("facts"),
                    "receipt_sha256": hashlib.sha256(data).hexdigest(),
                    "fingerprint": prior._review_key(row),
                    "review": review,
                    "changed_fields": [
                        k
                        for k in row.get("before", {})
                        if row["before"][k] != row.get("after", {}).get(k)
                    ],
                    "calls": [
                        {"index": i, "name": call["name"], "success": call["success"]}
                        for i, call in enumerate(row["calls"])
                    ],
                }
            )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = summarize(args.root, json.loads(args.reviews.read_bytes()) if args.reviews else {})
    data = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8", newline="\n")
    else:
        print(data)


if __name__ == "__main__":
    main()
