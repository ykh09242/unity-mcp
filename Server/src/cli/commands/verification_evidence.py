"""Register shared verification commands with the full CLI configuration."""

import click
from unity_mcp_evidence import commands
from unity_mcp_evidence.commands import (
    FILE,
    OUTPUT,
    compare,
    first_failure,
    offline_main,
    selection,
)

from ..utils.config import get_config


@click.group("verification-evidence", commands=commands.EVIDENCE_COMMANDS)
def verification_evidence() -> None:
    """Compare retained results and their input identities without starting Unity."""
    commands.set_evidence_format(get_config().format)


def emit_result(action, output):
    commands.emit_result(action, output, output_format=get_config().format)


__all__ = [
    "FILE",
    "OUTPUT",
    "compare",
    "emit_result",
    "first_failure",
    "offline_main",
    "selection",
    "verification_evidence",
]
