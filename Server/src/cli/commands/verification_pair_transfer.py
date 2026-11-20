"""Trusted build capture and review material for offline Player pair reproduction."""

import json
from pathlib import Path

import click


_DIR = click.Path(path_type=Path, file_okay=False, exists=True)
_FILE = click.Path(path_type=Path, dir_okay=False, exists=True)


@click.command("capture-pair-side")
@click.option("--project", type=_DIR, required=True)
@click.option("--package-checkout", type=_DIR, required=True)
@click.option("--candidate-id", required=True)
@click.option("--closure", type=_FILE, required=True)
@click.option("--output", type=click.Path(path_type=Path, file_okay=False), required=True)
def capture_pair_side(project, package_checkout, candidate_id, closure, output):
    """Freeze a registered native build and its current clean source commits together."""
    from services.verification.pair_transfer import capture_build

    try:
        click.echo(
            json.dumps(capture_build(project, package_checkout, candidate_id, closure, output))
        )
    except (ValueError, OSError, RuntimeError) as error:
        raise click.ClickException(str(error)) from None


@click.command("pack-pair")
@click.option("--project", type=_DIR, required=True)
@click.option(
    "--artifacts", type=_DIR, required=True, help="Directory with baseline and candidate captures."
)
@click.option(
    "--expectations",
    type=_FILE,
    required=True,
    help="Project-owned outcomes and optional shared references.",
)
def pack_pair_command(project, artifacts, expectations):
    """Verify both captures and publish a new pair.json for independent review."""
    from services.verification.pair_transfer import pack_pair

    try:
        click.echo(json.dumps(pack_pair(project, artifacts, expectations)))
    except (ValueError, OSError, RuntimeError) as error:
        raise click.ClickException(str(error)) from None
