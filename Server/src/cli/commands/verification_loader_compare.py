"""Compare static impact and native Player loader hooks before and after a branch."""

from pathlib import Path
import json

import click


@click.command("compare-loads")
@click.option(
    "--project", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True
)
@click.option(
    "--registration", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
@click.option(
    "--before", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True
)
@click.option(
    "--after", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True
)
def loader_compare(project, registration, before, after):
    """Compare the native job directories of two runs of the same registered build."""
    from services.verification.loader_compare import compare_player_loads

    try:
        comparison = compare_player_loads(project, registration, before, after)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None
    click.echo(json.dumps(comparison, ensure_ascii=False))
