"""Review unchanged inputs suggested by a retained outgoing reference graph."""

import json
from pathlib import Path

import click

from cli.utils.play_scenario_analysis_io import read_bytes, write_new

_FILE = click.Path(path_type=Path, dir_okay=False)


@click.command("dependency-closure")
@click.option("--project", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--draft", required=True, type=_FILE, help="Committed Git candidate draft.")
@click.option("--root", required=True, help="Project-relative entry asset.")
@click.option(
    "--graph-page",
    multiple=True,
    required=True,
    type=_FILE,
    help="Outgoing impact page in original order.",
)
@click.option(
    "--diagnostics", multiple=True, type=(str, _FILE), help="Graph node and its diagnostics page."
)
@click.option("--output", type=_FILE, help="New proposal JSON file.")
def dependency_closure(project, draft, root, graph_page, diagnostics, output):
    """Propose additional asset and metadata hashes from the selected commits."""
    from services.verification.dependency_closure import MAX_PAGES, propose_dependency_closure
    from services.verification.git_draft_artifacts import read_draft_side
    from services.verification.input_chunks import decode_object

    try:
        if len(graph_page) > MAX_PAGES or len(diagnostics) > MAX_PAGES:
            raise ValueError("candidate_dependency_page_limit")
        remaining = 16 * 1024**2

        def page(path):
            nonlocal remaining
            data = read_bytes(path, min(remaining, 4 * 1024**2))
            remaining -= len(data)
            return decode_object(data)

        evidence = read_draft_side(draft, "candidate")
        result = propose_dependency_closure(
            project,
            evidence,
            root,
            [page(path) for path in graph_page],
            [(node, page(path)) for node, path in diagnostics],
        )
        encoded = json.dumps(result, indent=2, ensure_ascii=False)
        if output:
            write_new(output, (encoded + "\n").encode("utf-8"))
            click.echo(str(output))
        else:
            click.echo(encoded)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
