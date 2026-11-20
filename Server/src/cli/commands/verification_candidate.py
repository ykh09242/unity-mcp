"""Offline operator commands for reviewing verification inputs."""

import json
from pathlib import Path

import click

from cli.commands.verification_candidate_portable import (
    export_candidate,
    register_candidate,
    verify_candidate,
)
from cli.commands.verification_dependency_closure import dependency_closure
from cli.commands.verification_input_chunks import candidate_directory, input_chunks
from cli.commands.verification_merge import anticipate_merge
from cli.commands.verification_reproduce import reproduce_candidate
from cli.utils.play_scenario_analysis_io import safe_local_path, write_new


@click.group("verification-candidate")
def verification_candidate():
    """Review source evidence and manage local Player candidate registrations."""


verification_candidate.add_command(input_chunks)
verification_candidate.add_command(export_candidate)
verification_candidate.add_command(verify_candidate)
verification_candidate.add_command(register_candidate)
verification_candidate.add_command(dependency_closure)
verification_candidate.add_command(reproduce_candidate)
verification_candidate.add_command(anticipate_merge)


@verification_candidate.command("draft")
@click.option("--project", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--baseline", default="HEAD", show_default=True, help="Git commit or revision.")
@click.option(
    "--selection", type=click.Choice(["working-tree", "staged", "committed"]), required=True
)
@click.option("--candidate-ref", help="Head commit or ref for a committed comparison.")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), help="New JSON file.")
@click.option(
    "--chunk-directory",
    type=click.Path(path_type=Path, file_okay=False),
    help="Parent of input-chunks. Defaults to the output file's directory.",
)
def draft(project, baseline, selection, candidate_ref, output, chunk_directory):
    """Compare selected Unity source bytes with a Git baseline and emit a reviewable JSON draft."""
    from services.verification.git_draft import git_draft

    try:
        if output is not None:
            target = safe_local_path(output)
            if target.exists():
                raise FileExistsError("candidate_draft_output_already_exists")
            if chunk_directory is None:
                chunk_directory = target.parent
        document = git_draft(
            project,
            baseline,
            selection,
            candidate_ref=candidate_ref,
            chunk_directory=chunk_directory,
        )
        result = json.dumps(document, indent=2, ensure_ascii=False)
        if output:
            write_new(output, (result + "\n").encode("utf-8"))
            click.echo(str(output))
        else:
            click.echo(result)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None


@verification_candidate.command("draft-inputs")
@click.option(
    "--draft", "draft_path", type=click.Path(path_type=Path, dir_okay=False), required=True
)
@click.option("--side", type=click.Choice(["baseline", "candidate"]), required=True)
@click.option("--candidate-id", required=True)
@click.option(
    "--chunk-directory",
    type=click.Path(path_type=Path, file_okay=False),
    help="Parent of input-chunks. Defaults to the draft file's directory.",
)
def draft_inputs(draft_path, side, candidate_id, chunk_directory):
    """Validate a Git draft side, transfer its chunks, and print registration fields."""
    from services.verification.git_draft_artifacts import read_draft_side
    from services.verification.input_chunks import publish_input_chunk

    try:
        directory = candidate_directory(candidate_id)
        evidence = read_draft_side(draft_path, side, chunk_directory=chunk_directory)
        if not evidence.inputs:
            raise ValueError(
                "candidate_draft_empty_side: Select existing source inputs for registration."
            )
        for ref, content in evidence.chunks:
            publish_input_chunk(directory, ref, content)
        click.echo(json.dumps(evidence.fields, indent=2, ensure_ascii=False))
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
