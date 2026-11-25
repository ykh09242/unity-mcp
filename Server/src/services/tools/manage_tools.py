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

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from transport.protocol import internal_catalog, internal_resource_catalog, is_sessionless

from models.host_tool_inventory import HostToolEvidence, HostToolNames, input_schema_digest
from services.registry import (
    mcp_for_unity_tool,
    TOOL_GROUPS,
    DEFAULT_ENABLED_GROUPS,
    get_group_tool_names,
    get_registered_tools,
)
from services.registry.action_catalog import ActionIntent, CatalogResource, ObservationSource
from services.registry.task_search import RequiredValues, RouteSurfaces

logger = logging.getLogger("mcp-for-unity-server")

_SearchQuery = Annotated[
    str | None,
    Field(
        max_length=512,
        description=(
            "Original user request. Keep its language, names, paths and text verbatim. Required "
            "for the agent directory. Optional for an exact agent family or ranked evidence search."
        ),
    ),
]
_CatalogQuery = Annotated[
    str | None,
    Field(
        max_length=512,
        description=(
            "Ranked search only. Optional English action and target terms or exact tool/action "
            "identifier for lexical matching. Keep the original request in query."
        ),
    ),
]
_ObservationSource = Annotated[
    ObservationSource | None,
    Field(
        description=(
            "Ranked search only. Require evidence from the current Editor (editor_live), a saved "
            "file (asset_file), import results (imported_asset), a compilation job (compile_job), "
            "or a Play Scenario job (scenario_job)."
        ),
    ),
]
_RequiredValues = Annotated[
    RequiredValues | None,
    Field(
        description=(
            "Ranked search only. Require every listed result field as evidence. Combine with "
            "observation_source to require its provenance."
        ),
    ),
]
_INTENT_DESCRIPTION = (
    "Choose the requested purpose by meaning in any language: observe for reading state, "
    "interact for operating the Editor or running game, edit for changing project content."
)
_SEARCH_DESCRIPTION = (
    "Choose one search mode. For exhaustive browsing, use selection=agent with the verbatim query, "
    "then open returned family IDs and follow next_offset. For lexical or required evidence searches, "
    "use selection=ranked. catalog_query, intent, observation_source and required_values are ranked only."
)
_Selection = Annotated[
    Literal["ranked", "agent"],
    Field(
        description="Choose agent for exhaustive browsing or ranked for lexical and required evidence search."
    ),
]
_Family = Annotated[
    str | None,
    Field(
        min_length=1,
        description="Agent browse only. Provide an exact family ID. query is optional.",
    ),
]


