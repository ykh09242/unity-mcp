"""Qualify one planted fixture fault against independent, preserved NUnit runs."""

import re
from dataclasses import dataclass

from cli.utils.verification_selection import Cohort, selection_quality

_MUTATION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


@dataclass(frozen=True, slots=True)
class MutationTrial:
    mutation_id: str
    mutated_asset: str
    witness_test: str
    baseline: Cohort
    selected: Cohort
    full: Cohort
    witness_baseline: Cohort
    witness_mutant: Cohort


def _observed(run: Cohort) -> dict:
    return {
        "artifact_sha256": run.digest,
        "execution_id": run.fixture.execution_id,
        "duration_seconds": run.seconds,
        "cases": dict(sorted(run.cases.items())),
    }


def assess_mutation(trial: MutationTrial) -> dict:
    """Distinguish selected escapes, full escapes and unresolved mutation controls."""
    if not _MUTATION_ID.fullmatch(trial.mutation_id):
        raise ValueError("Mutation ID must be a lowercase name with optional digits and hyphens")

    baseline, full, selected = trial.baseline, trial.full, trial.selected
    witness_baseline, witness_mutant = trial.witness_baseline, trial.witness_mutant
    runs = (baseline, full, selected, witness_baseline, witness_mutant)
    if len({run.fixture.execution_id for run in runs}) != len(runs):
        raise ValueError("Mutation controls must be independent NUnit executions")
    if len({run.fixture.context for run in runs}) != 1:
        raise ValueError("Mutation executions differ in context")

    expected = baseline.fixture.expected_cases
    if expected != full.fixture.expected_cases or expected != selected.fixture.expected_cases:
        raise ValueError("Mutation executions differ in suite inventory")
    witness_inventory = witness_baseline.fixture.expected_cases
    if (
        witness_inventory != witness_mutant.fixture.expected_cases
        or trial.witness_test not in witness_inventory
        or expected & witness_inventory
    ):
        raise ValueError("Specify an independent witness outside the measured suite inventory")

    clean_inputs, mutant_inputs = baseline.fixture.inputs, full.fixture.inputs
    if (
        witness_baseline.fixture.inputs != clean_inputs
        or selected.fixture.inputs != mutant_inputs
        or witness_mutant.fixture.inputs != mutant_inputs
    ):
        raise ValueError("Matched mutation controls disagree on observed inputs")
    if {name: item[0] for name, item in clean_inputs.items()} != {
        name: item[0] for name, item in mutant_inputs.items()
    }:
        raise ValueError("The planted mutation requires stable observed asset paths and GUIDs")
    changed = {name for name, item in clean_inputs.items() if mutant_inputs[name] != item}
    if changed != {trial.mutated_asset}:
        raise ValueError("Expected exactly the declared fixture asset to change")

    marker = "MCP_MUTATION_WITNESS " + trial.mutation_id

    def caught(run: Cohort) -> set[str]:
        return {
            name
            for name, outcome in run.cases.items()
            if outcome == "Failed"
            and any(
                line.strip() == marker for line in run.failure_messages.get(name, "").splitlines()
            )
        }

    comparison = selection_quality(selected, full, ())
    full_caught, selected_caught = caught(full), caught(selected)
    unmatched_failures = [
        {"cohort": role, "test": name}
        for role, run, relevant in (
            ("full", full, full_caught),
            ("selected", selected, selected_caught),
        )
        for name, outcome in sorted(run.cases.items())
        if outcome == "Failed" and name not in relevant
    ]
    issues = []
    if baseline.cases.keys() != expected or set(baseline.cases.values()) != {"Passed"}:
        issues.append("clean_full_control_not_passing")
    if witness_baseline.cases != {trial.witness_test: "Passed"}:
        issues.append("clean_witness_not_passing")
    if witness_mutant.cases != {trial.witness_test: "Failed"} or caught(witness_mutant) != {
        trial.witness_test
    }:
        issues.append("planted_fault_not_witnessed")
    if comparison["state"] != "measured":
        issues.append("selection_or_full_control_unresolved")
    if unmatched_failures:
        issues.append("unattributed_suite_failures")

    status = "incomplete"
    if not issues:
        if not full_caught:
            status = "missed_by_full"
        elif not selected_caught:
            status = "missed_by_selection"
        else:
            status = "caught_by_selection"

    return {
        "schema": "verification_mutation_assessment@1",
        "state": status,
        "mutation_id": trial.mutation_id,
        "context": full.fixture.context,
        "changed_asset": {
            "path": trial.mutated_asset,
            "guid": clean_inputs[trial.mutated_asset][0],
            "baseline_sha256": clean_inputs[trial.mutated_asset][1],
            "mutant_sha256": mutant_inputs[trial.mutated_asset][1],
        },
        "baseline": _observed(baseline),
        "witness_baseline": _observed(witness_baseline),
        "witness_mutant": _observed(witness_mutant),
        "selected": {
            **_observed(selected),
            "caught_tests": sorted(selected_caught),
            "unresolved_checks": comparison["selection"]["unresolved_checks"],
            "divergent_results": comparison["selection"]["divergent_results"],
        },
        "full": {
            **_observed(full),
            "caught_tests": sorted(full_caught),
            "unresolved_checks": comparison["full"]["unresolved_checks"],
        },
        "unmatched_failures": unmatched_failures,
        "unresolved_controls": issues,
        "missed_witnessing_full_tests": sorted(full_caught - selected_caught)
        if not issues
        else None,
    }
