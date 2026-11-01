"""Code CLI commands - read, search, and execute source code."""

import sys
import os
import re
import click
import regex
from typing import Optional, Any

from cli.utils.config import CLIConfig, get_config
from cli.utils.output import format_output, print_error, print_info, print_success
from cli.utils.connection import (
    run_command,
    handle_unity_errors,
    UnityCommandError,
    UnityConnectionError,
)
from cli.utils.script_read import format_script_read, script_contents

_SEARCH_RESULT_LIMIT = 1000
_EXCERPT_CHARS = 500
_MATCH_CHARS = 200


def _read_source(params: dict[str, Any], config: CLIConfig) -> dict[str, Any]:
    """Keep read/search transport failures in the selected structured output format."""
    try:
        result = run_command("manage_script", params, config)
    except UnityConnectionError as error:
        raise UnityCommandError({"success": False, "error": str(error)}) from error
    return result.get("result", result)


@click.group()
def code():
    """Code operations - read, search, and execute."""
    pass


@code.command("execute")
@click.argument("source", required=False)
@click.option(
    "--file",
    "-f",
    default=None,
    type=click.Path(exists=True),
    help="Read code from a file instead of argument.",
)
@click.option(
    "--no-safety-checks",
    is_flag=True,
    help="Disable blocked-pattern checks (allows File.Delete, Process.Start, etc).",
)
@handle_unity_errors
def execute(source: Optional[str], file: Optional[str], no_safety_checks: bool):
    """Execute C# code in Unity Editor.

    Code runs as a method body with access to UnityEngine and UnityEditor.
    Use 'return' to send data back.

    \b
    Examples:
        unity-mcp code execute "return Application.unityVersion;"
        unity-mcp code execute "Debug.Log(Camera.main.name);"
        unity-mcp code execute -f my_script.cs
    """
    config = get_config()

    if file:
        with open(file, "r", encoding="utf-8") as f:
            source = f.read()
    elif not source:
        print_error("Provide code as an argument or use --file.")
        sys.exit(1)

    params: dict[str, Any] = {
        "action": "execute",
        "code": source,
        "safety_checks": not no_safety_checks,
    }

    result = run_command("execute_code", params, config)
    click.echo(format_output(result, config.format, show_success_message=True))
    _print_execution_result(result)


@code.command("history")
@click.option(
    "--limit", "-n", default=10, type=int, help="Number of entries to show (default: 10)."
)
@handle_unity_errors
def history(limit: int):
    """Show execution history.

    \b
    Examples:
        unity-mcp code history
        unity-mcp code history --limit 5
    """
    config = get_config()
    result = run_command("execute_code", {"action": "get_history", "limit": limit}, config)
    click.echo(format_output(result, config.format, show_success_message=True))


@code.command("replay")
@click.argument("index", type=int)
@handle_unity_errors
def replay(index: int):
    """Replay a history entry by index.

    \b
    Examples:
        unity-mcp code replay 0
        unity-mcp code replay 3
    """
    config = get_config()
    result = run_command("execute_code", {"action": "replay", "index": index}, config)
    click.echo(format_output(result, config.format, show_success_message=True))
    _print_execution_result(result)


def _print_execution_result(result: dict[str, Any]) -> None:
    if result.get("success") and get_config().format != "json":
        data = result.get("data", {})
        if data and data.get("result") is not None:
            print_success(f"Result: {data['result']}")


@code.command("clear-history")
@handle_unity_errors
def clear_history():
    """Clear execution history."""
    config = get_config()
    result = run_command("execute_code", {"action": "clear_history"}, config)
    click.echo(format_output(result, config.format, show_success_message=True))


@code.command("read")
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
    """Read a source file.

    \b
    Examples:
        unity-mcp code read "Assets/Scripts/Player.cs"
        unity-mcp code read "Assets/Scripts/Player.cs" --start-line 10 --line-count 20
    """
    config = get_config()

    # Extract name and directory from path
    parts = path.replace("\\", "/").split("/")
    filename = os.path.splitext(parts[-1])[0]
    directory = "/".join(parts[:-1]) or "Assets"

    params: dict[str, Any] = {
        "action": "read",
        "name": filename,
        "path": directory,
    }

    result = _read_source(params, config)
    click.echo(format_script_read(result, config.format, start_line, line_count))


