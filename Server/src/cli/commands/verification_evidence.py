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
