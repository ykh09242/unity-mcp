"""Seal pre-run NUnit budget plans and assess later independent CI receipts on their PR head."""

import argparse
from pathlib import Path

from cli.utils.play_scenario_analysis_io import read_json
from cli.utils.verification_pr_replay import Archives, instant, prepare_plans
from cli.utils.verification_selection import selection_quality
from cli.utils.verification_test_ranking import OID
from prospective_pr import ROLES, digest, stamp, write


def prepare(path, at):
    manifest = read_json(path)
    if (
        manifest.get("schema") != "prospective_nunit_inputs@1"
        or not OID.fullmatch(str(manifest.get("commit")))
        or not manifest.get("pr")
        or not manifest.get("manual_source")
        or type(manifest.get("budget_ms")) is not int
        or manifest["budget_ms"] < 1
    ):
        raise ValueError(
            "Expected a native PR identity, human selection source, exact head and positive budget"
        )
    cutoff = instant(at)
    row = {**manifest, "plan_at": at}
    archives = Archives(path)
    if archives.index_executions([row]):
        raise ValueError("Conflicting timing or execution receipts")
    candidate, ranking, plans = prepare_plans(
        archives, row, cutoff, row["commit"], row["budget_ms"]
    )
    fields = (
        "pr",
        "commit",
        "manual_source",
        "budget_ms",
        "draft",
        "mappings",
        "history",
        "closures",
        "manual_ranking",
    )
    lock = {
        "schema": "prospective_nunit_plan@1",
        "pr": row["pr"],
        "head": row["commit"],
        "plan_at": at,
        "manual_source": row["manual_source"],
        "budget_ms": row["budget_ms"],
        "inputs_sha256": digest({k: row[k] for k in fields}),
        "ranking": ranking,
        "plans": plans,
    }
    return lock, archives, candidate, row


def assess(path, frozen):
    at = frozen["plan_at"]
    lock, archives, candidate, row = prepare(path, at)
    if lock != frozen:
        raise ValueError("Frozen pre-run inputs or selections differ")
    runs = {}
    gaps = {}
    for role in ROLES:
        receipt = row.get(role)
        if receipt is None:
            gaps[role] = "missing execution"
            continue
        try:
            if (
                not instant(at)
                < instant(receipt.get("started_at"))
                <= instant(receipt.get("available_at"))
            ):
                raise ValueError("Execution must start after plan publication")
            run = archives.load(receipt, instant(at), row["commit"], result=True, xml=True)
            if not candidate or not candidate.keys() <= run.fixture.inputs.keys():
                raise ValueError("NUnit execution lacks declared candidate input observations")
            if any(
                run.fixture.inputs[p][1] != value
                for p, value in candidate.items()
                if p in run.fixture.inputs
            ):
                raise ValueError("Candidate input bytes differ from the committed draft")
            expected = (
                run.fixture.expected_cases
                if role == "full"
                else set(lock["plans"][role]["test_names"])
            )
            if (
                run.cases.keys() != expected
                or run.fixture.context != lock["plans"]["automatic"]["context"]
            ):
                raise ValueError("Execution context or planned inventory differs")
            runs[role] = run
        except (ValueError, OSError) as exc:
            gaps[role] = str(exc)
    if len({r.fixture.execution_id for r in runs.values()}) != len(runs):
        raise ValueError("Full and selected NUnit results must be independent executions")
    full = runs.get("full")
    observations = {}
    for role in ROLES[:2]:
        selected = runs.get(role)
        if full is None:
            continue
        if selected is None:
            if (
                not lock["plans"][role]["test_names"]
                and row.get(role) is None
                and all(s in ("Passed", "Failed") for s in full.cases.values())
            ):
                observations[role] = {
                    "missed_failures": sorted(n for n, s in full.cases.items() if s == "Failed"),
                    "caught_failures": [],
                    "additional_failures": [],
                    "measured_seconds_saved": None,
                }
                gaps.pop(role, None)
            continue
        try:
            quality = selection_quality(selected, full, ())
        except ValueError as exc:
            gaps[role] = str(exc)
            continue
        observation = quality["selection"]
        observation["additional_failures"] = sorted(
            n
            for n, state in selected.cases.items()
            if state == "Failed" and full.cases.get(n) == "Passed"
        )
        observations[role] = observation
    paired = len(observations) == 2 and all(
        obs["missed_failures"] is not None for obs in observations.values()
    )
    return {
        "schema": "prospective_nunit_result@1",
        "pr": lock["pr"],
        "head": lock["head"],
        "scope": "instrumented NUnit inventory",
        "state": "paired" if paired else "incomplete",
        "plan_sha256": digest(lock),
        "full_failures": sorted(n for n, s in full.cases.items() if s == "Failed")
        if full
        else None,
        "executions": {
            role: {
                "id": r.fixture.execution_id,
                "seconds": r.seconds,
                "sha256": r.digest,
                "cases": len(r.cases),
            }
            for role, r in runs.items()
        },
        "additional_full_failures_caught_by_automatic": sorted(
            set(observations["automatic"]["caught_failures"])
            - set(observations["manual"]["caught_failures"])
        )
        if paired
        else None,
        "comparisons": observations,
        "gaps": gaps,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("freeze", "assess"):
        command = sub.add_parser(name)
        command.add_argument("inputs", type=Path)
        if name == "assess":
            command.add_argument("plan", type=Path)
        command.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        inputs = read_json(args.inputs)
        if any(inputs.get(r) is not None for r in ROLES):
            raise ValueError("A plan cannot be sealed from inputs containing a candidate outcome")
        result = prepare(args.inputs, stamp())[0]
    else:
        result = assess(args.inputs, read_json(args.plan))
    write(args.output, result)


if __name__ == "__main__":
    main()
