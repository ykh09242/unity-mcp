"""Portable report export and paired replay commands."""

from pathlib import Path
import subprocess

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_html import HtmlReportError, export_html
from cli.utils.play_scenario_replay import reduce_replay


def register_reporting_commands(group: click.Group) -> None:
    """Register the offline exporter and the explicitly selected native replay workflow."""

    @group.command("reduce-replay")
    @click.option(
        "--broken", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option(
        "--repaired", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option(
        "--candidate-pair",
        nargs=2,
        multiple=True,
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        help="Prepared broken and repaired builds for a candidate. May be repeated.",
    )
    @click.option(
        "--builder",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Project-owned executable for missing candidates, taking definition, broken output and repaired output.",
    )
    @click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--timeout", type=click.IntRange(1, 3600), default=60, show_default=True)
    @click.option("--max-trials", type=click.IntRange(1, 128), default=24, show_default=True)
    @click.option("--graphical", is_flag=True)
    def reduce(broken, repaired, candidate_pair, builder, output, timeout, max_trials, graphical):
        """Replay verified controls and remove steps preserving the first observed state failure."""
        try:
            result = reduce_replay(
                broken,
                repaired,
                candidate_pair,
                output,
                builder=builder,
                graphical=graphical,
                timeout=timeout,
                max_trials=max_trials,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            format_output(
                {
                    "status": result["status"],
                    "stop": result["stop"],
                    "before_steps": len(result["before"]["kept_indices"]),
                    "after_steps": len(result["after"]["kept_indices"]),
                    "report": str(output / "reduction.json"),
                },
                get_config().format,
            )
        )
        if result["stop"] != "single_deletion_fixed_point":
            raise click.exceptions.Exit(2)

    @group.command("export-html")
    @click.argument("source", type=click.Path(path_type=Path))
    @click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--comparison", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--analysis", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--visual", type=click.Path(dir_okay=False, path_type=Path))
    @click.option("--baseline", type=click.Path(file_okay=False, path_type=Path))
    def export(
        source: Path,
        output: Path,
        comparison: Path | None,
        analysis: Path | None,
        visual: Path | None,
        baseline: Path | None,
    ) -> None:
        """Export a run/suite JSON or saved soak directory to one new offline HTML file.

        --baseline adds full compatible soak curves when SOURCE is a soak directory.
        """
        try:
            result = export_html(
                source,
                output,
                comparison=comparison,
                analysis=analysis,
                visual=visual,
                baseline=baseline,
            )
        except HtmlReportError as exc:
            click.echo(
                format_output({"status": "not_exported", "error": str(exc)}, get_config().format)
            )
            raise click.exceptions.Exit(2) from exc
        click.echo(format_output(result, get_config().format))
