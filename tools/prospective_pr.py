"""Freeze and compare independent hosted tools-suite selections on an opening PR head."""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


ROLES = ("manual", "automatic", "full")
WORKFLOW = ".github/workflows/prospective-pr.yml"


def stamp():
    return datetime.now(UTC).isoformat()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.write("\n")


def read(path):
    return json.loads(Path(path).read_bytes())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args]).decode().strip()


def manual_select(body, inventory):
    blocks = re.findall(r"^```prospective-tests\r?\n(.*?)^```\s*$", body, re.M | re.S)
    if len(blocks) != 1:
        return None, "Expected one prospective-tests block in the opening PR description"
    requested = blocks[0].splitlines()
    files = {name.split("::", 1)[0] for name in inventory}
    if len(requested) > 32 or any(
        name not in files and name not in inventory for name in requested
    ):
        return None, "Unknown test file or node ID, or more than 32 selectors"
    return sorted(n for n in inventory if n in requested or n.split("::", 1)[0] in requested), None


def automatic_select(paths, inventory, base, head):
    from cli.utils.verification_test_ranking import propose_ranking

    files = {name.split("::", 1)[0] for name in inventory}
    mappings = {}
    for module in sorted(files):
        mappings[module] = [module]
        filename = Path(module).name
        if filename.startswith("test_"):
            mappings.setdefault("tools/" + filename.removeprefix("test_"), []).append(module)
    # This scope uses Git's committed tree inventory as its source fingerprint.
    # Direct mappings target modules; pytest discovery supplies their exact cases.
    tree = subprocess.check_output(["git", "ls-tree", "-rz", head])
    draft = {
        "kind": "git_candidate_draft",
        "selection": "committed",
        "candidate_commit": head,
        "baseline_commit": base,
        "candidate": {"source_revision": hashlib.sha256(tree).hexdigest()},
        "changed_paths": [{"path": p} for p in paths if p],
    }
    ranking = propose_ranking(
        draft, {"mappings": [{"path": p, "tests": tests} for p, tests in mappings.items()]}, [], []
    )
    selected = {row["name"] for row in ranking["tests"]}
    return sorted(n for n in inventory if n.split("::", 1)[0] in selected), ranking


def freeze(event, inventory):
    pr = event["pull_request"]
    head = pr["head"]["sha"]
    if event["action"] != "opened" or git("rev-parse", "HEAD") != head:
        raise ValueError("Expected checkout of the opening event head")
    if not inventory or len(set(inventory)) != len(inventory):
        raise ValueError("Expected distinct collected tools-suite node IDs")
    paths = git("diff", "--name-only", "-z", f"{pr['base']['sha']}...{head}").split("\0")
    human, missing = manual_select(pr.get("body") or "", inventory)
    if pr["user"].get("type") != "User":
        human, missing = None, "Opening description lacks an attributable human author"
    auto, ranking = automatic_select(paths, inventory, pr["base"]["sha"], head)
    policy = os.environ.get("PROSPECTIVE_POLICY")
    observer = None
    if policy:
        revision = git("-C", policy, "rev-parse", "HEAD")
        if revision != pr["base"]["sha"]:
            raise ValueError("Planning checkout differs from the opening base")
        observer = {
            "revision": revision,
            "blobs": {
                name: git("-C", policy, "hash-object", name)
                for name in ("tools/prospective_pr.py", "tools/prospective_pytest_runner.py")
            },
        }
    return {
        "schema": "prospective_pytest_plan@1",
        "scope": "GitHub hosted tools/tests",
        "repository": pr["base"]["repo"]["full_name"],
        "pr": pr["number"],
        "head": head,
        "base": pr["base"]["sha"],
        "opened_at": pr["created_at"],
        "plan_at": stamp(),
        "run_id": os.environ["GITHUB_RUN_ID"],
        "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "observer": observer,
        "manual_source": {"author": pr["user"]["login"], "event": "pull_request.opened"},
        "manual_gap": missing,
        "automatic_source": {"policy": "budget-rank direct module mappings", "ranking": ranking},
        "selection": {"manual": human, "automatic": auto, "full": sorted(inventory)},
    }


