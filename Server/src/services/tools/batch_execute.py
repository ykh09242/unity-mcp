"""Defines the batch_execute tool for orchestrating multiple Unity MCP commands."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from core.config import config
from models.response_limits import ResponseOwner, response_limit_error, response_owner
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.shared_tool_reads import SharedReadCapacityError, SharedToolReads
from services.tools.utils import coerce_bool, coerce_int
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import (
    async_send_command_with_retry,
    get_authenticated_stdio_generation,
)

logger = logging.getLogger(__name__)

# Fallback used when the Unity-side configured limit is not yet known.
DEFAULT_MAX_COMMANDS_PER_BATCH = 25

# Hard ceiling matching the C# AbsoluteMaxCommandsPerBatch.
ABSOLUTE_MAX_COMMANDS_PER_BATCH = 100

# Settings are mutable and belong to a selected user's editor instance.
_LIMIT_CACHE_TTL_SECONDS = 5.0
_LIMIT_CACHE_MAX_ENTRIES = 128
_cached_max_commands: dict[tuple[str, str | None, str, str | None], tuple[int, float]] = {}
_limit_cache_generation = 0
# Share only the validated setting; unrelated editor snapshots are request-local.
_limit_reads: SharedToolReads[int | None] = SharedToolReads(freshness_s=_LIMIT_CACHE_TTL_SECONDS)


async def _get_max_commands_from_editor_state(ctx: Context, unity_instance: str | None) -> int:
    """
    Attempt to read the configured batch limit from the Unity editor state.
    Falls back to DEFAULT_MAX_COMMANDS_PER_BATCH if unavailable.
    """
    cache_key = None
    if unity_instance:
        try:
            user_id = await ctx.get_state("user_id")
            http_session = (
                await ctx.get_state("unity_session_id")
                if config.transport_mode.lower() == "http"
                else await get_authenticated_stdio_generation(unity_instance)
            )
            if (user_id is None or isinstance(user_id, str)) and (
                not config.http_remote_hosted or bool(user_id)
            ):
                if isinstance(http_session, str) and bool(http_session):
                    cache_key = (config.transport_mode, user_id, unity_instance, http_session)
        except Exception:
            pass
    cached = _cached_max_commands.get(cache_key) if cache_key is not None else None
    if cached is not None and time.monotonic() < cached[1]:
        return cached[0]

    generation = _limit_cache_generation
    read_key = (generation, cache_key) if cache_key is not None else None

    async def fetch_editor_settings() -> int | None:
        # The enriched resource also scans external assets and reads project_info;
        # batch validation only needs one scalar from the editor's settings.
        raw_owner = ResponseOwner()
        token = response_owner.set(raw_owner)
        try:
            value = await send_with_unity_instance(
                async_send_command_with_retry,
                unity_instance,
                "get_editor_state",
                {},
            )
            value = value.model_dump() if hasattr(value, "model_dump") else value
            data = (
                value.data
                if hasattr(value, "data")
                else (value.get("data") if isinstance(value, dict) else None)
            )
            if isinstance(data, dict):
                settings = data.get("settings")
                if isinstance(settings, dict):
                    limit = settings.get("batch_execute_max_commands")
                    if type(limit) is int and 1 <= limit <= ABSOLUTE_MAX_COMMANDS_PER_BATCH:
                        return limit
            return None
        finally:
            response_owner.reset(token)
            raw_owner.release()

    try:
        async with _limit_reads.session(read_key) as shared_read:
            limit = await shared_read.fetch(fetch_editor_settings)
        if limit is not None:
            identity_current = (
                config.transport_mode.lower() == "http"
                or cache_key is not None
                and cache_key[3] == await get_authenticated_stdio_generation(unity_instance)
            )
            if cache_key is not None and generation == _limit_cache_generation and identity_current:
                if (
                    cache_key not in _cached_max_commands
                    and len(_cached_max_commands) >= _LIMIT_CACHE_MAX_ENTRIES
                ):
                    _cached_max_commands.pop(next(iter(_cached_max_commands)))
                _cached_max_commands[cache_key] = (
                    limit,
                    time.monotonic() + _LIMIT_CACHE_TTL_SECONDS,
                )
            return limit
    except SharedReadCapacityError:
        raise
    except Exception as exc:
        logger.debug("Could not read batch limit from editor state: %s", exc)

    return DEFAULT_MAX_COMMANDS_PER_BATCH


def invalidate_cached_max_commands() -> None:
    """Reset the cached limit so the next call re-reads from editor state."""
    global _limit_cache_generation
    _limit_cache_generation += 1
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
    parallel: Annotated[bool | None, "Attempt to run read-only commands in parallel"] = None,
    fail_fast: Annotated[bool | None, "Stop processing after the first failure"] = None,
    max_parallelism: Annotated[
        int | None, "Hint for the maximum number of parallel workers"
    ] = None,
) -> dict[str, Any]:
    """Proxy the batch_execute tool to the Unity Editor transporter."""
    parallel = coerce_bool(parallel)
    fail_fast = coerce_bool(fail_fast)
    max_parallelism = coerce_int(max_parallelism)
    if not isinstance(commands, list) or not commands:
        raise ValueError("'commands' must be a non-empty list of command specifications")

    if len(commands) > ABSOLUTE_MAX_COMMANDS_PER_BATCH:
        raise ValueError(
            f"batch_execute supports at most {ABSOLUTE_MAX_COMMANDS_PER_BATCH} commands; received {len(commands)}"
        )

    normalized_commands: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ValueError(
                f"Command at index {index} must be an object with 'tool' and 'params' keys"
            )

        tool_name = command.get("tool")
        params = command.get("params", {})

        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"Command at index {index} is missing a valid 'tool' name")
        if tool_name.casefold() == "batch_execute":
            raise ValueError("Nested batch_execute commands are not allowed")

        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError(f"Command '{tool_name}' must specify parameters as an object/dict")

        if "unity_instance" in params:
            raise ValueError(
                f"Command '{tool_name}' at index {index} contains 'unity_instance'. "
                "Per-command instance routing is not supported inside batch_execute. "
                "Set unity_instance on the outer batch_execute call to route the entire batch."
            )

        normalized_commands.append(
            {
                "tool": tool_name,
                "params": params,
            }
        )

    unity_instance = await get_unity_instance_from_context(ctx)
    try:
        max_commands = await _get_max_commands_from_editor_state(ctx, unity_instance)
    except SharedReadCapacityError:
        return response_limit_error("result_capacity")
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
