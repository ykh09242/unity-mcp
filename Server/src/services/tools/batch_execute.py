"""Defines the batch_execute tool for orchestrating multiple Unity MCP commands."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from core.config import config
from models.response_limits import ResponseOwner, response_limit_error, response_owner
from services.credential_lease import CredentialLeaseError, require_credential_lease
from services.command_progress import claim_command_progress
from services.operation_permissions import authorize_remote_operation, remote_operation_allowed
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
DEFAULT_MAX_COMMANDS_PER_BATCH = 100

# Hard ceiling matching the C# AbsoluteMaxCommandsPerBatch.
ABSOLUTE_MAX_COMMANDS_PER_BATCH = 1000

# Settings are mutable and belong to a selected user's editor instance.
_LIMIT_CACHE_TTL_SECONDS = 5.0
_LIMIT_CACHE_MAX_ENTRIES = 128
_cached_max_commands: dict[tuple, tuple[int, float]] = {}
_limit_cache_generation = 0
# Share only the validated setting; unrelated editor snapshots are request-local.
_limit_reads: SharedToolReads[int | None] = SharedToolReads(freshness_s=_LIMIT_CACHE_TTL_SECONDS)


async def _get_max_commands_from_editor_state(ctx: Context, unity_instance: str | None) -> int:
    """
    Attempt to read the configured batch limit from the Unity editor state.
    Falls back to DEFAULT_MAX_COMMANDS_PER_BATCH if unavailable.
    """
    if not await remote_operation_allowed("get_editor_state", {}, ctx=ctx):
        return DEFAULT_MAX_COMMANDS_PER_BATCH
    lease = (
        await require_credential_lease()
        if config.http_remote_hosted and config.transport_mode.lower() == "http"
        else None
    )
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
    if lease is not None and cache_key is not None:
        cache_key = (lease.service, lease.generation, *cache_key)
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
    except CredentialLeaseError:
        raise
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


def _preview_reply_is_exact(
    reply: Any, commands: list[dict[str, Any]], scope: str = "action_names"
) -> bool:
    """Accept only the dedicated peer's complete, ordered action-name receipt."""
    if not isinstance(reply, dict) or type(reply.get("success")) is not bool:
        return False
    data = reply.get("data")
    validation = data.get("validation") if isinstance(data, dict) else None
    if not isinstance(validation, dict) or set(validation) != {
        "contractVersion",
        "validateOnly",
        "scope",
        "complete",
        "valid",
        "items",
    }:
        return False
    if (
        type(validation["contractVersion"]) is not int
        or validation["contractVersion"] != 1
        or validation["validateOnly"] is not True
        or validation["scope"] != scope
    ):
        return False
    if type(validation["complete"]) is not bool or type(validation["valid"]) is not bool:
        return False
    items = validation["items"]
    if not isinstance(items, list) or len(items) != len(commands):
        return False
    allowed_reasons = {
        "valid": {"ok"},
        "incomplete": {"action_validation_unavailable"},
        "rejected": {
            "invalid_batch",
            "batch_disabled",
            "unknown_command",
            "resource_disabled",
            "tool_disabled",
            "invalid_action",
            "missing_action",
            "unsupported_action",
            "action_moved",
        },
    }
    if scope == "targets":
        allowed_reasons["incomplete"] = {
            "unsupported_target_preview",
            "dependent_target",
            "editor_not_stable",
            "prepare_prefab_plan_first",
            "directory_move_not_modeled",
            "destination_parent_not_present",
            "concrete_instance_id_required",
            "unsupported_parameters",
        }
        allowed_reasons["rejected"] = {"invalid_parameters_or_target"}
    for index, (item, command) in enumerate(zip(items, commands)):
        if not isinstance(item, dict) or set(item) != {
            "index",
            "tool",
            "toolTruncated",
            "status",
            "reason",
        }:
            return False
        if (
            type(item["index"]) is not int
            or item["index"] != index
            or type(item["toolTruncated"]) is not bool
        ):
            return False
        raw_name = command["tool"].encode("utf-16-le", errors="surrogatepass")
        expected_name = raw_name[:256].decode("utf-16-le", errors="surrogatepass")
        if (
            type(item["tool"]) is not str
            or item["tool"] != expected_name
            or item["toolTruncated"] != (len(raw_name) > 256)
        ):
            return False
        status, reason = item["status"], item["reason"]
        if (
            type(status) is not str
            or type(reason) is not str
            or reason not in allowed_reasons.get(status, set())
        ):
            return False
    complete = all(item["status"] != "incomplete" for item in items)
    valid = all(item["status"] == "valid" for item in items)
    if scope == "targets":
        guard = data.get("preview")
        if not valid:
            if guard is not None:
                return False
        elif (
            not isinstance(guard, dict)
            or set(guard) != {"contractVersion", "commandsHash", "revisions", "signature"}
            or type(guard["contractVersion"]) is not int
            or guard["contractVersion"] != 1
            or not isinstance(guard["revisions"], list)
            or len(guard["revisions"]) != len(commands)
            or any(
                not isinstance(value, str) or len(value) != 44
                for value in [guard["commandsHash"], guard["signature"], *guard["revisions"]]
            )
        ):
            return False
    return (
        validation["complete"] == complete
        and validation["valid"] == valid
        and reply["success"] == valid
    )


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
    validate_only: Annotated[
        bool,
        "Validate action names without executing children; requires the peer preview capability",
    ] = False,
    preview_scope: Annotated[
        Literal["action_names", "targets"],
        "With validate_only, inspect action names or supported concrete targets",
    ] = "action_names",
    preview: Annotated[
        dict[str, Any] | None,
        "Unmodified target-preview guard to recheck before supported mutations; no rollback or retry guarantee",
    ] = None,
) -> dict[str, Any]:
    """Execute sequentially, or request a read-only action-name preview.

    Preview uses a distinct native command and the same full member authorization.
    The opt-in targets scope validates supported inputs, targets and revisions;
    unsupported/future dependencies stay incomplete. Applying its guard uses a
    distinct peer command and rechecks every target and permission. Ordinary
    settled responses add indexed outcomes; cancellation/lost replies retain their
    existing unknown-outcome contract and never imply rollback.
    """
    if type(validate_only) is not bool:
        raise ValueError("validate_only must be a boolean")
    if preview_scope not in ("action_names", "targets"):
        raise ValueError("preview_scope must be action_names or targets")
    if preview_scope != "action_names" and not validate_only:
        raise ValueError("preview_scope=targets requires validate_only")
    if preview is not None and (not isinstance(preview, dict) or validate_only):
        raise ValueError("preview must be an object and cannot be combined with validate_only")
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
        if tool_name.casefold() in {"batch_execute", "batch_validate", "batch_apply_preview"}:
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

    # The entire normalized batch is admitted before context/settings/preflight
    # work. A later forbidden member cannot leave earlier native side effects.
    await authorize_remote_operation("batch_execute", {"commands": normalized_commands}, ctx=ctx)
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

    if validate_only and preview_scope == "targets":
        payload["previewScope"] = "targets"
    if preview is not None:
        payload["preview"] = preview
    native_command = (
        "batch_validate"
        if validate_only
        else "batch_apply_preview"
        if preview is not None
        else "batch_execute"
    )
    progress = None if validate_only else claim_command_progress(len(normalized_commands))
    reply = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        native_command,
        payload,
        **({"_progress": progress} if progress is not None else {}),
    )
    if validate_only and not _preview_reply_is_exact(reply, normalized_commands, preview_scope):
        if isinstance(reply, dict) and reply.get("success") is False:
            data = reply.get("data")
            if reply.get("error") in ("permission_denied", "auth_required") or (
                isinstance(data, dict) and data.get("reason") == "outcome_unknown"
            ):
                return reply
        return {
            "success": False,
            "error": "batch_validation_unavailable",
            "message": "The selected peer did not provide the requested dedicated batch validation contract; no execution fallback was attempted.",
            "data": {"reason": "batch_validation_unavailable"},
        }
    return reply
