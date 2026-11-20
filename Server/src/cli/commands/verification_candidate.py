"""Offline operator commands for reviewing verification inputs."""

import json
from pathlib import Path

import click

from cli.commands.verification_input_chunks import input_chunks
from cli.utils.play_scenario_analysis_io import write_new


@click.group("verification-candidate")
def verification_candidate():
    """Review source evidence and manage local Player candidate registrations."""


verification_candidate.add_command(input_chunks)


@verification_candidate.command("draft")
@click.option("--project", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--baseline", default="HEAD", show_default=True, help="Git commit or revision.")
@click.option("--selection", type=click.Choice(["working-tree", "staged"]), required=True)
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), help="New JSON file.")
def draft(project, baseline, selection, output):
    """Compare selected Unity source bytes with a Git baseline and emit a reviewable JSON draft."""
    from services.verification.git_draft import git_draft

    try:
        result = json.dumps(git_draft(project, baseline, selection), indent=2, ensure_ascii=False)
        if output:
            write_new(output, (result + "\n").encode("utf-8"))
            click.echo(str(output))
        else:
            click.echo(result)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
