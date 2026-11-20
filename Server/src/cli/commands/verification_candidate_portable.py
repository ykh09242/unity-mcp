"""Review and explicitly admit portable Player candidate evidence."""

import json
from pathlib import Path

import click

_PROJECT = click.Path(path_type=Path, file_okay=False, exists=True)
_FILE = click.Path(path_type=Path, dir_okay=False)


@click.command("export")
@click.option("--project", type=_PROJECT, required=True)
@click.option("--candidate-id", required=True)
@click.option("--output", type=_FILE, required=True, help="New proof JSON file.")
def export_candidate(project, candidate_id, output):
    """Export source and build identities from a trusted local registration."""
    from services.verification.portable_candidate import export_candidate_inputs

    try:
        click.echo(json.dumps(export_candidate_inputs(project, candidate_id, output)))
    except (OSError, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from None


def _proof_options(command):
    command = click.option("--project", type=_PROJECT, required=True)(command)
    command = click.option("--proof", type=_FILE, required=True)(command)
    return click.option(
        "--sha256", required=True, help="Expected proof digest from a trusted source."
    )(command)


def _inspect(project, proof, sha256, candidate_id=None):
    from services.verification.portable_candidate import inspect_candidate_inputs

    try:
        click.echo(
            json.dumps(inspect_candidate_inputs(project, proof, sha256, register_as=candidate_id))
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from None


@click.command("verify")
@_proof_options
def verify_candidate(project, proof, sha256):
    """Check pinned evidence against the current checkout's source bytes."""
    _inspect(project, proof, sha256)


@click.command("register")
@click.option("--candidate-id", required=True, help="ID of the operator-provisioned local build.")
@_proof_options
def register_candidate(project, proof, sha256, candidate_id):
    """Verify local sources and the installed Player before creating a local registration."""
    _inspect(project, proof, sha256, candidate_id)