@code.command("search")
@click.argument("pattern")
@click.argument("path")
@click.option(
    "--max-results",
    "-n",
    default=50,
    type=click.IntRange(min=1),
    help="Maximum results (default: 50; output capped at 1000 with bounded excerpts).",
)
@click.option(
    "--case-sensitive",
    "-c",
    is_flag=True,
    help="Make search case-sensitive (default: case-insensitive).",
)
@handle_unity_errors
def search(pattern: str, path: str, max_results: int, case_sensitive: bool):
    """Search for patterns in Unity scripts using regex.

    PATTERN is a regex pattern to search for.
    PATH is the script path (e.g., Assets/Scripts/Player.cs).

    Results stop at --max-results (at most 1000). Excerpts are limited to 500
    characters and match text to 200. JSON data.count is the number returned;
    data.truncated indicates further matches, without counting the entire file.

    \b
    Examples:
        unity-mcp code search "class.*Player" "Assets/Scripts/Player.cs"
        unity-mcp code search "private.*int" "Assets/Scripts/GameManager.cs"
        unity-mcp code search "TODO|FIXME" "Assets/Scripts/Utils.cs"
    """
    config = get_config()

    # Extract name and directory from path
    parts = path.replace("\\", "/").split("/")
    filename = os.path.splitext(parts[-1])[0]
    directory = "/".join(parts[:-1]) or "Assets"

    # Step 1: Read the file via Unity's manage_script
    read_params: dict[str, Any] = {
        "action": "read",
        "name": filename,
        "path": directory,
    }

    result = _read_source(read_params, config)
    if result.get("success") is False:
        raise UnityCommandError(result)
    data = result.get("data")
    contents = script_contents(data) if isinstance(data, dict) else None
    if contents is None:
        raise UnityCommandError(
            {"success": False, "error": f"Could not read file content from {path}"}
        )

    # Load the server-side regex engine only when this command actually searches.
    from services.tools import bounded_regex

    # Step 2: Perform regex search locally
    flags = re.MULTILINE
    if not case_sensitive:
        flags |= re.IGNORECASE

    try:
        budget = bounded_regex.WorkBudget()
        limit = min(max_results, _SEARCH_RESULT_LIMIT, bounded_regex.MAX_MATCHES)
        found = bounded_regex.find_matches(
            pattern, contents, flags, limit, budget=budget, truncate=True
        )
        results = []
        cursor = 0
        line_num = 1
        for match in found[:limit]:
            start_idx = match.start()
            budget.consume(start_idx - cursor + _EXCERPT_CHARS + _MATCH_CHARS)
            line_num += contents.count("\n", cursor, start_idx)
            cursor = start_idx
            # Search only a bounded neighborhood rather than copying a whole wide line.
            excerpt_start = max(0, start_idx - _EXCERPT_CHARS // 2)
            line_start = contents.rfind("\n", excerpt_start, start_idx) + 1
            line_start = max(excerpt_start, line_start)
            excerpt_end = min(len(contents), line_start + _EXCERPT_CHARS)
            line_end = contents.find("\n", start_idx, excerpt_end)
            if line_end == -1:
                line_end = excerpt_end
            results.append(
                {
                    "line": line_num,
                    "content": contents[line_start:line_end].strip(),
                    "match": contents[start_idx : min(match.end(), start_idx + _MATCH_CHARS)],
                    "contentTruncated": line_start > 0
                    and contents[line_start - 1] != "\n"
                    or line_end < len(contents)
                    and contents[line_end] != "\n",
                    "matchTruncated": match.end() - start_idx > _MATCH_CHARS,
                }
            )
        budget.check()
    except (regex.error, ValueError, TimeoutError) as error:
        raise UnityCommandError({"success": False, "error": str(error)}) from error

    truncated = len(found) > limit
    if config.format == "json":
        click.echo(
            format_output(
                {
                    "success": True,
                    "data": {"matches": results, "count": len(results), "truncated": truncated},
                },
                config.format,
            )
        )
    elif not results:
        print_info(f"No matches found for pattern: {pattern}")
    else:
        total = f"at least {len(found)}" if truncated else str(len(found))
        click.echo(f"Found {len(results)} matches (total: {total}):\n")
        for match in results:
            suffix = " ... (excerpt truncated)" if match["contentTruncated"] else ""
            click.echo(f"  Line {match['line']}: {match['content']}{suffix}")
