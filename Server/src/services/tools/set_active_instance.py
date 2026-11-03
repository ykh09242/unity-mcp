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

    return {
        "success": True,
        "message": f"Active instance set to {resolved}",
        "data": {
            "instance": resolved,
            "session_id": getattr(ctx, "session_id", None),
        },
    }
