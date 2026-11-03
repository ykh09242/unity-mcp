"""Render follow-up commands against the effective connection and project guard."""

import os
import re
import shlex
from collections.abc import Sequence

from cli.utils.config import CLIConfig


def followup_command(config: CLIConfig, arguments: Sequence[str]) -> str:
    """Freeze resolved context settings; quote for PowerShell on Windows or POSIX shells."""
    command = [
        "unity-mcp",
        "--no-project-context",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--timeout",
        str(config.timeout),
        "--format",
        config.format,
    ]
    if config.unity_instance:
        command.extend(["--instance", config.unity_instance])
    if config.expected_project_root is not None:
        command.extend(["--expect-project", config.expected_project_root])
    if config.verbose:
        command.append("--verbose")
    command.extend(arguments)
    if os.name == "nt":
        # PowerShell escapes a literal apostrophe by doubling it inside single quotes.
        return " ".join(
            value
            if re.fullmatch(r"[A-Za-z0-9_./:-]+", value)
            else "'" + value.replace("'", "''") + "'"
            for value in command
        )
    return shlex.join(command)
