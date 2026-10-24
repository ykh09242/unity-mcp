"""
manage_tools - server-only meta-tool for dynamic tool group activation.

This tool lets the AI assistant (or user) discover available tool groups
and selectively enable / disable them for the current session. Activating
a group makes its tools appear in tool listings; deactivating hides them.

Legacy selection follows the connection. Modern selection requires a trusted
client scope and resolved project; static discovery profiles work without one.
Hosted remote availability follows the authenticated user's Unity catalog;
session group activation cannot grant access to a Unity-disabled tool.
"""

import logging
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from transport.protocol import is_sessionless

from services.registry import (
    mcp_for_unity_tool,
    TOOL_GROUPS,
    DEFAULT_ENABLED_GROUPS,
    get_group_tool_names,
    get_registered_tools,
)

logger = logging.getLogger("mcp-for-unity-server")


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Manage visible groups or individual feature tools in this session. "
        "Actions: list_groups (show all groups and their status), "
        "activate/deactivate (select exactly one group or tool), "
        "sync (refresh availability from Unity Editor's toggle states), "
        "reset (restore configured profile), describe (full schema for one tool). "
        "Activating a group makes its tools appear; deactivating hides them. "
        "Use sync after toggling tools in the Unity Editor GUI."
    ),
    annotations=ToolAnnotations(
        title="Manage Tools",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def manage_tools(
    ctx: Context,
    action: Annotated[
        Literal["list_groups", "activate", "deactivate", "sync", "reset", "describe"],
        "Action to perform.",
    ],
    group: Annotated[
        str | None,
        "Group name for activate/deactivate; select a group or a single tool. "
        "Valid groups: " + ", ".join(sorted(TOOL_GROUPS.keys())),
    ] = None,
    tool: Annotated[
        str | None,
        "Single tool name for activate/deactivate/describe. Discover names with list_groups.",
    ] = None,
) -> dict[str, Any]:
    from services.tool_discovery import REFRESH_GUIDANCE, UNSCOPED_GUIDANCE

    discovery = getattr(ctx.fastmcp, "_tool_discovery", None)
    modern = is_sessionless(ctx)
    if action in ("activate", "deactivate", "reset") and modern:
        if discovery is None or await discovery.scope_key(ctx) is None:
            return {"error": UNSCOPED_GUIDANCE}
    if action == "describe":
        if group is not None or not tool:
            return {"error": "describe requires a tool name and no group."}
        definition = discovery.definitions.get(tool.strip()) if discovery is not None else None
        if definition is None:
            return {"error": "Unknown built-in tool. Use list_groups to discover names."}
        return {
            "tool": definition.to_mcp_tool().model_dump(mode="json", exclude_none=True),
            "note": "Description only; selection and permissions are unchanged.",
        }
    if action == "list_groups":
        return await _list_groups(ctx)

    if action in ("activate", "deactivate") and tool is not None:
        if group is not None:
            return {"error": "Select exactly one group or tool."}
        tool = tool.strip()
        entry = next((item for item in get_registered_tools() if item["name"] == tool), None)
        if entry is None or entry.get("group") is None:
            return {
                "error": "Unknown or always-visible tool. Use list_groups to discover selectable tools."
            }
        operation = ctx.enable_components if action == "activate" else ctx.disable_components
        if discovery is not None:
            changed = await discovery.update(ctx, [tool], enabled=action == "activate")
        else:
            await operation(names={tool}, components={"tool"})
            changed = True
        return {
            "activated_tool" if action == "activate" else "deactivated_tool": tool,
            "changed": changed,
            "message": REFRESH_GUIDANCE,
        }

    if action in ("activate", "deactivate"):
        if not group:
            return {"error": f"group or tool is required for {action}"}
        group = group.strip().lower()
        if group not in TOOL_GROUPS:
            return {"error": f"Unknown group '{group}'. Valid: {', '.join(sorted(TOOL_GROUPS))}"}

    if action == "activate":
        tag = f"group:{group}"
        logger.info("Activating tool group: %s", group)
        if discovery is not None:
            changed = await discovery.update(
                ctx, get_group_tool_names().get(group, []), enabled=True
            )
        else:
            await ctx.enable_components(tags={tag}, components={"tool"})
            changed = True
        return {
            "activated": group,
            "tools": get_group_tool_names().get(group, []),
            "changed": changed,
            "message": REFRESH_GUIDANCE,
        }

    if action == "deactivate":
        tag = f"group:{group}"
        logger.info("Deactivating tool group: %s", group)
        if discovery is not None:
            changed = await discovery.update(
                ctx, get_group_tool_names().get(group, []), enabled=False
            )
        else:
            await ctx.disable_components(tags={tag}, components={"tool"})
            changed = True
        return {
            "deactivated": group,
            "tools": get_group_tool_names().get(group, []),
            "changed": changed,
            "message": REFRESH_GUIDANCE,
        }

    if action == "sync":
        logger.info("Syncing tool visibility from Unity Editor...")
        from services.tools import get_unity_instance_from_context, sync_tool_visibility_from_unity

        instance_id = await get_unity_instance_from_context(ctx)
        result = await sync_tool_visibility_from_unity(instance_id=instance_id, notify=True)
        if result.get("error"):
            msg = result["error"]
            if result.get("remote_sync_disabled"):
                return {"error": msg}
            if result.get("unsupported"):
                msg = (
                    "The connected Unity Editor does not support tool state syncing yet. "
                    "Update the MCPForUnity package to the latest version, then try again. "
                    "In the meantime, use activate/deactivate actions to toggle groups manually."
                )
            else:
                msg = f"Failed to sync tool visibility from Unity. Is Unity running? ({msg})"
            return {"error": msg}
        return {
            "synced": True,
            "enabled_groups": result.get("enabled_groups", []),
            "disabled_groups": result.get("disabled_groups", []),
            "enabled_tool_count": result.get("enabled_tool_count", 0),
            "total_tool_count": result.get("total_tool_count", 0),
            **({"settings_sync": result["settings_sync"]} if "settings_sync" in result else {}),
            **(
                {"notification_status": result["notification_status"]}
                if "notification_status" in result
                else {}
            ),
            "message": (
                "Unity availability refreshed; client selection is unchanged. "
                f"Enabled groups: {', '.join(result.get('enabled_groups', []))}. "
                f"Disabled groups: {', '.join(result.get('disabled_groups', []) or ['none'])}."
            ),
        }

    if action == "reset":
        logger.info("Resetting tool visibility to defaults")
        if discovery is not None:
            await discovery.update(ctx, reset=True)
        else:
            await ctx.reset_visibility()
        visible = await _visible_tools(ctx)
        return {
            "reset": True,
            "default_groups": sorted(
                discovery.default_groups if discovery else DEFAULT_ENABLED_GROUPS
            ),
            "visible_tools": sorted(tool.name for tool in visible),
            "message": "Client selection restored to configured server defaults. Unity availability still applies.",
        }

    return {"error": f"Unknown action '{action}'"}


async def _visible_tools(ctx: Context):
    """Read the same final availability cap used by tools/list."""
    # Server/session transforms and authenticated Unity catalogs both affect visibility.
    # Reuse this request's resolved routing instead of reinjecting it via tools/list.
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    visible_tools = await ctx.fastmcp.list_tools(run_middleware=False)
    for middleware in ctx.fastmcp.middleware:
        if isinstance(middleware, UnityInstanceMiddleware):
            visible_tools = await middleware.filter_tools_for_context(ctx, visible_tools)
    return visible_tools


async def _list_groups(ctx: Context) -> dict[str, Any]:
    """Build the list_groups response with group metadata and tool names."""
    discovery = getattr(ctx.fastmcp, "_tool_discovery", None)
    defaults = discovery.default_groups if discovery else DEFAULT_ENABLED_GROUPS
    group_tools = get_group_tool_names()
    visible_names = {tool.name for tool in await _visible_tools(ctx)}

    groups = []
    for name in sorted(TOOL_GROUPS.keys()):
        currently_enabled = bool(visible_names.intersection(group_tools.get(name, [])))
        groups.append(
            {
                "name": name,
                "description": TOOL_GROUPS[name],
                "enabled": currently_enabled,
                "default_enabled": name in defaults,
                "tools": group_tools.get(name, []),
                "enabled_tools": sorted(visible_names.intersection(group_tools.get(name, []))),
                "tool_count": len(group_tools.get(name, [])),
            }
        )
    return {
        "groups": groups,
        "profile": discovery.profile if discovery else "core",
        **({"discovery": await discovery.capability(ctx)} if discovery else {}),
        "note": (
            "A group is enabled when at least one of its registered tools is currently visible. "
            "Use activate/deactivate with a stateful handshake or trusted project-bound client scope. "
            "Tools with group=None (server meta-tools) are always visible."
        ),
    }
