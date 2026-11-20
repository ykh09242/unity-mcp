"""Click adapter for the official Unity provider evidence importer."""

from pathlib import Path

import click

from .official_evidence import import_session


@click.command("import-official")
@click.argument("session", type=Path)
@click.option("--nunit", type=Path, help="Original Unity Test Framework NUnit XML.")
@click.option("--player", type=Path, help="Original independent game-state JSON from the Player.")
@click.option("--output", type=Path)
def import_official(session, nunit, player, output):
    """Import retained NUnit and standalone Player facts from a connected unity mcp session."""
    from .commands import emit_result

    def inspect():
        result = import_session(session, nunit, player)
        return result, 0 if result["state"] == "observed" else 2

    emit_result(inspect, output)