async def _retry_project_binding(ctx: Context) -> dict[str, str]:
    """Retain the resolved ID when the caller selected a project on this call."""
    from transport.unity_instance_middleware import EXPLICIT_REQUEST_INSTANCE_STATE

    target = await ctx.get_state(EXPLICIT_REQUEST_INSTANCE_STATE)
    return {"unity_instance": target} if isinstance(target, str) else {}


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Search Unity tools, actions and readable resources without changing selection or invoking "
        "Unity. "
        + _SEARCH_DESCRIPTION
        + " Inspect route_groups for invocations and evidence limits, "
        "or no_match for scoped alternatives."
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
    query: _SearchQuery = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=20), "Maximum results per page."] = 5,
    catalog_query: _CatalogQuery = None,
    intent: Annotated[
        ActionIntent | None, Field(description="Ranked search only. " + _INTENT_DESCRIPTION)
    ] = None,
    observation_source: _ObservationSource = None,
    required_values: _RequiredValues = None,
    route_surfaces: Annotated[
        RouteSurfaces | None,
        Field(description="For either mode, show tools, resources or both (default)."),
    ] = None,
    host_tool_names: Annotated[
        HostToolNames | None,
        "Complete callable tool names exposed by this host for this connection. Omit if unknown.",
    ] = None,
    host_tool_evidence: Annotated[
        HostToolEvidence | None,
        "Bounded exact-name checks from this host on the same connection and target. "
        "Use instead of host_tool_names. Incomplete misses stay unknown.",
    ] = None,
    selection: _Selection = "ranked",
    family: _Family = None,
    offset: Annotated[
        int, Field(strict=True, ge=0, description="Agent browse only. Follow next_offset.")
    ] = 0,
) -> dict[str, Any]:
    from services.registry.task_search import (
        AGENT_SEARCH_FIELDS,
        RANKED_SEARCH_FIELDS,
        SearchRequest,
        search_catalog,
    )

    if host_tool_names is not None and host_tool_evidence is not None:
        return {"success": False, "error": "Supply host_tool_names or host_tool_evidence."}
    arguments = {
        "selection": selection,
        "family": family,
        "query": query,
        "catalog_query": catalog_query,
        "limit": limit,
        "intent": intent,
        "observation_source": observation_source,
        "required_values": required_values,
        "route_surfaces": route_surfaces or "both",
        "host_tool_names": host_tool_names,
        "host_tool_evidence": host_tool_evidence,
        **({"offset": offset} if offset else {}),
    }
    try:
        request = SearchRequest.model_validate(arguments)
    except ValidationError as error:
        detail = error.errors(include_input=False)[0]
        if detail["type"] == "search_query_required":
            return {
                "success": False,
                "error": detail["msg"],
                "required_one_of": ["query", "family"]
                if selection == "agent"
                else ["query", "observation_source", "required_values"],
            }
        if detail["type"] != "search_mode_conflict":
            field = ".".join(map(str, detail["loc"]))
            return {
                "success": False,
                "error": f"{field}: {detail['msg']}" if field else detail["msg"],
            }

        modes: list[tuple[str, tuple[str, ...], str]] = [
            ("ranked", AGENT_SEARCH_FIELDS, "Search for routes with the supplied filters.")
        ]
        if observation_source is None and required_values is None:
            modes.append(("agent", RANKED_SEARCH_FIELDS, "Browse complete operation pages."))
        project_binding = await _retry_project_binding(ctx)
        retries = []
        for mode, fields, purpose in modes:
            candidate = {
                name: value
                for name, value in arguments.items()
                if name not in fields and value is not None
            }
            candidate["selection"] = mode
            try:
                retry = SearchRequest.model_validate(candidate)
            except ValidationError:
                continue
            payload = {**retry.model_dump(mode="json", exclude_unset=True), **project_binding}
            for name in ("query", "catalog_query"):
                if name in candidate:
                    payload[name] = candidate[name]
            retries.append(
                {
                    "arguments": payload,
                    "omit": [name for name in fields if arguments.get(name) is not None],
                    "purpose": purpose,
                }
            )
        result = {
            "success": False,
            "error": detail["msg"],
            "incompatible_fields": detail.get("ctx", {}).get("fields", []),
            "search_retries": retries,
        }
        if not retries:
            result["hint"] = "Supply the original query for the corrected search."
        return result
    entries, visible = await _catalog_inventory(
        ctx, get_registered_tools(), getattr(ctx.fastmcp, "_tool_discovery", None)
    )
    if selection == "agent":
        entries = {
            name: {**entry, "input_schema": visible[name].parameters} if name in visible else entry
            for name, entry in entries.items()
        }
    # Resolve the caller's inventory on its MCP context before browsing declarations.
    outcome = await asyncio.to_thread(
        search_catalog,
        request,
        list(entries.values()),
        visible_names=set(visible),
        resources=await _catalog_resources(ctx),
    )
    if (
        outcome["matching"] == "observation"
        and "no_match" in outcome
        and request.intent in ("interact", "edit")
    ):
        hint = (
            "No route matched both the requested intent and evidence constraints. If the action "
            "need not return evidence, retry the same search without observation_source or "
            "required_values. Keep the target, surface and host scope. Use catalog_query for "
            "English action and target terms if helpful. If the resulting state also needs "
            "verification, find an observation route separately."
        )
        retry: dict[str, Any] = {}
        if request.query is not None:
            retry = {
                "retry_search": {
                    "arguments": {
                        **request.model_dump(
                            exclude={"family", "offset", "observation_source", "required_values"},
                            exclude_none=True,
                            mode="json",
                        ),
                        "query": query,
                        **(await _retry_project_binding(ctx)),
                    },
                    "omit": ["observation_source", "required_values"],
                }
            }
        else:
            hint += " Supply query with the original request for the action search."
        return {**outcome, "hint": hint, **retry}
    return outcome


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
        "search (discover routes for a task in any language), "
        "inspect_inventory (compare server advertisement with this host's exact-name evidence). "
        + _SEARCH_DESCRIPTION
        + " Selection changes server tools/list; it does not confirm the client's callable inventory. "
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
        "Single exact tool name for activate/deactivate/describe/catalog/inspect_inventory.",
    ] = None,
    query: _SearchQuery = None,
    limit: Annotated[
        int, Field(strict=True, ge=1, le=20), "Maximum search or catalog page entries (1..20)."
    ] = 5,
    include_schema: Annotated[
        bool,
        Field(strict=True),
        "Describe always includes the schema. On single-tool activation attach schema and readiness. On inspect_inventory include the advertised inputSchema and its SHA-256.",
    ] = False,
    catalog_query: _CatalogQuery = None,
    offset: Annotated[
        int,
        Field(strict=True, ge=0, description="Page offset for catalog or selection=agent search."),
    ] = 0,
    operation: Annotated[
        str | None,
        Field(min_length=1, max_length=128),
        "For describe, select one exact action from catalog. The returned full schema binds that action.",
    ] = None,
    intent: Annotated[
        ActionIntent | None,
        Field(
            description=_INTENT_DESCRIPTION
            + " Use with selection=ranked for search or with a tool/group in catalog."
        ),
    ] = None,
    observation_source: _ObservationSource = None,
    required_values: _RequiredValues = None,
    route_surfaces: Annotated[
        RouteSurfaces | None,
        Field(description="For either search mode, show tools, resources or both (default)."),
    ] = None,
    host_tool_names: Annotated[
        HostToolNames | None,
        "For search or inspect_inventory, supply this host's complete callable tool names. Omit if unknown.",
    ] = None,
    host_tool_evidence: Annotated[
        HostToolEvidence | None,
        "For search or inspect_inventory, check exact candidate names on this host's same connection "
        "and target. Use instead of host_tool_names. Incomplete misses stay unknown.",
    ] = None,
    selection: _Selection = "ranked",
    family: _Family = None,
) -> dict[str, Any]:
    from services.tool_discovery import REFRESH_GUIDANCE, UNSCOPED_GUIDANCE

    discovery = getattr(ctx.fastmcp, "_tool_discovery", None)
    modern = is_sessionless(ctx)
    if host_tool_names is not None and action not in {"search", "inspect_inventory"}:
        return {"success": False, "error": "host_tool_names requires search or inspect_inventory."}
    if host_tool_evidence is not None and action not in {"search", "inspect_inventory"}:
        return {
            "success": False,
            "error": "host_tool_evidence requires search or inspect_inventory.",
        }
    if action == "inspect_inventory":
        if not tool or len(tool) > 128:
            return {"success": False, "error": "Inspect one exact tool name (1..128 characters)."}
        if host_tool_names is not None and host_tool_evidence is not None:
            return {"success": False, "error": "Supply host_tool_names or host_tool_evidence."}
        # Only report the queried name's membership. Catalog definitions include
        # selectable but denied entries and must never populate this receipt.
        if include_schema:
            from fastmcp.utilities.versions import dedupe_with_versions

            with internal_catalog(ctx.fastmcp, "tools/list"):
                visible = dedupe_with_versions(
                    await ctx.fastmcp.list_tools(), lambda item: item.name
                )
        else:
            visible = await _visible_tools(ctx)
        definition = next((item for item in visible if item.name == tool), None)
        advertised = definition is not None
        received = None
        if host_tool_evidence is not None:
            received = host_tool_evidence.received(tool)
        elif host_tool_names is not None:
            received = tool in host_tool_names
        result = {
            "scope": "current_mcp_request",
            "server_advertised": advertised,
            "host_evidence": "caller_report" if received is not None else "unknown",
            "status": (
                "server_unavailable"
                if not advertised
                else "host_unknown"
                if received is None
                else "host_advertised"
                if received
                else "host_filtered"
            ),
        }
        if include_schema and definition is not None:
            schema = definition.to_mcp_tool().input_schema
            result.update(inputSchema=schema, input_schema_sha256=input_schema_digest(schema))
        return result
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
    if (selection != "ranked" or family is not None) and action != "search":
        return {"success": False, "error": "selection and family are supported only for search."}
    if offset and action not in {"catalog", "search"}:
        return {"success": False, "error": "offset requires catalog or selection=agent search."}
    if operation is not None and action != "describe":
        return {"success": False, "error": "operation is supported only for describe."}
    if include_schema and (action not in {"activate", "describe"} or not tool or group is not None):
        return {
            "success": False,
            "error": "include_schema requires one tool for describe, activate or inspect_inventory.",
        }
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
        result = await search_unity_tools(
            ctx,
            selection=selection,
            family=family,
            offset=offset,
            query=query,
            catalog_query=catalog_query,
            limit=limit,
            intent=intent,
            observation_source=observation_source,
            required_values=required_values,
            route_surfaces=route_surfaces,
            host_tool_names=host_tool_names,
            host_tool_evidence=host_tool_evidence,
        )
        for retry in result.get("search_retries", []):
            retry["arguments"]["action"] = "search"
        if "retry_search" in result:
            result["retry_search"]["arguments"]["action"] = "search"
        return result
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
                {**entries[name], "input_schema": definition.parameters},
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
        description = await manage_tools(ctx, "describe", tool=tool) if include_schema else None
        if description is not None and "catalog" in description:
            advertised = description["catalog"]["advertised"]
        else:
            advertised = any(item.name == tool for item in await _visible_tools(ctx))
        diagnostic = {"resource": f"mcpforunity://tool-diagnostics/{tool}"}
        result = {
            "activated_tool" if action == "activate" else "deactivated_tool": tool,
            "changed": changed,
            "server_advertised": advertised,
            "client_inventory_status": "unconfirmed",
            "message": REFRESH_GUIDANCE,
        }
        if action == "activate" and not advertised:
            result.update(
                message="Selected for this scope, but absent from its advertised inventory. Check the tool diagnostic for Editor availability and permission.",
                next_action=diagnostic,
            )
        if description is not None:
            result["description"] = description
            from services.registry.action_catalog import declared_actions

            actions = declared_actions(entry)
            result["readiness"] = {
                "status": "unobserved",
                "next_action": {"tool": tool, "action": "capabilities"}
                if advertised and "capabilities" in actions
                else diagnostic,
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

        names = get_group_tool_names().get(group, [])
        enabled = action == "activate"
        logger.info("%s tool group: %s", action, group)
        if discovery is not None:
            changed = await discovery.update(ctx, names, enabled=enabled)
        else:
            update = ctx.enable_components if enabled else ctx.disable_components
            await update(tags={f"group:{group}"}, components={"tool"})
            changed = True
        visible = {item.name for item in await _visible_tools(ctx)}
        return {
            "activated" if enabled else "deactivated": group,
            "tools": names,
            "server_advertised_tools": sorted(visible.intersection(names)),
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
    with internal_resource_catalog(ctx.fastmcp):
        listed = await ctx.fastmcp.list_resources()
        templates = await ctx.fastmcp.list_resource_templates()
    resources: list[CatalogResource] = []
    for item in dedupe_with_versions(listed, lambda item: str(item.uri)):
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
    resources.extend(
        {
            "uri": item.uri_template,
            "name": item.name,
            "description": item.description or "",
            "kwargs": {"meta": item.meta},
            "schema_required": tuple(item.parameters.get("required", ())),
        }
        for item in dedupe_with_versions(templates, lambda item: item.uri_template)
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
