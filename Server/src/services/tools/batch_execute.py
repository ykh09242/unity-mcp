"""Defines the batch_execute tool for orchestrating multiple Unity MCP commands."""
from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_bool, coerce_int
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

logger = logging.getLogger(__name__)

# Fallback used when the Unity-side configured limit is not yet known.
DEFAULT_MAX_COMMANDS_PER_BATCH = 25

# Hard ceiling matching the C# AbsoluteMaxCommandsPerBatch.
ABSOLUTE_MAX_COMMANDS_PER_BATCH = 100

# Settings are mutable and belong to a selected user's editor instance.
_LIMIT_CACHE_TTL_SECONDS = 5.0
_LIMIT_CACHE_MAX_ENTRIES = 128
_cached_max_commands: dict[tuple[str | None, str], tuple[int, float]] = {}


async def _get_max_commands_from_editor_state(ctx: Context, unity_instance: str | None) -> int:
    """
    Attempt to read the configured batch limit from the Unity editor state.
    Falls back to DEFAULT_MAX_COMMANDS_PER_BATCH if unavailable.
    """
    cache_key = None
    if unity_instance:
        try:
            user_id = await ctx.get_state("user_id")
            if user_id is None or isinstance(user_id, str):
                cache_key = (user_id, unity_instance)
        except Exception:
            pass
    cached = _cached_max_commands.get(cache_key) if cache_key is not None else None
    if cached is not None and time.monotonic() < cached[1]:
        return cached[0]

    try:
        # The enriched resource also scans external assets and reads project_info;
        # batch validation only needs the editor's settings snapshot.
        state_resp = await send_with_unity_instance(
            async_send_command_with_retry, unity_instance, "get_editor_state", {},
        )
        data = state_resp.data if hasattr(state_resp, "data") else (
            state_resp.get("data") if isinstance(state_resp, dict) else None
        )
        if isinstance(data, dict):
            settings = data.get("settings")
            if isinstance(settings, dict):
                limit = settings.get("batch_execute_max_commands")
                if type(limit) is int and 1 <= limit <= ABSOLUTE_MAX_COMMANDS_PER_BATCH:
                    if cache_key is not None:
                        if cache_key not in _cached_max_commands and len(_cached_max_commands) >= _LIMIT_CACHE_MAX_ENTRIES:
                            _cached_max_commands.pop(next(iter(_cached_max_commands)))
                        _cached_max_commands[cache_key] = (limit, time.monotonic() + _LIMIT_CACHE_TTL_SECONDS)
                    return limit
    except Exception as exc:
        logger.debug("Could not read batch limit from editor state: %s", exc)

    return DEFAULT_MAX_COMMANDS_PER_BATCH


def invalidate_cached_max_commands() -> None:
    """Reset the cached limit so the next call re-reads from editor state."""
    _cached_max_commands.clear()


@mcp_for_unity_tool(
    name="batch_execute",
    description=(
        "Executes multiple MCP commands in a single batch for dramatically better performance. "
        "STRONGLY RECOMMENDED when creating/modifying multiple objects, adding components to multiple targets, "
        "or performing any repetitive operations. Reduces latency and token costs by 10-100x compared to "
        "sequential tool calls. The max commands per batch is configurable in the Unity MCP Tools window "
        f"(default {DEFAULT_MAX_COMMANDS_PER_BATCH}, hard max {ABSOLUTE_MAX_COMMANDS_PER_BATCH}). "
        "Example: creating 5 cubes → use 1 batch_execute with 5 create commands instead of 5 separate calls."
    ),
    annotations=ToolAnnotations(
        title="Batch Execute",
        destructiveHint=True,
    ),
)
async def batch_execute(
    ctx: Context,
    commands: Annotated[list[dict[str, Any]], "List of commands with 'tool' and 'params' keys."],
    parallel: Annotated[bool | None,
                        "Attempt to run read-only commands in parallel"] = None,
    fail_fast: Annotated[bool | None,
                         "Stop processing after the first failure"] = None,
    max_parallelism: Annotated[int | None,
                               "Hint for the maximum number of parallel workers"] = None,
) -> dict[str, Any]:
    """Proxy the batch_execute tool to the Unity Editor transporter."""
    parallel = coerce_bool(parallel)
    fail_fast = coerce_bool(fail_fast)
    max_parallelism = coerce_int(max_parallelism)
    if not isinstance(commands, list) or not commands:
        raise ValueError(
            "'commands' must be a non-empty list of command specifications")

    if len(commands) > ABSOLUTE_MAX_COMMANDS_PER_BATCH:
        raise ValueError(
            f"batch_execute supports at most {ABSOLUTE_MAX_COMMANDS_PER_BATCH} commands; received {len(commands)}"
        )

    normalized_commands: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ValueError(
                f"Command at index {index} must be an object with 'tool' and 'params' keys")

        tool_name = command.get("tool")
        params = command.get("params", {})

        if not tool_name or not isinstance(tool_name, str):
            raise ValueError(
                f"Command at index {index} is missing a valid 'tool' name")

        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError(
                f"Command '{tool_name}' must specify parameters as an object/dict")

        if "unity_instance" in params:
            raise ValueError(
                f"Command '{tool_name}' at index {index} contains 'unity_instance'. "
                "Per-command instance routing is not supported inside batch_execute. "
                "Set unity_instance on the outer batch_execute call to route the entire batch."
            )

        normalized_commands.append({
            "tool": tool_name,
            "params": params,
        })

    unity_instance = await get_unity_instance_from_context(ctx)
    max_commands = await _get_max_commands_from_editor_state(ctx, unity_instance)
    if len(commands) > max_commands:
        raise ValueError(
            f"batch_execute supports up to {max_commands} commands (configured in Unity); received {len(commands)}"
        )

    payload: dict[str, Any] = {
        "commands": normalized_commands,
    }

    if parallel is not None:
        payload["parallel"] = parallel
    if fail_fast is not None:
        payload["failFast"] = fail_fast
    if max_parallelism is not None:
        payload["maxParallelism"] = max_parallelism

    return await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "batch_execute",
        payload,
    )
