"""Editor CLI commands."""

import sys
import time
from uuid import uuid4
import click
from typing import Optional, Any, assert_never

from pydantic import ValidationError
from models.editor_readiness import PlayReadinessJob

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success, print_info
from cli.utils.connection import (
    run_command,
    run_list_custom_tools,
    handle_unity_errors,
    UnityConnectionError,
    UnityCommandError,
)
from cli.utils.suggestions import suggest_matches, format_suggestions
from cli.utils.parsers import parse_json_dict_or_exit


@click.group()
def editor():
    """Editor operations - play mode, console, tags, layers."""
    pass


@editor.command("play")
@click.option(
    "--wait-until",
    type=click.Choice(["scene_loaded", "first_frame"]),
    default=None,
    help="Wait for loaded active scene or subsequent simulation frame (not app async initialization).",
)
@click.option("--timeout-seconds", type=click.IntRange(1, 300), default=30, show_default=True)
@handle_unity_errors
def play(wait_until: str | None, timeout_seconds: int):
    """Enter play mode."""
    config = get_config()
    params = {"action": "play"}
    if wait_until is None:
        result = run_command("manage_editor", params, config)
    else:
        job_id = uuid4().hex
        params.update(wait_until=wait_until, timeout_seconds=timeout_seconds, job_id=job_id)
        deadline = time.monotonic() + timeout_seconds
        result = None
        first_request = True
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                result = run_command(
                    "manage_editor", params, config, timeout=min(config.timeout, remaining)
                )
            except UnityConnectionError as error:
                # A play transition may drop its acknowledgement. Recover by ID; never replay play.
                if str(error).startswith("HTTP error from server:") and not str(error).startswith(
                    ("HTTP error from server: 404", "HTTP error from server: 503")
                ):
                    raise
            except UnityCommandError as error:
                response = error.response
                if response.get("hint") != "retry":
                    raise
            else:
                if time.monotonic() >= deadline:
                    raise UnityCommandError(
                        {
                            "success": False,
                            "error": "play_readiness_timeout",
                            "data": {
                                "job_id": job_id,
                                "status": "wait_timed_out",
                                "last_response": result,
                            },
                        }
                    )
                data = result.get("data")
                try:
                    job = PlayReadinessJob.model_validate(data)
                except ValidationError:
                    raise UnityCommandError(
                        {
                            "success": False,
                            "error": "invalid_play_readiness_response",
                            "data": result,
                        }
                    )
                if job.job_id != job_id:
                    raise UnityCommandError(
                        {"success": False, "error": "play_readiness_job_mismatch", "data": data}
                    )
                match job.status:
                    case "succeeded":
                        break
                    case "failed" | "cancelled" | "timed_out":
                        raise UnityCommandError(
                            {**result, "success": False, "error": job.error or job.status}
                        )
                    case "running":
                        pass
                    case unreachable:
                        assert_never(unreachable)
            if first_request:
                params = {"action": "get_play_mode_job", "job_id": job_id}
                first_request = False
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.1, remaining))
        else:
            raise UnityCommandError(
                {
                    "success": False,
                    "error": "play_readiness_timeout",
                    "data": {
                        "job_id": job_id,
                        "status": "wait_timed_out",
                        "last_response": result,
                    },
                }
            )
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(result.get("message") or "Play mode requested")


@editor.command("play-status")
@click.argument("job_id")
@handle_unity_errors
def play_status(job_id: str):
    """Read a persisted play readiness job."""
    config = get_config()
    result = run_command("manage_editor", {"action": "get_play_mode_job", "job_id": job_id}, config)
    click.echo(format_output(result, config.format))


@editor.command("cancel-play-wait")
@click.argument("job_id")
@handle_unity_errors
def cancel_play_wait(job_id: str):
    """Cancel readiness monitoring without stopping Play Mode."""
    config = get_config()
    result = run_command(
        "manage_editor", {"action": "cancel_play_mode_job", "job_id": job_id}, config
    )
    click.echo(format_output(result, config.format))


