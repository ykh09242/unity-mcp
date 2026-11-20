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
from pydantic import Field, ValidationError
from transport.protocol import is_sessionless

from services.registry import (
    mcp_for_unity_tool,
    TOOL_GROUPS,
    DEFAULT_ENABLED_GROUPS,
    get_group_tool_names,
    get_registered_tools,
)
from services.registry.action_catalog import ActionIntent, CatalogResource, ObservationSource
from services.registry.task_search import HostToolNames, RequiredValues, RouteSurfaces

logger = logging.getLogger("mcp-for-unity-server")


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Search Unity tools, actions and readable resources without changing selection or invoking "
        "Unity. Choose observation_source and required_values by meaning in any language, or search "
        "catalog text. Inspect route_groups for invocations and evidence limits, or no_match for scoped alternatives."
    ),
    annotations=ToolAnnotations(
        title="Search Unity Tools",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def search_unity_tools(
    ctx: Context,
    query: Annotated[
        str | None, Field(max_length=512), "Optional original task or lexical query."
    ] = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=20), "Maximum results per page."] = 5,
    catalog_query: Annotated[
        str | None, Field(max_length=512), "Optional alternate catalog terms for a lexical query."
    ] = None,
    intent: Annotated[ActionIntent | None, "Requested operation purpose."] = None,
    observation_source: Annotated[
        ObservationSource | None, "Origin of the required evidence."
    ] = None,
    required_values: Annotated[
        RequiredValues | None, "Select every result field the observation must supply."
    ] = None,
    route_surfaces: Annotated[
        RouteSurfaces | None, "Group tools, resources or both (default)."
    ] = None,
    host_tool_names: Annotated[
        HostToolNames | None,
        "Complete callable tool names exposed by this host for this connection. Omit if unknown.",
    ] = None,
) -> dict[str, Any]:
    from services.registry.task_search import SearchRequest, search_catalog

    try:
        request = SearchRequest(
            query=query,
            catalog_query=catalog_query,
            limit=limit,
            intent=intent,
            observation_source=observation_source,
            required_values=required_values,
            route_surfaces=route_surfaces or "both",
            host_tool_names=host_tool_names,
        )
    except ValidationError:
        return {
            "success": False,
            "error": "Supply query (1..512 nonblank characters), observation_source or 1..32 required_values. Use a declared source and values, optional catalog_query (1..512 characters), and limit 1..20.",
        }
    entries, visible = await _catalog_inventory(
        ctx, get_registered_tools(), getattr(ctx.fastmcp, "_tool_discovery", None)
    )
    return search_catalog(
        request,
        list(entries.values()),
        visible_names=set(visible),
        resources=await _catalog_resources(ctx),
    )


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Find Unity tools, actions and readable resources for a task; inspect schemas or manage visible groups. "
        "Actions: list_groups (show all groups and their status), "
        "activate/deactivate (select exactly one group or tool), "
        "sync (refresh availability from Unity Editor's toggle states), "
        "reset (restore configured profile), describe (full schema for one tool, optionally bind operation), "
        "catalog (choose a domain by purpose, then browse tools, actions or readable resources), "
        "search (match declared observation_source and required_values directly from a task in any language, or search by text). "
        "Selection changes server tools/list; it does not confirm the client's callable inventory. "
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
        Literal[
            "list_groups",
            "activate",
            "deactivate",
            "sync",
            "reset",
            "describe",
            "search",
            "catalog",
            "inspect_inventory",
        ],
        "Action to perform.",
    ],
    group: Annotated[
        str | None,
        "Group name for activate/deactivate/catalog. Catalog also supports resources for fixed URIs "
        "and resource_templates for parameterized Unity observations. "
        "Valid groups: " + ", ".join(sorted(TOOL_GROUPS.keys())),
    ] = None,
    tool: Annotated[
        str | None,
        "Single tool name for activate/deactivate/describe/catalog. Discover names with catalog.",
    ] = None,
    query: Annotated[
        str | None,
        Field(max_length=512),
        "Optional original task text when searching by observation, or a lexical query (1..512 characters).",
    ] = None,
    limit: Annotated[
        int, Field(strict=True, ge=1, le=20), "Maximum search or catalog page entries (1..20)."
    ] = 5,
    include_schema: Annotated[
        bool,
        Field(strict=True),
        "On single-tool activation, attach its schema and readiness lookup.",
    ] = False,
    catalog_query: Annotated[
        str | None,
        Field(max_length=512),
        "Optional alternate terms for literal search, such as a known API identifier. The complete catalog is available for requests in any language.",
    ] = None,
    offset: Annotated[
        int, Field(strict=True, ge=0), "Catalog page offset for a selected group or tool."
    ] = 0,
    operation: Annotated[
        str | None,
        Field(min_length=1, max_length=128),
        "For describe, select one exact action from catalog. The returned full schema binds that action.",
    ] = None,
    intent: Annotated[
        ActionIntent | None,
        "Optional purpose interpreted from any language. For search or catalog with a tool/group: observe state, interact with the running game/editor, or edit project content. Classified routes only; inspect action effects separately.",
    ] = None,
    observation_source: Annotated[
        ObservationSource | None,
        Field(
            description="For search, interpret the request in its own language. Select where the evidence must originate: current Editor state (editor_live), saved file contents (asset_file), Unity import results (imported_asset), or a retained compilation request (compile_job)."
        ),
    ] = None,
    required_values: Annotated[
        RequiredValues | None,
        Field(
            description="For search, select every result field the request needs. Game View pixels use render_size.width and render_size.height; saved graph totals use nodeCount and edgeCount. A route must declare all selected values. No query or translation is needed."
        ),
    ] = None,
    route_surfaces: Annotated[
        RouteSurfaces | None,
        "For search, choose both (default), tools, or resources for route_groups. Use tools for a "
        "tools-only client. Each group offers a representative and available alternatives.",
    ] = None,
    host_tool_names: Annotated[
        HostToolNames | None,
        "For search or inspect_inventory, supply this host's complete callable tool names. Omit if unknown.",
    ] = None,
) -> dict[str, Any]:
    from services.tool_discovery import REFRESH_GUIDANCE, UNSCOPED_GUIDANCE

    discovery = getattr(ctx.fastmcp, "_tool_discovery", None)
    modern = is_sessionless(ctx)
    if host_tool_names is not None and action not in {"search", "inspect_inventory"}:
        return {"success": False, "error": "host_tool_names requires search or inspect_inventory."}
    if action == "inspect_inventory":
        if not tool or len(tool) > 128:
            return {"success": False, "error": "Inspect one exact tool name (1..128 characters)."}
        # Only report the queried name's membership. Catalog definitions include
        # selectable but denied entries and must never populate this receipt.
        advertised = any(item.name == tool for item in await _visible_tools(ctx))
        return {
            "scope": "current_mcp_request",
            "server_advertised": advertised,
            "host_evidence": "caller_report" if host_tool_names is not None else "unknown",
            "status": (
                "server_unavailable"
                if not advertised
                else "host_unknown"
                if host_tool_names is None
                else "host_advertised"
                if tool in host_tool_names
                else "host_filtered"
            ),
        }
    if intent is not None and action not in {"catalog", "search"}:
        return {"success": False, "error": "intent is supported only for search or catalog."}
    if (observation_source is not None or required_values is not None) and action != "search":
        return {
            "success": False,
            "error": "observation_source and required_values are supported only for search.",
        }
    if route_surfaces is not None and action != "search":
        return {"success": False, "error": "route_surfaces is supported only for search."}
    if catalog_query is not None and action != "search":
        return {"success": False, "error": "catalog_query is supported only for search."}
    if offset and action != "catalog":
        return {"success": False, "error": "offset is supported only for catalog."}
    if operation is not None and action != "describe":
        return {"success": False, "error": "operation is supported only for describe."}
    if include_schema and (action != "activate" or not tool or group is not None):
        return {"success": False, "error": "include_schema requires activating one tool."}
    entries = get_registered_tools()
    if action == "catalog":
        from services.registry.action_catalog import CatalogRequest, browse_catalog

        if query is not None:
            return {
                "success": False,
                "error": "catalog uses group or tool selectors without a query.",
            }
        try:
            request = CatalogRequest(
                group=group, tool=tool, offset=offset, limit=limit, intent=intent
            )
            entries, visible = await _catalog_inventory(ctx, entries, discovery)
            resources = await _catalog_resources(ctx) if request.includes_resources else []
            return browse_catalog(
                request, list(entries.values()), visible_names=set(visible), resources=resources
            )
        except ValueError as error:
            return {"success": False, "error": str(error)}
    if action == "search":
        if group is not None or tool is not None:
            return {
                "success": False,
                "error": "search does not accept a group or tool selector.",
            }
        return await search_unity_tools(
            ctx,
            query=query,
            catalog_query=catalog_query,
            limit=limit,
            intent=intent,
            observation_source=observation_source,
            required_values=required_values,
            route_surfaces=route_surfaces,
            host_tool_names=host_tool_names,
        )
    if action in ("activate", "deactivate", "reset") and modern:
        if discovery is None or await discovery.scope_key(ctx) is None:
            return {"success": False, "error": UNSCOPED_GUIDANCE}
    if action == "describe":
        from services.registry.action_catalog import describe_action

        if group is not None or not tool:
            return {"success": False, "error": "describe requires a tool name and no group."}
        entries, visible = await _catalog_inventory(ctx, entries, discovery)
        name = tool.strip()
        definition = visible.get(name)
        if definition is None and discovery is not None:
            definition = discovery.definitions.get(name)
        if definition is None or name not in entries:
            return {
                "success": False,
                "error": "Unknown tool in this catalog and the current advertised inventory. Browse catalog or refresh the client tool list.",
            }
        try:
            return describe_action(
                entries[name],
                definition.to_mcp_tool().model_dump(mode="json", exclude_none=True),
                operation,
                visible_names=set(visible),
            )
        except ValueError as error:
            return {"success": False, "error": str(error)}
    if action == "list_groups":
        return await _list_groups(ctx)

    if action in ("activate", "deactivate") and tool is not None:
        if group is not None:
            return {"success": False, "error": "Select exactly one group or tool."}
        tool = tool.strip()
        entry = next((item for item in get_registered_tools() if item["name"] == tool), None)
        if entry is None or entry.get("group") is None:
            return {
                "success": False,
                "error": "Unknown or always-visible tool. Use list_groups to discover selectable tools.",
            }
        operation = ctx.enable_components if action == "activate" else ctx.disable_components
        if discovery is not None:
            changed = await discovery.update(ctx, [tool], enabled=action == "activate")
        else:
            await operation(names={tool}, components={"tool"})
            changed = True
        result = {
            "activated_tool" if action == "activate" else "deactivated_tool": tool,
            "changed": changed,
            "client_inventory_status": "unconfirmed",
            "message": REFRESH_GUIDANCE,
        }
        if include_schema:
            description = await manage_tools(ctx, "describe", tool=tool)
            result["description"] = description
            from services.registry.action_catalog import declared_actions

            actions = declared_actions(entry)
            result["readiness"] = {
                "status": "unobserved",
                "next_action": {"tool": tool, "action": "capabilities"}
                if "capabilities" in actions
                else {"resource": "mcpforunity://tool-diagnostics"},
            }
        return result

    if action in ("activate", "deactivate"):
        if not group:
            return {"success": False, "error": f"group or tool is required for {action}"}
        group = group.strip().lower()
        if group not in TOOL_GROUPS:
            return {
                "success": False,
                "error": f"Unknown group '{group}'. Valid: {', '.join(sorted(TOOL_GROUPS))}",
            }

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
            "client_inventory_status": "unconfirmed",
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
            "client_inventory_status": "unconfirmed",
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
                return {"success": False, "error": msg}
            if result.get("unsupported"):
                msg = (
                    "The connected Unity Editor does not support tool state syncing yet. "
                    "Update the MCPForUnity package to the latest version, then try again. "
                    "In the meantime, use activate/deactivate actions to toggle groups manually."
                )
            else:
                msg = f"Failed to sync tool visibility from Unity. Is Unity running? ({msg})"
            return {"success": False, "error": msg}
        return {
            "synced": True,
            "client_inventory_status": "unconfirmed",
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
                "The client's callable inventory is unconfirmed; refresh it or reconnect. "
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
            "client_inventory_status": "unconfirmed",
            "message": (
                "Server selection restored to configured defaults. Unity availability still applies. "
                "The client's callable inventory is unconfirmed; refresh it or reconnect."
            ),
        }

    return {"success": False, "error": f"Unknown action '{action}'"}


