"""Script CLI commands."""

import base64
import json
import sys
from typing import Any, Optional

import click

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success
from cli.utils.connection import run_command, handle_unity_errors
from cli.utils.parsers import parse_json_list_or_exit
from cli.utils.confirmation import confirm_destructive_action


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


@script.command("create")
@click.argument("name")
@click.option(
    "--path", "-p",
    default="Assets/Scripts",
    help="Directory to create the script in."
)
@click.option(
    "--type", "-t",
    "script_type",
    type=click.Choice(["MonoBehaviour", "ScriptableObject",
                      "Editor", "EditorWindow", "Plain"]),
    default="MonoBehaviour",
    help="Type of script to create."
)
@click.option(
    "--namespace", "-n",
    default=None,
    help="Namespace for the script."
)
@click.option(
    "--contents", "-c",
    default=None,
    help="Full script contents (overrides template)."
)
@handle_unity_errors
def create(name: str, path: str, script_type: str, namespace: Optional[str], contents: Optional[str]):
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
    "--start-line", "-s",
    default=None,
    type=click.IntRange(min=1),
    help="Starting line number (1-based)."
)
@click.option(
    "--line-count", "-n",
    default=None,
    type=click.IntRange(min=1),
    help="Number of lines to read."
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

    result = run_command("manage_script", params, config)
    # For read, just output the content directly
    if result.get("success") and result.get("data"):
        data = result.get("data", {})
        if isinstance(data, dict) and "contents" in data:
            contents = data["contents"]
            if start_line is not None or line_count is not None:
                start = (start_line or 1) - 1
                end = start + line_count if line_count is not None else None
                contents = "".join(contents.splitlines(keepends=True)[start:end])
            if config.format == "json":
                result = dict(result)
                result["data"] = dict(result["data"])
                result["data"]["contents"] = contents
                if start_line is not None or line_count is not None:
                    if result["data"].get("contentsEncoded"):
                        result["data"]["encodedContents"] = base64.b64encode(
                            contents.encode("utf-8")
                        ).decode("ascii")
                click.echo(format_output(result, config.format))
            else:
                click.echo(contents)
        else:
            click.echo(format_output(result, config.format))
    else:
        click.echo(format_output(result, config.format))


@script.command("delete")
@click.argument("path")
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Skip confirmation prompt."
)
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
    "--edits", "-e",
    required=True,
    help='Edits as JSON array of {startLine, startCol, endLine, endCol, newText}.'
)
@handle_unity_errors
def edit(path: str, edits: str):
    """Apply text edits to a script.

    \b
    Examples:
        unity-mcp script edit "Assets/Scripts/Player.cs" --edits '[{"startLine": 10, "startCol": 1, "endLine": 10, "endCol": 20, "newText": "// Modified"}]'
    """
    config = get_config()

    edits_list = parse_json_list_or_exit(edits, "edits")
    name, directory = _split_script_path(path)

    params: dict[str, Any] = {
        "action": "apply_text_edits",
        "name": name,
        "path": directory,
        "edits": edits_list,
    }

    result = run_command("manage_script", params, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Applied edits to: {path}")


@script.command("validate")
@click.argument("path")
@click.option(
    "--level", "-l",
    type=click.Choice(["basic", "standard"]),
    default="basic",
    help="Validation level."
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
    click.echo(format_output(result, config.format))
