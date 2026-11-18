import asyncio
from typing import Annotated, Any
from types import SimpleNamespace

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from transport.legacy.unity_connection import get_unity_connection_pool, run_discovery
from transport.unity_instance_middleware import (
    get_unity_instance_middleware,
    resolve_discovered_instance,
)
from transport.plugin_hub import PluginHub
from core.config import config
from transport.protocol import is_sessionless
from pydantic import Field


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description="Select a Unity instance by Name@hash, hash prefix, or stdio port. On sessionless clients, include_context returns a request-scoped observation.",
    annotations=ToolAnnotations(
        title="Set Active Instance",
        # Changes session-local routing only; touches nothing in the project.
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def set_active_instance(
    ctx: Context,
    instance: Annotated[
        str, "Target instance (Name@hash, hash prefix, or port number in stdio mode)"
    ],
    include_context: Annotated[
        bool, Field(strict=True), "Attach a bounded read-only snapshot of the selected Editor."
    ] = False,
    evidence_needed: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=256)]] | None,
        Field(
            max_length=8,
            description="Caller-supplied checks still needed by the receiving agent. Supplying this includes context.",
        ),
    ] = None,
) -> dict[str, Any]:
    value = (instance or "").strip()
    if not value:
        return {
            "success": False,
            "error": "Instance identifier is required. "
            "Use mcpforunity://instances to copy a Name@hash or provide a hash prefix.",
        }
    sessionless = is_sessionless(ctx)
    with_context = include_context or evidence_needed is not None
    if sessionless and not with_context:
        return {
            "success": False,
            "error": "This MCP protocol is sessionless, so active instance selection cannot persist. "
            "Pass unity_instance on each tool call, or use a client with a stateful MCP handshake.",
        }
    transport = (config.transport_mode or "stdio").lower()
    user_id = None
    sessions_by_instance = {}

    # Discover running instances based on transport
    if transport == "http":
        # In remote-hosted mode, filter sessions by user_id
        user_id = (await ctx.get_state("user_id")) if config.http_remote_hosted else None
        sessions_data = await PluginHub.get_sessions(user_id=user_id)
        sessions = sessions_data.sessions
        instances = []
        for session_id, session in sessions.items():
            project = session.project or "Unknown"
            hash_value = session.hash
            if not hash_value:
                continue
            inst_id = f"{project}@{hash_value}"
            sessions_by_instance[inst_id] = session_id
            instances.append(
                SimpleNamespace(
                    id=inst_id,
                    hash=hash_value,
                    name=project,
                )
            )
    else:
        pool = get_unity_connection_pool()
        instances = await run_discovery(pool.discover_all_instances, force_refresh=True)

    if not instances and not value.isdigit():
        return {
            "success": False,
            "error": "No Unity instances are currently connected. Start Unity and press 'Start Session'.",
        }
    try:
        resolved = resolve_discovered_instance(value, instances, transport)
    except ValueError as error:
        return {"success": False, "error": str(error)}

    target = None
    if with_context:
        from services.tools.wait_for_operation import capture_observation_target

        deadline = asyncio.get_running_loop().time() + 3
        # Pin the chosen route before the session write yields to a possible reconnect.
        try:
            async with asyncio.timeout_at(deadline):
                target = await capture_observation_target(
                    resolved, sessions_by_instance.get(resolved), user_id
                )
        except Exception:
            pass  # Unavailable optional observation must not prevent selection.

    if sessionless:
        result = {
            "success": True,
            "message": f"Resolved {resolved} for this observation.",
            "data": {"instance": resolved, "selection_persisted": False},
        }
    else:
        middleware = get_unity_instance_middleware()
        await middleware.set_active_instance(ctx, resolved)
        result = {
            "success": True,
            "message": f"Active instance set to {resolved}",
            "data": {"instance": resolved, "session_id": getattr(ctx, "session_id", None)},
        }
    if with_context:
        context, observation = await selected_context(target, deadline)
        result["data"]["context"] = context
        result["data"]["handoff"] = {
            "instance": resolved,
            "generation": context.get("generation"),
            "status": context["status"],
            "source": "live_editor" if observation is not None else "unavailable",
            "saved_state": "unobserved",
            **handoff_observation(observation),
            "evidence_needed": evidence_needed or [],
            "evidence_needed_source": "caller",
        }
        if sessionless and observation is None:
            result["success"] = False
            result["error"] = context["reason"]
            result["message"] = f"Could not observe {resolved}."
    return result


