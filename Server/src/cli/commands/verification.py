"""Public MCP verification profile and report commands."""

import click

from cli.utils.connection import handle_unity_errors


@click.command("verification")
@click.argument(
    "action",
    type=click.Choice(["profiles", "set_profile", "prepare", "run", "status", "cancel", "report"]),
)
@click.option(
    "--options",
    default="{}",
    help="Action options as JSON, @file or - for stdin. Run/status/report use the returned owned IDs.",
)
@handle_unity_errors
def verification(action: str, options: str):
    """Prepare source-linked checks and observe runs without automatically replaying work."""
    from cli.utils.composed_tools import echo_composed_tool

    echo_composed_tool("manage_verification", action, options)