@editor.command("game-view-size")
@click.option("--width", type=click.IntRange(1, 8192), default=None)
@click.option("--height", type=click.IntRange(1, 8192), default=None)
@click.option("--aspect-ratio", default=None, help="Aspect ratio W:H, for example 16:9.")
@click.option(
    "--preset", default=None, help="Unique label returned by this command without options."
)
@click.option(
    "--restore-token", default=None, help="Restore a previous selection using a returned token."
)
@handle_unity_errors
def game_view_size(
    width: int | None,
    height: int | None,
    aspect_ratio: str | None,
    preset: str | None,
    restore_token: str | None,
):
    """Read or select the size of an open Game View."""
    selectors = sum(
        (
            width is not None or height is not None,
            aspect_ratio is not None,
            preset is not None,
            restore_token is not None,
        )
    )
    if selectors > 1 or (width is None) != (height is None):
        raise click.UsageError(
            "Specify width and height together, or one of aspect-ratio, preset, restore-token."
        )
    config = get_config()
    action = "get_game_view_size"
    if selectors:
        action = "restore_game_view_size" if restore_token is not None else "set_game_view_size"
    params = {
        "action": action,
        "width": width,
        "height": height,
        "aspect_ratio": aspect_ratio,
        "preset": preset,
        "restore_token": restore_token,
    }
    result = run_command(
        "manage_editor", {key: value for key, value in params.items() if value is not None}, config
    )
    click.echo(format_output(result, config.format))


@editor.command("pause")
@handle_unity_errors
def pause():
    """Toggle pause/resume in play mode."""
    config = get_config()
    result = run_command("manage_editor", {"action": "pause"}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(result.get("message") or "Pause/resume toggled")


@editor.command("stop")
@handle_unity_errors
def stop():
    """Stop play mode."""
    config = get_config()
    result = run_command("manage_editor", {"action": "stop"}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(result.get("message") or "Stop requested")


@editor.command("console")
@click.option(
    "--type",
    "-t",
    "log_types",
    multiple=True,
    type=click.Choice(["error", "warning", "log", "all"]),
    default=["error", "warning", "log"],
    help="Message types to retrieve.",
)
@click.option("--count", "-n", default=10, type=int, help="Number of messages to retrieve.")
@click.option(
    "--filter", "-f", "filter_text", default=None, help="Filter messages containing this text."
)
@click.option("--stacktrace", "-s", is_flag=True, help="Include stack traces.")
@click.option("--clear", is_flag=True, help="Clear the console instead of reading.")
@click.option("--include-mcp-logs", is_flag=True, help="Include MCP internal logger messages.")
@handle_unity_errors
def console(
    log_types: tuple,
    count: int,
    filter_text: Optional[str],
    stacktrace: bool,
    clear: bool,
    include_mcp_logs: bool,
):
    """Read or clear the Unity console.

    \b
    Examples:
        unity-mcp editor console
        unity-mcp editor console --type error --count 20
        unity-mcp editor console --filter "NullReference" --stacktrace
        unity-mcp editor console --clear
    """
    config = get_config()

    if clear:
        result = run_command("read_console", {"action": "clear"}, config)
        click.echo(format_output(result, config.format))
        if result.get("success") and config.format != "json":
            print_success("Console cleared")
        return

    params: dict[str, Any] = {
        "action": "get",
        "types": list(log_types),
        "count": count,
        "include_stacktrace": stacktrace,
    }
    if stacktrace:
        params["format"] = "detailed"
    if include_mcp_logs:
        params["include_mcp_logs"] = True

    if filter_text:
        params["filter_text"] = filter_text

    result = run_command("read_console", params, config)
    data = result.get("data")
    if (
        stacktrace
        and config.format != "json"
        and result.get("success")
        and isinstance(data, list)
        and data
    ):
        for entry in data:
            click.echo(format_output(entry, "text"))
    else:
        click.echo(format_output(result, config.format))


@editor.command("add-tag")
@click.argument("tag_name")
@handle_unity_errors
def add_tag(tag_name: str):
    """Add a new tag.

    \b
    Examples:
        unity-mcp editor add-tag "Enemy"
        unity-mcp editor add-tag "Collectible"
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "add_tag", "tagName": tag_name}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Added tag: {tag_name}")


@editor.command("remove-tag")
@click.argument("tag_name")
@handle_unity_errors
def remove_tag(tag_name: str):
    """Remove a tag.

    \b
    Examples:
        unity-mcp editor remove-tag "OldTag"
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "remove_tag", "tagName": tag_name}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Removed tag: {tag_name}")


@editor.command("add-layer")
@click.argument("layer_name")
@handle_unity_errors
def add_layer(layer_name: str):
    """Add a new layer.

    \b
    Examples:
        unity-mcp editor add-layer "Interactable"
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "add_layer", "layerName": layer_name}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Added layer: {layer_name}")


@editor.command("remove-layer")
@click.argument("layer_name")
@handle_unity_errors
def remove_layer(layer_name: str):
    """Remove a layer.

    \b
    Examples:
        unity-mcp editor remove-layer "OldLayer"
    """
    config = get_config()
    result = run_command(
        "manage_editor", {"action": "remove_layer", "layerName": layer_name}, config
    )
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Removed layer: {layer_name}")


@editor.command("tool")
@click.argument("tool_name")
@handle_unity_errors
def set_tool(tool_name: str):
    """Set the active editor tool.

    \b
    Examples:
        unity-mcp editor tool "Move"
        unity-mcp editor tool "Rotate"
        unity-mcp editor tool "Scale"
    """
    config = get_config()
    result = run_command(
        "manage_editor", {"action": "set_active_tool", "toolName": tool_name}, config
    )
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Set active tool: {tool_name}")


@editor.command("deploy")
@handle_unity_errors
def deploy():
    """Deploy MCPForUnity package from configured source.

    Copies the configured MCPForUnity source folder into the project's
    installed package location. The source path must be set in the
    MCP for Unity Advanced Settings first. Triggers recompilation.

    \b
    Examples:
        unity-mcp editor deploy
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "deploy_package"}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success("Package deployed")


