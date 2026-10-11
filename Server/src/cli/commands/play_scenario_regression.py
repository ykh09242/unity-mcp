"""User-project regression profile execution through the existing Player launchers."""

from pathlib import Path

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.play_scenario_regression import run_regression


@click.command("regression-run")
@click.argument("profile", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path))
def regression_run(profile: Path, output_dir: Path) -> None:
    """Run one explicit profile: exit 0 passed, 1 failed, 2 unavailable, 130 cancelled.

    Output must be new. Build/policy/reference paths are relative to the profile file.
    Every Player requires verified payload provenance. Baselines are never repinned.
    """
    try:
        result = run_regression(profile, output_dir)
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        click.echo(
            format_output({"status": "unavailable", "error": str(exc)[:2048]}, get_config().format)
        )
        raise click.exceptions.Exit(2) from exc
    click.echo(format_output(result.model_dump(mode="json"), get_config().format))
    raise click.exceptions.Exit(result.exit_code)


def register_regression_commands(group: click.Group) -> None:
    group.add_command(regression_run)
