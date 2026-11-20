"""CLI entry point for merge-tree loader verification."""

import json
from pathlib import Path

import click

from cli.utils.play_scenario_analysis_io import write_new


@click.command("anticipate-merge")
@click.option(
    "--project", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
@click.option("--target", required=True, help="Current receiving Git ref or commit.")
@click.option("--head", required=True, help="Proposed Git head ref or commit.")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), help="New JSON file.")
@click.pass_context
def anticipate_merge(ctx, project, target, head, output):
    """Check Resources references against Git's anticipated merge tree."""
    from services.verification.git_merge import anticipate_merge as review_merge

    report = review_merge(project, target, head)
    encoded = json.dumps(report, indent=2)
    try:
        if output is not None:
            write_new(output, (encoded + "\n").encode("utf-8"))
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
    click.echo(encoded)
    ctx.exit({"clean": 0, "regression": 1, "conflict": 1, "unavailable": 2}[report["status"]])