@editor.command("restore")
@handle_unity_errors
def restore():
    """Restore MCPForUnity package from last backup.

    Reverts the last deployment by restoring from backup.

    \b
    Examples:
        unity-mcp editor restore
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "restore_package"}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success("Package restored from backup")


@editor.command("undo")
@handle_unity_errors
def undo():
    """Undo the last editor action.

    \b
    Examples:
        unity-mcp editor undo
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "undo"}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success("Undo performed")


@editor.command("redo")
@handle_unity_errors
def redo():
    """Redo the last undone action.

    \b
    Examples:
        unity-mcp editor redo
    """
    config = get_config()
    result = run_command("manage_editor", {"action": "redo"}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success("Redo performed")


@editor.command("menu")
@click.argument("menu_path")
@handle_unity_errors
def execute_menu(menu_path: str):
    """Execute a menu item.

    \b
    Examples:
        unity-mcp editor menu "File/Save"
        unity-mcp editor menu "Edit/Undo"
        unity-mcp editor menu "GameObject/Create Empty"
    """
    config = get_config()
    result = run_command("execute_menu_item", {"menu_path": menu_path}, config)
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(f"Executed: {menu_path}")


@editor.command("tests")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["EditMode", "PlayMode"]),
    default="EditMode",
    help="Test mode to run.",
)
@click.option(
    "--async", "async_mode", is_flag=True, help="Run asynchronously and return job ID for polling."
)
@click.option(
    "--wait",
    "-w",
    type=int,
    default=None,
    help="Wait up to N seconds for completion (default: no wait).",
)
@click.option("--details", is_flag=True, help="Include detailed results for all tests.")
@click.option("--failed-only", is_flag=True, help="Include details for failed/skipped tests only.")
@click.option(
    "--clear-stuck",
    is_flag=True,
    help="Clear an orphaned running job that is blocking new runs, instead of starting a run.",
)
@handle_unity_errors
def run_tests(
    mode: str,
    async_mode: bool,
    wait: Optional[int],
    details: bool,
    failed_only: bool,
    clear_stuck: bool,
):
    """Run Unity tests.

    \b
    Examples:
        unity-mcp editor tests
        unity-mcp editor tests --mode PlayMode
        unity-mcp editor tests --async
        unity-mcp editor tests --wait 60 --failed-only
        unity-mcp editor tests --clear-stuck
    """
    config = get_config()

    if clear_stuck:
        result = run_command("run_tests", {"clear_stuck": True}, config)
        click.echo(format_output(result, config.format))
        return

    params: dict[str, Any] = {"mode": mode}
    if details:
        params["includeDetails"] = True
    if failed_only:
        params["includeFailedTests"] = True

    result = run_command("run_tests", params, config)

    # For async mode, just show job ID
    if async_mode and result.get("success"):
        job_id = result.get("data", {}).get("job_id")
        if job_id:
            if config.format == "json":
                click.echo(format_output(result, config.format))
            else:
                click.echo(f"Test job started: {job_id}")
                print_info("Poll with: unity-mcp editor poll-test " + job_id)
            return

    if wait and result.get("success"):
        job_id = result.get("data", {}).get("job_id")
        if job_id:
            poll_test.callback(job_id, wait, details, failed_only)
            return

    click.echo(format_output(result, config.format))


