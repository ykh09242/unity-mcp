"""Run a reviewed offline Player pair using the checked-out verifier."""

import json
from pathlib import Path

import click


@click.command("reproduce")
@click.option(
    "--project", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
@click.option(
    "--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True
)
@click.option("--sha256", required=True, help="Pair manifest digest from an independent review.")
@click.option(
    "--artifacts",
    type=click.Path(path_type=Path, file_okay=False),
    help="Trusted pair directory. Defaults to the manifest's parent.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path, file_okay=False),
    required=True,
    help="New directory for fresh native evidence and reproduction.json.",
)
@click.pass_context
def reproduce_candidate(ctx, project, manifest, sha256, artifacts, output):
    """Verify Git inputs, dependency closures and builds, then run both Players."""
    from services.verification.reproduce import reproduce_pair

    try:
        report, code = reproduce_pair(
            project, manifest, sha256, artifacts or manifest.parent, output
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from None
    click.echo(json.dumps(report))
    ctx.exit(code)
