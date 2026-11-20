"""Compare explicitly selected saved pointer traces without an Editor connection."""

import json
from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_analysis_io import write_new
from cli.utils.play_scenario_pointer import compare_pointer_traces


@click.command("compare-pointer-traces")
@click.argument("baseline", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("candidate", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def compare_saved_pointer_traces(baseline: Path, candidate: Path, output: Path | None) -> None:
    """Compare a click in two saved traces. Exit 0 same, 1 diverged, 2 incomplete."""
    try:
        result = compare_pointer_traces(baseline, candidate)
        if output is not None:
            write_new(output, json.dumps(result, indent=2).encode("utf-8"))
    except (OSError, ValueError) as exc:
        result = {
            "schema_version": 1,
            "status": "incomplete",
            "reason": "invalid_evidence_or_output",
            "first_divergence": None,
            "error": str(exc),
        }
    click.echo(format_output(result, get_config().format))
    raise click.exceptions.Exit({"same": 0, "diverged": 1, "incomplete": 2}[result["status"]])


def register_pointer_commands(group: click.Group) -> None:
    """Attach the offline pointer comparison to play-scenario."""
    group.add_command(compare_saved_pointer_traces)