def compare(plan, captures):
    """Keep unrun, skipped, incomplete and divergent observations visible."""
    verified = {}
    gaps = {}
    for role in ROLES:
        row = captures.get(role)
        names = plan["selection"][role]
        if row is None:
            gaps[role] = (
                "missing selection"
                if names is None
                else "missing execution"
                if names
                else "empty selection"
            )
            continue
        if not (
            row["role"] == role
            and row["head"] == plan["head"]
            and row["plan_sha256"] == digest(plan)
            and row["execution_id"] == f"{plan['run_id']}/{plan['attempt']}/{role}"
            and datetime.fromisoformat(row["started_at"]) > datetime.fromisoformat(plan["plan_at"])
            and row["exit"] in (0, 1)
            and names
            and set(row["cases"]) == set(names)
            and math.isfinite(row["seconds"])
            and row["seconds"] >= 0
            and all(state in ("passed", "failed", "skipped") for state in row["cases"].values())
        ):
            gaps[role] = "incomplete inventory, execution, head or plan binding"
            continue
        verified[role] = row
    full = verified.get("full")
    full_failed = {n for n, state in full["cases"].items() if state == "failed"} if full else None
    comparisons = {}
    for role in ROLES[:2]:
        selected = verified.get(role)
        observed = {n for n, s in selected["cases"].items() if s == "failed"} if selected else None
        if plan["selection"][role] == [] and role not in verified and captures.get(role) is None:
            observed = set()
        paired = full_failed is not None and observed is not None
        comparisons[role] = {
            "missed_full_failures": sorted(full_failed - observed) if paired else None,
            "additional_failures": sorted(n for n in observed if full["cases"][n] == "passed")
            if paired
            else None,
            "selected_failures_skipped_by_full": sorted(
                n for n in observed if full["cases"][n] == "skipped"
            )
            if paired
            else None,
            "caught_full_failures": sorted(full_failed & observed) if paired else None,
            "seconds_saved": full["seconds"] - selected["seconds"] if paired and selected else None,
        }
    paired = all(comparisons[r]["missed_full_failures"] is not None for r in ROLES[:2])
    manual_caught = comparisons["manual"]["caught_full_failures"]
    auto_caught = comparisons["automatic"]["caught_full_failures"]
    return {
        "schema": "prospective_pytest_result@1",
        "pr": plan["pr"],
        "head": plan["head"],
        "run_id": plan["run_id"],
        "repository": plan["repository"],
        "scope": plan["scope"],
        "plan_at": plan["plan_at"],
        "plan_sha256": digest(plan),
        "state": "paired" if paired else "incomplete",
        "discovered_cases": len(plan["selection"]["full"]),
        "full_failures": sorted(full_failed) if full else None,
        "gaps": gaps,
        "manual_gap": plan["manual_gap"],
        "comparisons": comparisons,
        "additional_full_failures_caught_by_automatic": sorted(
            set(auto_caught) - set(manual_caught)
        )
        if paired
        else None,
        "executions": {
            r: {
                "id": c["execution_id"],
                "seconds": c["seconds"],
                "cases": len(c["cases"]),
                "skipped": sum(s == "skipped" for s in c["cases"].values()),
            }
            for r, c in verified.items()
        },
    }


def rollout():
    target = git(
        "log", "--first-parent", "--diff-filter=A", "--format=%H", "--", WORKFLOW
    ).splitlines()[-1]
    from prospective_github import activation

    return activation(os.environ["GITHUB_REPOSITORY"], target)


def summarize(path):
    from prospective_github import cohort, paired_report

    repository = os.environ["GITHUB_REPOSITORY"]
    receipt = rollout()
    rows = cohort(repository, receipt["activation_at"])
    for row in rows:
        row["state"] = "incomplete"
        if not row.get("head"):
            continue
        try:
            row.update(paired_report(repository, row))
        except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            row["reason"] = str(exc)
    paired = sum(row["state"] == "paired" for row in rows)
    metrics = {}
    for role in ROLES[:2]:
        measured = [
            r["comparisons"][role]
            for r in rows
            if (r.get("comparisons") or {}).get(role, {}).get("missed_full_failures") is not None
        ]
        times = [r["seconds_saved"] for r in measured if r["seconds_saved"] is not None]
        metrics[role] = {
            "compared_prs": len(measured),
            "timed_prs": len(times),
            "missed_failures": sum(len(r["missed_full_failures"]) for r in measured)
            if measured
            else None,
            "additional_failures": sum(len(r["additional_failures"]) for r in measured)
            if measured
            else None,
            "seconds_saved": sum(times) if times else None,
        }
    write(
        path,
        {
            "schema": "prospective_pr_cohort@1",
            "repository": repository,
            **receipt,
            "target_prs": 3,
            "enrolled_prs": len(rows),
            "awaiting_prs": 3 - len(rows),
            "paired_prs": paired,
            "incomplete_prs": len(rows) - paired,
            "metrics": metrics,
            "prs": rows,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("freeze")
    plan.add_argument("event", type=Path)
    plan.add_argument("inventory", type=Path)
    plan.add_argument("output", type=Path)
    assess = sub.add_parser("assess")
    assess.add_argument("plan", type=Path)
    assess.add_argument("captures", type=Path)
    assess.add_argument("output", type=Path)
    sub.add_parser("cohort").add_argument("output", type=Path)
    sub.add_parser("eligible").add_argument("number", type=int)
    args = parser.parse_args()
    if args.action == "eligible":
        from prospective_github import cohort

        since = rollout()["activation_at"]
        for attempt in range(6):
            rows = cohort(os.environ["GITHUB_REPOSITORY"], since)
            if any(r.get("pr") == args.number and r["original_target"] == "beta" for r in rows):
                print("true")
                return
            if len(rows) == 3 or attempt == 5:
                break
            time.sleep(5)
        print("false")
    elif args.action == "cohort":
        summarize(args.output)
    elif args.action == "freeze":
        write(args.output, freeze(read(args.event), read(args.inventory)))
    else:
        captures = {r: read(p) for r in ROLES if (p := args.captures / f"{r}.json").is_file()}
        write(args.output, compare(read(args.plan), captures))


if __name__ == "__main__":
    main()