@editor.command("poll-test")
@click.argument("job_id")
@click.option(
    "--wait", "-w", type=int, default=30, help="Wait up to N seconds for completion (default: 30)."
)
@click.option("--details", is_flag=True, help="Include detailed results for all tests.")
@click.option("--failed-only", is_flag=True, help="Include details for failed/skipped tests only.")
@handle_unity_errors
def poll_test(job_id: str, wait: int, details: bool, failed_only: bool):
    """Poll an async test job for status/results.

    \b
    Examples:
        unity-mcp editor poll-test abc123
        unity-mcp editor poll-test abc123 --wait 60
        unity-mcp editor poll-test abc123 --failed-only
    """
    config = get_config()

    params: dict[str, Any] = {"job_id": job_id}
    if details:
        params["includeDetails"] = True
    if failed_only:
        params["includeFailedTests"] = True

    deadline = time.monotonic() + max(wait, 0)
    if wait > 0:
        result = run_command("get_test_job", params, config, timeout=min(config.timeout, wait))
        if time.monotonic() >= deadline:
            result = {
                "success": False,
                "error": "Timeout waiting for test job",
                "data": {"job_id": job_id},
            }
    else:
        result = run_command("get_test_job", params, config)
    while wait > 0 and result.get("success"):
        data = result.get("data")
        if not isinstance(data, dict):
            break
        status = data.get("status")
        if not status:
            break
        if status in ("succeeded", "failed", "cancelled"):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(2.0, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        response = run_command(
            "get_test_job", params, config, timeout=min(config.timeout, remaining)
        )
        if time.monotonic() >= deadline:
            break
        result = response
    click.echo(format_output(result, config.format))

    if isinstance(result, dict) and result.get("success") and config.format != "json":
        data = result.get("data", {})
        if not isinstance(data, dict):
            return
        status = data.get("status", "unknown")
        if status == "succeeded":
            print_success("Tests completed successfully")
        elif status == "failed":
            test_result = data.get("result")
            if test_result is None:
                print_error(f"Test job failed: {data.get('error') or 'No test results available'}")
            else:
                summary = test_result.get("summary", {})
                failed = summary.get("failed", 0)
                print_error(f"Tests failed: {failed} failures")
        elif status == "running":
            progress = data.get("progress", {})
            completed = progress.get("completed", 0)
            total = progress.get("total", 0)
            print_info(f"Tests running: {completed}/{total}")


@editor.command("refresh")
@click.option(
    "--mode", type=click.Choice(["if_dirty", "force"]), default="if_dirty", help="Refresh mode."
)
@click.option(
    "--scope",
    type=click.Choice(["assets", "scripts", "all"]),
    default="all",
    help="What to refresh.",
)
@click.option("--compile", is_flag=True, help="Request script compilation.")
@click.option("--no-wait", is_flag=True, help="Don't wait for refresh to complete.")
@handle_unity_errors
def refresh(mode: str, scope: str, compile: bool, no_wait: bool):
    """Force Unity to refresh assets/scripts.

    \b
    Examples:
        unity-mcp editor refresh
        unity-mcp editor refresh --mode force
        unity-mcp editor refresh --compile
        unity-mcp editor refresh --scope scripts --compile
    """
    config = get_config()

    params: dict[str, Any] = {
        "mode": mode,
        "scope": scope,
        "compile": "request" if compile else "none",
        # Keep the native compilation start barrier, but wait for asset refresh
        # readiness here so stable Play Mode and domain reload do not strand it.
        "wait_for_ready": compile and not no_wait,
    }

    if config.format != "json":
        click.echo("Refreshing Unity...")
    deadline = time.monotonic() + config.timeout
    result = run_command("refresh_unity", params, config)
    if not no_wait and result.get("success") is True:
        # Use the canonical resource's pure advice calculation on authoritative
        # native snapshots; the CLI HTTP boundary exposes commands, not resources.
        from services.resources.editor_state import _enrich_advice_and_staleness

        ready = False
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                state = run_command("get_editor_state", {}, config, timeout=min(2.0, remaining))
            except UnityConnectionError as error:
                if str(error).startswith("HTTP error from server:") and not str(error).startswith(
                    ("HTTP error from server: 404", "HTTP error from server: 503")
                ):
                    raise
                pass  # Read-only retries can survive a domain reload disconnect.
            except UnityCommandError as error:
                response = error.response
                message = str(response.get("error") or response.get("message") or "").lower()
                if response.get("hint") != "retry" and not any(
                    transient in message
                    for transient in ("disconnected", "connection closed", "timeout", "timed out")
                ):
                    raise
            else:
                if time.monotonic() >= deadline:
                    break
                data = state.get("data")
                if (
                    not isinstance(data, dict)
                    or data.get("schema_version") != "unity-mcp/editor_state@2"
                ):
                    raise UnityCommandError(
                        {
                            "success": False,
                            "error": "invalid_editor_state",
                            "message": "Readiness requires a canonical editor state snapshot.",
                        }
                    )
                advice = _enrich_advice_and_staleness(data)["advice"]
                if advice["ready_for_tools"] is True:
                    ready = True
                    break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.25, remaining))
        if not ready:
            raise UnityCommandError(
                {
                    "success": False,
                    "error": "refresh_timeout_waiting_for_ready",
                    "message": f"Refresh was acknowledged, but editor readiness was not confirmed within {config.timeout}s.",
                    "data": {
                        "timeout": True,
                        "wait_seconds": config.timeout,
                        "refresh_response": result,
                    },
                    "hint": "Check editor state before deciding whether to refresh again.",
                }
            )
    click.echo(format_output(result, config.format))
    if result.get("success") and config.format != "json":
        print_success(result.get("message") or "Unity refresh requested")


