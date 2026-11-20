"""Resume a retained dependency scan and compare committed roots offline."""

import json
from pathlib import Path
import sqlite3

import click

_FILE = click.Path(path_type=Path, dir_okay=False)


@click.command("dependency-scan")
@click.option("--project", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--commit", required=True, help="Exact Git commit for this graph.")
@click.option("--root", required=True, help="Committed entry asset path.")
@click.option("--checkpoint", required=True, type=_FILE, help="Local resumable SQLite file.")
@click.option("--graph-page", multiple=True, type=_FILE, help="Next saved outgoing impact page.")
@click.option("--output", type=_FILE, help="New JSONL inventory file, after the final page.")
def dependency_scan(project, commit, root, checkpoint, graph_page, output):
    """Record graph pages atomically and hash the corresponding committed assets and metadata."""
    from services.verification.dependency_diff import inventory, publish_rows
    from services.verification.dependency_scan import scan_pages

    try:
        result = scan_pages(project, checkpoint, commit, root, graph_page)
        if output:
            result["inventory"] = publish_rows(output, inventory(checkpoint))
        click.echo(json.dumps(result, sort_keys=True))
    except (OSError, sqlite3.Error, ValueError) as error:
        raise click.ClickException(str(error)) from None


@click.command("dependency-diff")
@click.option("--baseline", required=True, type=_FILE, help="Complete baseline scan checkpoint.")
@click.option("--candidate", required=True, type=_FILE, help="Complete candidate scan checkpoint.")
@click.option("--output", required=True, type=_FILE, help="New JSONL comparison and consumer list.")
def dependency_diff(baseline, candidate, output):
    """Compare retained native references and their prior and current consumers."""
    from services.verification.dependency_diff import compare_scans, publish_rows

    try:
        result = publish_rows(output, compare_scans(baseline, candidate))
        click.echo(json.dumps(result, sort_keys=True))
    except (OSError, sqlite3.Error, ValueError) as error:
        raise click.ClickException(str(error)) from None
