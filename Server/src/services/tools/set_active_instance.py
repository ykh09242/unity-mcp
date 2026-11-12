from typing import Annotated, Any
from types import SimpleNamespace

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from transport.legacy.unity_connection import get_unity_connection_pool
from transport.unity_instance_middleware import (
    get_unity_instance_middleware,
    resolve_discovered_instance,
)
from transport.plugin_hub import PluginHub
from services.execution_receipt_context import owned_to_thread
from core.config import config
from transport.protocol import is_sessionless
from pydantic import Field


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description="Set the active Unity instance for this client/session. Accepts Name@hash, hash prefix, or port number (stdio only).",
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
) -> dict[str, Any]:
    value = (instance or "").strip()
    if not value:
        return {
            "success": False,
            "error": "Instance identifier is required. "
            "Use mcpforunity://instances to copy a Name@hash or provide a hash prefix.",
        }
    if is_sessionless(ctx):
        return {
            "success": False,
            "error": "This MCP protocol is sessionless, so active instance selection cannot persist. "
            "Pass unity_instance on each tool call, or use a client with a stateful MCP handshake.",
        }
    transport = (config.transport_mode or "stdio").lower()

    # Discover running instances based on transport
    if transport == "http":
        # In remote-hosted mode, filter sessions by user_id
        user_id = (await ctx.get_state("user_id")) if config.http_remote_hosted else None
        sessions_data = await PluginHub.get_sessions(user_id=user_id)
        sessions = sessions_data.sessions
        instances = []
        for session in sessions.values():
            project = session.project or "Unknown"
            hash_value = session.hash
            if not hash_value:
                continue
            inst_id = f"{project}@{hash_value}"
            instances.append(
                SimpleNamespace(
                    id=inst_id,
                    hash=hash_value,
                    name=project,
                )
            )
    else:
        pool = get_unity_connection_pool()
        instances = await owned_to_thread(pool.discover_all_instances, force_refresh=True)

    if not instances and not value.isdigit():
        return {
            "success": False,
            "error": "No Unity instances are currently connected. Start Unity and press 'Start Session'.",
        }
    try:
        resolved = resolve_discovered_instance(value, instances, transport)
    except ValueError as error:
        return {"success": False, "error": str(error)}

    # Store selection in middleware (session-scoped via FastMCP context state).
    middleware = get_unity_instance_middleware()
    await middleware.set_active_instance(ctx, resolved)

    result = {
        "success": True,
        "message": f"Active instance set to {resolved}",
        "data": {
            "instance": resolved,
            "session_id": getattr(ctx, "session_id", None),
        },
    }
    if include_context:
        result["data"]["context"] = await selected_context(ctx, resolved)
    return result


async def selected_context(ctx, instance):
    from services.operation_permissions import authorize_remote_operation
    from models.response_lifetime import ResponsePollLifetime
    from services.tools.wait_for_operation import capture_target, fetch_observation
    from models.unity_response import normalize_unity_response
    import asyncio

    try:
        async with asyncio.timeout(3):
            await authorize_remote_operation("get_editor_state", {}, ctx=ctx)
            target = await capture_target(ctx)
            if target is None or target.instance != instance:
                return {"status": "unavailable", "reason": "selected_editor_identity_unavailable"}
            async with ResponsePollLifetime() as lifetime:
                response = await lifetime.fetch(
                    fetch_observation(target, "get_editor_state", {}), 2
                )
            response = normalize_unity_response(response)
            data = response.get("data") if isinstance(response, dict) else None
            if not isinstance(data, dict) or response.get("success") is not True:
                return {"status": "unavailable", "reason": "editor_state_unavailable"}
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
                "instance": instance,
                "generation": target.generation,
                "editor_state": fields,
                "readiness": "unobserved",
                "capabilities_resource": "mcpforunity://capabilities",
            }
    except Exception as error:
        return {"status": "unavailable", "reason": type(error).__name__}