@editor.command("custom-tool")
@click.argument("tool_name")
@click.option("--params", "-p", default="{}", help="Tool parameters as JSON.")
@handle_unity_errors
def custom_tool(tool_name: str, params: str):
    """Execute a custom Unity tool.

    Custom tools are registered by Unity projects via the MCP plugin.

    \b
    Examples:
        unity-mcp editor custom-tool "MyCustomTool"
        unity-mcp editor custom-tool "BuildPipeline" --params '{"target": "Android"}'
    """
    config = get_config()

    params_dict = parse_json_dict_or_exit(params, "params")

    try:
        result = run_command(
            "execute_custom_tool",
            {
                "tool_name": tool_name,
                "parameters": params_dict,
            },
            config,
        )
    except UnityCommandError as error:
        result = error.response
    click.echo(format_output(result, config.format))
    if result.get("success"):
        if config.format != "json":
            print_success(f"Executed custom tool: {tool_name}")
    else:
        message = (result.get("message") or result.get("error") or "").lower()
        if config.format != "json" and "not found" in message and "tool" in message:
            try:
                tools_result = run_list_custom_tools(config)
                tools = tools_result.get("tools")
                if tools is None:
                    data = tools_result.get("data", {})
                    tools = data.get("tools") if isinstance(data, dict) else None
                names = (
                    [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
                    if isinstance(tools, list)
                    else []
                )
                matches = suggest_matches(tool_name, names)
                suggestion = format_suggestions(matches)
                if suggestion:
                    print_info(suggestion)
                    print_info(f'Example: unity-mcp editor custom-tool "{matches[0]}"')
            except UnityConnectionError:
                pass
        sys.exit(1)
