"""Unity documentation lookup CLI commands."""

import asyncio
import json
import subprocess
import sys

import click
from pydantic import JsonValue

from cli.utils.config import get_config
from cli.utils.output import format_output


def _worker_command() -> list[str]:
    """Use the same installed distribution and interpreter as the CLI."""
    return [
        sys.executable,
        "-c",
        "from cli.commands.docs import _run_lookup_worker; _run_lookup_worker()",
    ]


def _run_lookup_worker() -> None:
    """Keep blocking fetch workers inside the process owned by this lookup."""
    from services.tools.unity_docs import _get_doc

    request = json.loads(sys.stdin.buffer.read())
    result = asyncio.run(
        _get_doc(request["class_name"], request["member_name"], request["version"])
    )
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


def _lookup_doc(
    class_name: str, member_name: str | None, version: str | None, timeout: int
) -> dict[str, JsonValue]:
    """Bound imports, fetches and executor shutdown by one owned process deadline."""
    request = {"class_name": class_name, "member_name": member_name, "version": version}
    try:
        completed = subprocess.run(
            _worker_command(),
            input=json.dumps(request).encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps its child before raising, including
        # urllib workers that cannot be interrupted by asyncio cancellation.
        return {
            "success": False,
            "message": f"Documentation request timed out after {timeout}s.",
            "error_type": "TimeoutError",
        }
    except OSError:
        return {
            "success": False,
            "message": "Could not start the documentation lookup worker.",
            "error_type": "DocumentationWorkerError",
        }
    if completed.returncode == 0:
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            result = None
        match result:
            case dict() if type(result.get("success")) is bool:
                return result
    return {
        "success": False,
        "message": "Documentation lookup worker failed to return a valid result.",
        "error_type": "DocumentationWorkerError",
    }


@click.group()
def docs():
    """Fetch Unity API documentation."""
    pass


@docs.command("get")
@click.argument("class_name")
@click.argument("member_name", required=False)
@click.option("--version", "-v", default=None, help="Unity version (e.g., 6000.0).")
def get_doc(class_name: str, member_name: str | None, version: str | None):
    """Fetch documentation for a Unity class or member.

    \b
    Examples:
        unity-mcp docs get Physics
        unity-mcp docs get Physics Raycast
        unity-mcp docs get NavMeshAgent SetDestination --version 6000.0
    """
    config = get_config()
    result = _lookup_doc(class_name, member_name, version, config.timeout)
    click.echo(format_output(result, config.format))
    if result.get("success") is False:
        raise click.exceptions.Exit(1)
