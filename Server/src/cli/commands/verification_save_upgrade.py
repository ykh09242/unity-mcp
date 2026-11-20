"""Verify a reviewed native Player save across an upgrade and a fresh restart."""

import json
from pathlib import Path

import click


@click.command("save-upgrade")
@click.option(
    "--project", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
@click.option(
    "--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True
)
@click.option("--sha256", required=True, help="Manifest digest from an independent review.")
@click.option(
    "--artifacts",
    type=click.Path(path_type=Path, file_okay=False),
    help="Trusted pair directory. Defaults to the manifest's parent.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path, file_okay=False),
    required=True,
    help="New directory for the isolated save slot and native evidence.",
)
@click.pass_context
def save_upgrade_candidate(ctx, project, manifest, sha256, artifacts, output):
    """Create a save with the older Player, then verify migration and a fresh restart."""
    from services.verification.reproduce import reproduce_pair

    try:
        report, code = reproduce_pair(
            project, manifest, sha256, artifacts or manifest.parent, output, save_upgrade=True
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from None
    click.echo(json.dumps(report))
    ctx.exit(code)
