"""Inspect selected retained verification evidence offline."""

import json
from pathlib import Path

import click
from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_analysis_io import write_new
from cli.utils.verification_selection import read_cohort, selection_quality

FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
OUTPUT = click.Path(dir_okay=False, path_type=Path)


@click.group("verification-evidence")
def verification_evidence():
    """Compare retained results and their input identities without starting Unity."""


def emit_result(action, output):
    try:
        result, code = action()
        if output:
            write_new(output, json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
    except (OSError, ValueError) as exc:
        result, code = {"state": "incomplete", "error": str(exc)}, 2
    click.echo(format_output(result, get_config().format))
    raise click.exceptions.Exit(code)


@verification_evidence.command("selection")
@click.option("--selected", type=FILE, required=True, help="Narrow Unity NUnit XML.")
@click.option("--full", type=FILE, required=True, help="Independently executed full cohort.")
@click.option("--fallback", type=FILE, help="Independently executed broader fallback.")
@click.option("--affected-test", multiple=True, help="Known affected test in the fault fixture.")
@click.option("--output", type=OUTPUT)
def selection(selected, full, fallback, affected_test, output):
    """Measure missed regressions, unnecessary checks and observed elapsed time."""

    def analyze():
        result = selection_quality(
            read_cohort(selected),
            read_cohort(full),
            affected_test,
            read_cohort(fallback) if fallback else None,
        )
        return result, 0 if result["state"] == "measured" else 2

    emit_result(analyze, output)


@verification_evidence.command("compare")
@click.option("--baseline", type=FILE, nargs=2, required=True, metavar="VERIFICATION NATIVE_RUN")
@click.option("--candidate", type=FILE, nargs=2, required=True, metavar="VERIFICATION NATIVE_RUN")
@click.option("--output", type=OUTPUT)
def compare(baseline, candidate, output):
    """Classify new, existing, repaired and unexecuted checks on the reviewed PR pair."""
    from cli.utils.verification_change import compare_runs, read_run

    def analyze():
        result = compare_runs(read_run(*baseline), read_run(*candidate))
        return result, {"no_new_failures": 0, "regressed": 1}.get(result["state"], 2)

    emit_result(analyze, output)


@verification_evidence.command("first-failure")
@click.argument("history_manifest", type=FILE)
@click.option(
    "--kind",
    type=click.Choice(["scenario", "test", "profile", "observation", "stage"]),
    required=True,
)
@click.option(
    "--id", "check_id", required=True, help="Exact named check from the verification reports."
)
@click.option("--output", type=OUTPUT)
def first_failure(history_manifest, kind, check_id, output):
    """Locate the first failing archived build, or report observed bounds and missing trials."""
    from cli.utils.verification_history import inspect_history

    def analyze():
        result = inspect_history(history_manifest, kind, check_id)
        return result, {"no_observed_failure": 0, "first_failure": 1}.get(result["state"], 2)

    emit_result(analyze, output)
