"""Trusted build capture and review material for offline Player pair reproduction."""

import argparse
import json
from pathlib import Path

import click


_DIR = click.Path(path_type=Path, file_okay=False, exists=True)
_FILE = click.Path(path_type=Path, dir_okay=False, exists=True)


def resolve_project_command():
    """Resolve the reviewed project for a standalone pair producer or consumer."""
    from services.verification.pair_project import captured_project, reviewed_project

    parser = argparse.ArgumentParser(description=resolve_project_command.__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--admitted-captures", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--fallback", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.manifest is not None:
        project = reviewed_project(args.manifest, args.sha256, args.fallback, args.output)
    else:
        project = captured_project(args.admitted_captures, args.fallback, args.output)
    click.echo(str(project))


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
