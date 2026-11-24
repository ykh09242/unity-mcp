"""Blind the first-choice tasks and score a paired family-first replacement decision."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.discovery_evaluation_fixture import load_suite
from tools.experiments.evaluate_family_first import HOLDOUT, HOLDOUT_SHA256
from tools.experiments.check_family_first_compatibility import CONTRACTS

ARMS = ("shipping", "candidate")
PROFILES = ("core", "full")
CELLS = tuple((arm, profile) for profile in PROFILES for arm in ARMS)
BASELINE = "ab35ff1dd53313583ec753990bbe33dbda6cb7a1"


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result

    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def _schedule(requests):
    languages = sorted({r.language for r in requests}, key=lambda v: digest(["language", v]))
    tasks = sorted({r.task for r in requests}, key=lambda v: digest(["task", v]))
    for request in requests:
        offset = languages.index(request.language) + tasks.index(request.task)
        for cell, (arm, profile) in enumerate(CELLS):
            actor = (cell - offset) % len(CELLS)
            key = digest(["family-first-v1", request.query, arm, profile])[:16]
            yield request, arm, profile, actor, key


def prepare(suite, captures):
    if suite.sha256 != HOLDOUT_SHA256:
        raise ValueError("Unexpected held-out corpus")
    records = {}
    wanted = {(r.language, r.task) for r in suite.requests}
    for arm in ARMS:
        capture = captures[arm]
        if (
            capture["corpus"] != suite.sha256
            or capture["arm"] != arm
            or set(capture["profiles"]) != set(PROFILES)
        ):
            raise ValueError(f"Invalid {arm} capture")
        for profile, rows in capture["profiles"].items():
            if len(rows) != len(wanted) or {(r["language"], r["task"]) for r in rows} != wanted:
                raise ValueError(f"Incomplete {arm}/{profile} capture")
            for row in rows:
                groups = row["groups"]
                if len(groups) > 3 or any(not group for group in groups):
                    raise ValueError("Invalid public result group")
                records[row["language"], row["task"], arm, profile] = groups
    packets = [[] for _ in CELLS]
    for r, arm, profile, actor, key in _schedule(suite.requests):
        groups = records[r.language, r.task, arm, profile]
        packets[actor].append({"id": key, "request": r.query, "groups": groups})
    for packet in packets:
        packet.sort(key=lambda case: case["id"])
    return records, packets


def summarize(suite, rows):
    oracle = {(r.language, r.task): r for r in suite.requests}
    metrics = {}
    for row in rows:
        request = oracle[row["language"], row["task"]]
        for arm in ARMS:
            result = row[arm]
            groups = result["groups"]
            top = [routes[0] for routes in groups]
            offered = {route for routes in groups for route in routes}
            decision = result["first_choice"]
            sample = {
                "count": 1,
                "top3": int(bool(request.expected.intersection(top))),
                "first_choice": int(decision in offered and decision in request.expected),
                "opposite_first": int(bool(top) and top[0] in request.wrong_effect),
            }
            counter = metrics.setdefault(row["profile"], {}).setdefault(arm, Counter())
            counter.update(sample)
    return metrics


def passed(metrics):
    return all(
        scores["candidate"]["count"] == scores["shipping"]["count"] > 0
        and scores["candidate"]["top3"] > scores["shipping"]["top3"]
        and scores["candidate"]["first_choice"] > scores["shipping"]["first_choice"]
        and scores["candidate"]["opposite_first"] <= scores["shipping"]["opposite_first"]
        for scores in (metrics[p] for p in PROFILES)
    )


def compatible(checks):
    wanted = {Path(path).stem for path in CONTRACTS}
    old, new = (checks[arm] for arm in ARMS)
    for arm, check in zip(ARMS, (old, new), strict=True):
        if check["arm"] != arm or set(check["suites"]) != wanted:
            raise ValueError("Incomplete compatibility evidence")
    if not old["test_ids_sha256"] or old["test_ids_sha256"] != new["test_ids_sha256"]:
        raise ValueError("The engines ran different compatibility cases")
    for name, suite in old["suites"].items():
        if (
            suite["unsuccessful"]
            or suite["count"] <= 0
            or suite["count"] != new["suites"][name]["count"]
        ):
            raise ValueError("The shipping compatibility control was incomplete or unsuccessful")
    return not any(suite["unsuccessful"] for suite in new["suites"].values())


def report(suite, captures, responses, candidate_revision, checks):
    records, packets = prepare(suite, captures)
    if len(responses) != len(packets):
        raise ValueError("Missing first-choice packet")
    for choices, packet in zip(responses, packets, strict=True):
        if set(choices) != {case["id"] for case in packet} or not all(
            value is None or isinstance(value, str) for value in choices.values()
        ):
            raise ValueError("Invalid or incomplete first-choice submission")
    rows = {}
    for request, arm, profile, actor, key in _schedule(suite.requests):
        label = (request.language, request.task, profile)
        row = rows.setdefault(
            label, {"language": request.language, "task": request.task, "profile": profile}
        )
        groups = records[request.language, request.task, arm, profile]
        row[arm] = {
            "groups": [[r["route"] for r in group] for group in groups],
            "actor": actor,
            "first_choice": responses[actor][key],
        }
    metrics = summarize(suite, rows.values())
    contracts_passed = compatible(checks)
    return {
        "corpus_sha256": suite.sha256,
        "shipping_revision": BASELINE,
        "candidate_revision": candidate_revision,
        "packet_sha256": [digest(packet) for packet in packets],
        "choice_sha256": [digest(choices) for choices in responses],
        "metrics": metrics,
        "heldout_gate_passed": passed(metrics),
        "compatibility": checks,
        "replace": passed(metrics) and contracts_passed,
        "rows": list(rows.values()),
    }


def verify_record(suite, record):
    """Rebuild the assignments, submitted choices and decision from the published rows."""
    if record["corpus_sha256"] != suite.sha256 or record["shipping_revision"] != BASELINE:
        raise ValueError("Unrecognized corpus or baseline")
    rows = record["rows"]
    indexed = {(r["language"], r["task"], r["profile"]): r for r in rows}
    if len(indexed) != len(rows) or len(rows) != len(suite.requests) * len(PROFILES):
        raise ValueError("Incomplete paired results")
    choices = [{} for _ in CELLS]
    for request, arm, profile, actor, key in _schedule(suite.requests):
        result = indexed[request.language, request.task, profile][arm]
        if result["actor"] != actor:
            raise ValueError("Changed actor assignment")
        choices[actor][key] = result["first_choice"]
    if [digest(packet) for packet in choices] != record["choice_sha256"]:
        raise ValueError("Changed first choices")
    metrics = summarize(suite, rows)
    contracts_passed = compatible(record["compatibility"])
    if (
        metrics != record["metrics"]
        or passed(metrics) != record["heldout_gate_passed"]
        or (passed(metrics) and contracts_passed) != record["replace"]
    ):
        raise ValueError("Changed replacement decision")
    return choices


def verify_replay(suite, captures, record, checks):
    """Check the public packet contents against the original choices, without new judgments."""
    choices = verify_record(suite, record)
    rebuilt = report(suite, captures, choices, record["candidate_revision"], checks)
    if rebuilt != record:
        raise ValueError("Public results or blinded packet contents differ from the trial")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("packets", "score", "verify"))
    parser.add_argument(
        "directory", type=Path, help="Directory containing shipping.json and candidate.json."
    )
    parser.add_argument("--candidate-revision")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    suite = load_suite(HOLDOUT)
    captures = {arm: read_json(args.directory / f"{arm}.json") for arm in ARMS}
    if args.command == "packets":
        _, packets = prepare(suite, captures)
        for index, packet in enumerate(packets):
            path = args.directory / f"actor-{index}.json"
            path.write_text(
                json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    elif args.command == "verify":
        if not args.record:
            parser.error("verify requires --record")
        checks = {arm: read_json(args.directory / f"compat-{arm}.json") for arm in ARMS}
        verify_replay(suite, captures, read_json(args.record), checks)
        print("Public packets, first choices and replacement decision match the record.")
    else:
        if not args.candidate_revision or not args.output:
            parser.error("score requires --candidate-revision and --output")
        choices = [read_json(args.directory / f"choice-{i}.json") for i in range(len(CELLS))]
        checks = {arm: read_json(args.directory / f"compat-{arm}.json") for arm in ARMS}
        result = report(suite, captures, choices, args.candidate_revision, checks)
        verify_record(suite, result)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(
            json.dumps({key: result[key] for key in ("metrics", "heldout_gate_passed", "replace")})
        )


if __name__ == "__main__":
    main()
