"""Script CLI commands."""

import json
import sys
from typing import Any, Optional

import click
from cli.utils.parsers import admit_json_inputs

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success
from cli.utils.connection import run_command, handle_unity_errors, UnityCommandError
from cli.utils.parsers import parse_json_list_or_exit
from cli.utils.confirmation import confirm_destructive_action
from cli.utils.script_read import echo_script_read


def _split_script_path(path: str) -> tuple[str, str]:
    """Resolve a CLI script path to Unity's name/directory locator."""
    parts = path.replace("\\", "/").rsplit("/", 1)
    filename = parts[-1]
    directory = parts[0] if len(parts) > 1 else "Assets"
    name = filename[:-3] if filename.lower().endswith(".cs") else filename
    return name, directory


@click.group()
def script():
    """Script operations - create, read, edit C# scripts."""
    pass


@script.command("quality")
@click.argument(
    "action",
    type=click.Choice(
        ["capabilities", "capture_baseline", "analyze", "status", "cancel", "impact"]
    ),
)
@click.option(
    "--options",
    default="{}",
    help="Action options as JSON, @file or - for stdin; explicit paths or change_set_id are required for analysis.",
)
@handle_unity_errors
def quality(action: str, options: str):
    """Read source-bound diagnostics through the public MCP tool; never apply fixes."""
    from cli.utils.composed_tools import echo_composed_tool

    echo_composed_tool("manage_script_quality", action, options)


@script.command("create")
@click.argument("name")
@click.option("--path", "-p", default="Assets/Scripts", help="Directory to create the script in.")
@click.option(
    "--type",
    "-t",
    "script_type",
    type=click.Choice(["MonoBehaviour", "ScriptableObject", "Editor", "EditorWindow", "Plain"]),
    default="MonoBehaviour",
    help="Type of script to create.",
)
@click.option("--namespace", "-n", default=None, help="Namespace for the script.")
@click.option("--contents", "-c", default=None, help="Full script contents (overrides template).")
@handle_unity_errors
def create(
    name: str, path: str, script_type: str, namespace: Optional[str], contents: Optional[str]
):
    """Create a new C# script.

    \b
    Examples:
        unity-mcp script create "PlayerController"
        unity-mcp script create "GameManager" --path "Assets/Scripts/Managers"
        unity-mcp script create "EnemyData" --type ScriptableObject
        unity-mcp script create "CustomEditor" --type Editor --namespace "MyGame.Editor"
    """
    config = get_config()

    params: dict[str, Any] = {
        "action": "create",
        "name": name,
        "path": path,
        "scriptType": script_type,
    }

    if namespace:
        params["namespace"] = namespace
    if contents:
        params["contents"] = contents

    result = run_command("manage_script", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Created script: {name}.cs")


@script.command("read")
@click.argument("path")
@click.option(
    "--start-line",
    "-s",
    default=None,
    type=click.IntRange(min=1),
    help="Starting line number (1-based).",
)
@click.option(
    "--line-count", "-n", default=None, type=click.IntRange(min=1), help="Number of lines to read."
)
@handle_unity_errors
def read(path: str, start_line: Optional[int], line_count: Optional[int]):
    """Read a C# script file.

    \b
    Examples:
        unity-mcp script read "Assets/Scripts/Player.cs"
        unity-mcp script read "Assets/Scripts/Player.cs" --start-line 10 --line-count 20
    """
    config = get_config()

    name, directory = _split_script_path(path)

    params: dict[str, Any] = {
        "action": "read",
        "name": name,
        "path": directory,
    }

    if start_line is not None:
        params["start_line"] = start_line
    if line_count is not None:
        params["line_count"] = line_count
    result = run_command("manage_script", params, config)
    echo_script_read(result, config.format, start_line, line_count)


@script.command("delete")
@click.argument("path")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
@handle_unity_errors
def delete(path: str, force: bool):
    """Delete a C# script.

    \b
    Examples:
        unity-mcp script delete "Assets/Scripts/OldScript.cs"
    """
    config = get_config()

    confirm_destructive_action("Delete", "script", path, force)

    name, directory = _split_script_path(path)

    params: dict[str, Any] = {
        "action": "delete",
        "name": name,
        "path": directory,
    }

    result = run_command("manage_script", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Deleted: {path}")


@script.command("edit")
@click.argument("path")
@click.option(
    "--edits",
    "-e",
    required=True,
    help="Edits as JSON array of {startLine, startCol, endLine, endCol, newText}. Accepts @file or -/@- stdin.",
)
@click.option(
    "--expected-sha", default=None, help="SHA-256 observed in a previous JSON script read."
)
@handle_unity_errors
def edit(path: str, edits: str, expected_sha: str | None):
    """Apply text edits to a script.

    \b
    Examples:
        unity-mcp script edit "Assets/Scripts/Player.cs" --edits '[{"startLine": 10, "startCol": 1, "endLine": 10, "endCol": 20, "newText": "// Modified"}]'
    """
    edits = admit_json_inputs(edits=edits)["edits"]

    config = get_config()

    edits_list = parse_json_list_or_exit(edits, "edits")
    name, directory = _split_script_path(path)

    # When omitted, retain the legacy just-in-time lookup for existing callers.
    sha = expected_sha
    if sha is None:
        sha_result = run_command(
            "manage_script", {"action": "get_sha", "name": name, "path": directory}, config
        )
        data = sha_result.get("data")
        sha = data.get("sha256") if isinstance(data, dict) else None
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(c not in "0123456789abcdefABCDEF" for c in sha)
    ):
        raise UnityCommandError(
            {
                "success": False,
                "error": "Expected SHA-256 must be a 64-character hexadecimal digest; no edits were applied.",
            }
        )

    params: dict[str, Any] = {
        "action": "apply_text_edits",
        "name": name,
        "path": directory,
        "edits": edits_list,
        "precondition_sha256": sha,
    }

    result = run_command("manage_script", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Applied edits to: {path}")


@script.command("validate")
@click.argument("path")
@click.option(
    "--level",
    "-l",
    type=click.Choice(["basic", "standard"]),
    default="basic",
    help="Validation level.",
)
@handle_unity_errors
def validate(path: str, level: str):
    """Validate a C# script for errors.

    \b
    Examples:
        unity-mcp script validate "Assets/Scripts/Player.cs"
        unity-mcp script validate "Assets/Scripts/Player.cs" --level standard
    """
    config = get_config()
    name, directory = _split_script_path(path)

    params: dict[str, Any] = {
        "action": "validate",
        "name": name,
        "path": directory,
        "level": level,
    }

    result = run_command("manage_script", params, config)
    click.echo(format_output(result, config.format, show_success_message=True))
