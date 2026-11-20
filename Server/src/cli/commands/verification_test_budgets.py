"""Plan time-boxed checks and assess independent fault controls offline."""

from pathlib import Path

import click
from cli.utils.verification_mutation import MutationTrial, assess_mutation
from cli.utils.verification_selection import read_cohort
from cli.utils.verification_test_budget import (
    MAX_HISTORY,
    BudgetInputs,
    assess_budget,
    plan_budget,
    read_ranking,
)

FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
OUTPUT = click.Path(dir_okay=False, path_type=Path)


def _plan(ranking, history, budget_ms):
    if len(history) > MAX_HISTORY:
        raise ValueError(f"At most {MAX_HISTORY} independent timing artifacts are supported")
    return plan_budget(
        BudgetInputs(read_ranking(ranking), tuple(map(read_cohort, history)), budget_ms)
    )


@click.command("budget-plan")
@click.option("--ranking", type=FILE, required=True, help="Ordered test names and impact reasons.")
@click.option("--history", type=FILE, multiple=True, required=True, help="Prior Unity NUnit XML.")
@click.option("--budget-ms", type=click.IntRange(min=1), required=True)
@click.option("--output", type=OUTPUT)
def budget_plan(ranking, history, budget_ms, output):
    """Select ranked tests that fit an estimate derived from observed history."""
    from cli.commands.verification_evidence import emit_result

    def analyze():
        result = _plan(ranking, history, budget_ms)
        return result, 0 if result["state"] == "ready" else 2

    emit_result(analyze, output)


@click.command("budget-assess")
@click.option("--ranking", type=FILE, required=True, help="Original ranked test list.")
@click.option("--history", type=FILE, multiple=True, required=True, help="Original timing XML.")
@click.option("--budget-ms", type=click.IntRange(min=1), required=True)
@click.option("--selected", type=FILE, help="Independent run of exactly the planned test names.")
@click.option(
    "--full", type=FILE, required=True, help="Independent run of the full test inventory."
)
@click.option("--output", type=OUTPUT)
def budget_assess(ranking, history, budget_ms, selected, full, output):
    """Measure elapsed time and missed failures against an independent full run."""
    from cli.commands.verification_evidence import emit_result

    def analyze():
        result = assess_budget(
            _plan(ranking, history, budget_ms),
            read_cohort(full),
            read_cohort(selected) if selected else None,
        )
        return result, 0 if result["state"] == "measured" else 2

    emit_result(analyze, output)


@click.command("mutation")
@click.option("--mutation-id", required=True, help="Expected native fault witness marker ID.")
@click.option("--mutated-asset", required=True, help="Exact fixture asset deliberately changed.")
@click.option("--baseline", type=FILE, required=True, help="Clean full-suite NUnit XML.")
@click.option("--selected", type=FILE, required=True, help="Mutated selected-suite NUnit XML.")
@click.option("--full", type=FILE, required=True, help="Mutated full-suite NUnit XML.")
@click.option(
    "--witness-test", required=True, help="Exact test from the independent witness fixture."
)
@click.option(
    "--witness",
    type=FILE,
    nargs=2,
    required=True,
    metavar="CLEAN_XML MUTANT_XML",
    help="Independent clean and mutated executions of only the witness test.",
)
@click.option("--output", type=OUTPUT)
def mutation(mutation_id, mutated_asset, baseline, selected, full, witness_test, witness, output):
    """Measure which suite catches a fault witnessed independently in NUnit."""
    from cli.commands.verification_evidence import emit_result

    def analyze():
        result = assess_mutation(
            MutationTrial(
                mutation_id=mutation_id,
                mutated_asset=mutated_asset,
                witness_test=witness_test,
                baseline=read_cohort(baseline),
                selected=read_cohort(selected),
                full=read_cohort(full),
                witness_baseline=read_cohort(witness[0]),
                witness_mutant=read_cohort(witness[1]),
            )
        )
        return result, {
            "caught_by_selection": 0,
            "missed_by_selection": 1,
            "missed_by_full": 1,
        }.get(result["state"], 2)

    emit_result(analyze, output)
