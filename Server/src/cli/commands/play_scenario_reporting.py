"""Offline portable report commands."""

from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_html import HtmlReportError, export_html


def register_reporting_commands(group: click.Group) -> None:
    """Keep report export separate from commands that control Unity execution."""

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