async def selected_context(target, deadline):
    from services.operation_permissions import authorize_remote_operation
    from models.response_lifetime import ResponsePollLifetime
    from services.tools.wait_for_operation import fetch_observation
    from models.unity_response import normalize_unity_response

    if target is None:
        return {"status": "unavailable", "reason": "selected_editor_identity_unavailable"}, None
    try:
        async with asyncio.timeout_at(deadline):
            await authorize_remote_operation("get_editor_state", {}, session_id=target.session_id)
            async with ResponsePollLifetime() as lifetime:
                response = await lifetime.fetch(
                    fetch_observation(target, "get_editor_state", {}), 2
                )
            response = normalize_unity_response(response)
            data = response.get("data") if isinstance(response, dict) else None
            if not isinstance(data, dict) or response.get("success") is not True:
                if (
                    isinstance(data, dict)
                    and data.get("reason") == "observation_generation_changed"
                ):
                    return {
                        "status": "unavailable",
                        "reason": "observation_generation_changed",
                    }, None
                return {"status": "unavailable", "reason": "editor_state_unavailable"}, None
            unity = data.get("unity")
            if isinstance(unity, dict) and unity.get("instance_id") not in (
                None,
                "",
                target.instance,
            ):
                return {"status": "unavailable", "reason": "editor_instance_mismatch"}, None
            fields = {}
            for section, keys in (
                ("unity", ("unity_version", "platform", "is_batch_mode")),
                ("activity", ("phase", "since_unix_ms")),
                ("compilation", ("is_compiling", "is_domain_reload_pending")),
                ("assets", ("is_updating", "external_changes_dirty")),
                ("native_coordination", ("blocked", "reason", "recovery_action")),
            ):
                values = data.get(section)
                if isinstance(values, dict):
                    fields[section] = {
                        key: values[key][:256] if isinstance(values[key], str) else values[key]
                        for key in keys
                        if type(values.get(key)) in (bool, int, str)
                    }
            return {
                "status": "observed",
                "instance": target.instance,
                "generation": target.generation,
                "editor_state": fields,
                "readiness": "unobserved",
                "capabilities_resource": "mcpforunity://capabilities",
            }, data
    except Exception as error:
        return {"status": "unavailable", "reason": type(error).__name__}, None


def handoff_observation(observation: dict | None) -> dict:
    """Project identity from one native observation, preserving missing fields from older peers."""
    state = observation or {}
    editor = state.get("editor")
    editor = editor if isinstance(editor, dict) else {}
    play = editor.get("play_mode")
    mode = "unknown"
    if isinstance(play, dict):
        if play.get("is_changing") is True:
            mode = "transitioning"
        elif play.get("is_changing") is False and type(play.get("is_playing")) is bool:
            mode = "play" if play["is_playing"] else "edit"
    scene = editor.get("active_scene")
    scene = scene if isinstance(scene, dict) else {}
    scene_identity = {}
    for key, limit in (("path", 4096), ("guid", 64), ("name", 256)):
        value = scene.get(key)
        scene_identity[key] = value if isinstance(value, str) and 0 < len(value) <= limit else None
    handle = scene.get("handle")
    # Older Editors expose Int32; Unity 6.4+ exposes the raw UInt64 scene identity.
    if type(handle) is int and -(2**31) <= handle < 2**64 and handle != 0:
        scene_identity["handle"] = handle
    epoch = scene.get("lifetime_epoch")
    if isinstance(epoch, str) and len(epoch) == 32 and all(c in "0123456789abcdef" for c in epoch):
        scene_identity["lifetime_epoch"] = epoch
    observed_at = state.get("observed_at_unix_ms")
    if type(observed_at) is not int or observed_at <= 0:
        observed_at = None
    unity = state.get("unity")
    version = unity.get("unity_version") if isinstance(unity, dict) else None
    return {
        **(
            {"unity_version": version}
            if isinstance(version, str) and 0 < len(version) <= 256
            else {}
        ),
        "mode": mode,
        "active_scene": scene_identity,
        "observed_at_unix_ms": observed_at,
        "freshness": "point_in_time" if observed_at is not None else "unknown",
    }