async def _catalog_resources(ctx: Context) -> list[CatalogResource]:
    from fastmcp.exceptions import AuthorizationError
    from fastmcp.utilities.versions import dedupe_with_versions
    from services.operation_permissions import authorize_remote_resource

    await authorize_remote_resource()
    resources: list[CatalogResource] = []
    for item in dedupe_with_versions(
        await ctx.fastmcp.list_resources(), lambda item: str(item.uri)
    ):
        uri = str(item.uri)
        try:
            # A listed descriptor can still have a separate resource read policy.
            await ctx.fastmcp._check_resource_access(uri)
        except AuthorizationError:
            continue
        resources.append(
            {
                "uri": uri,
                "name": item.name,
                "description": item.description or "",
                "kwargs": {"meta": item.meta},
            }
        )
    templates = dedupe_with_versions(
        await ctx.fastmcp.list_resource_templates(),
        lambda item: item.uri_template,
    )
    resources.extend(
        {
            "uri": item.uri_template,
            "name": item.name,
            "description": item.description or "",
            "kwargs": {"meta": item.meta},
            "schema_required": tuple(item.parameters.get("required", ())),
        }
        for item in templates
    )
    return resources


async def _catalog_inventory(ctx, declarations, discovery):
    """Retain selectable built-ins and add wire descriptors visible to this request only."""
    visible = {item.name: item for item in await _visible_tools(ctx)}
    registered = discovery.definitions if discovery is not None else visible
    entries = {entry["name"]: entry for entry in declarations if entry["name"] in registered}
    for name, item in visible.items():
        if name not in entries:
            entries[name] = {
                "name": name,
                "group": None,
                "description": item.description,
                "kwargs": {"annotations": item.annotations},
                "input_schema": item.parameters,
            }
    return entries, visible


async def _visible_tools(ctx: Context):
    """Read the same final availability cap used by tools/list."""
    # Server/session transforms and authenticated Unity catalogs both affect visibility.
    # Reuse this request's resolved routing instead of reinjecting it via tools/list.
    from transport.unity_instance_middleware import UnityInstanceMiddleware
    from services.test_results_app import project_test_results_tools

    visible_tools = await ctx.fastmcp.list_tools(run_middleware=False)
    for middleware in ctx.fastmcp.middleware:
        if isinstance(middleware, UnityInstanceMiddleware):
            visible_tools = await middleware.filter_tools_for_context(ctx, visible_tools)
            visible_tools = project_test_results_tools(ctx, visible_tools)
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
