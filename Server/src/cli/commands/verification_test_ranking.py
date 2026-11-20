"""Derive rankings and replay them using operator-selected archive artifacts."""

from pathlib import Path

import click
from cli.commands.verification_evidence import emit_result
from cli.utils.play_scenario_analysis_io import read_json
from cli.utils.verification_pr_replay import replay
from cli.utils.verification_selection import read_cohort
from cli.utils.verification_test_ranking import propose_ranking


@click.command("budget-rank")
@click.option("--draft", required=True, type=Path, help="Committed base/head Git candidate draft.")
@click.option("--mappings", required=True, type=Path, help="Project check mapping document.")
@click.option("--closure", multiple=True, type=Path, help="Retained scene dependency closure.")
@click.option("--history", multiple=True, type=Path, help="Prior instrumented NUnit XML.")
@click.option("--output", type=Path)
def budget_rank(draft, mappings, closure, history, output):
    """Propose an exact test ranking for budget-plan and show unmapped changes."""

    def analyze():
        if len(closure) > 32 or len(history) > 16:
            raise ValueError("At most 32 scene closures and 16 prior executions are supported")
        ranking = propose_ranking(
            read_json(draft),
            read_json(mappings),
            list(map(read_json, closure)),
            list(map(read_cohort, history)),
        )
        return ranking, 0 if ranking["state"] == "proposed" else 2

    emit_result(analyze, output)


@click.command("budget-replay")
@click.argument("manifest", type=Path)
@click.option("--output", type=Path)
def budget_replay(manifest, output):
    """Compare automatic and manual budget plans against chronological full PR controls."""

    def analyze():
        result = replay(manifest)
        return result, 0 if result["state"] == "measured" else 2

    emit_result(analyze, output)
