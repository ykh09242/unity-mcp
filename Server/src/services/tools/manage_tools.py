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
from collections import Counter
import inspect
import logging
from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.tools import Tool
from fastmcp.utilities.json_schema import dereference_refs
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from transport.protocol import internal_catalog, internal_resource_catalog, is_sessionless

from models.host_tool_inventory import HostToolEvidence, HostToolNames, input_schema_digest
from services.resource_inventory import (
    can_prepare_observation,
    connection_inventory,
    connection_inventory_call,
    observation_template_domain,
    observation_uri_allowed,
)
from services.registry import (
    mcp_for_unity_tool,
    TOOL_GROUPS,
    DEFAULT_ENABLED_GROUPS,
    get_group_tool_names,
    get_registered_tools,
)
from services.registry.action_catalog import (
    RESOURCE_TEMPLATE_EXPANSION,
    ActionIntent,
    CatalogEntry,
    CatalogResource,
    ObservationSource,
)
from services.registry.action_contracts import (
    forbidden_action_parameters,
    input_actions,
    required_for_actions,
)
from services.registry.call_preparation import (
    MAX_ORIGINAL_REQUEST_LENGTH,
    ORIGINAL_REQUEST,
    PAGE_SIZE,
    ExactTextAssertion,
    not_prepared,
    prepared_binding_execution,
    prepare_tool_call,
)
from services.registry.input_choices import OPERATION_SELECTOR_MAX_LENGTH
from services.registry.preview_continuation import PreviewBinding
from services.registry.task_search import RANKED_SEARCH_FIELDS, RequiredValues, RouteSurfaces
from services.registry.search_arguments import SEARCH_INPUT

logger = logging.getLogger("mcp-for-unity-server")

_MAX_DISCOVERY_PAGE = 20
_RequestedPageSize = Annotated[
    int,
    Field(strict=True, ge=1, json_schema_extra={PAGE_SIZE: {"maximum": _MAX_DISCOVERY_PAGE}}),
    "Requested entries per page for search, catalog or a tool's operation choices. At most 20 per page.",
]
_ManagePageSize = Annotated[
    _RequestedPageSize,
    Field(
        json_schema_extra={
            PAGE_SIZE: {
                "maximum": _MAX_DISCOVERY_PAGE,
                "actions": ["search", "catalog", "describe"],
            }
        }
    ),
]

_SearchQuery = Annotated[
    str | None,
    Field(
        max_length=MAX_ORIGINAL_REQUEST_LENGTH,
        json_schema_extra={ORIGINAL_REQUEST: {}},
        description=(
            "Original user request. Keep its language, names, paths and text verbatim. Required "
            "for the agent directory. Optional for an exact agent family or ranked evidence search."
        ),
    ),
]
_ManageQuery = Annotated[
    _SearchQuery,
    Field(
        description="Original request for search, unscoped catalog search or prepare. With a catalog group or tool, filter its public metadata by matching all query terms. Keep names, paths and text verbatim.",
        json_schema_extra={ORIGINAL_REQUEST: {"action": "search"}},
    ),
]
_EXACT_TEXT_DESCRIPTION = (
    "For user-specified exact text, add exact_text and the unchanged original query alongside "
    "arguments on prepare. Use an array of {path, expected} objects. Each path is an array of "
    "exact keys or zero-based array indices within arguments. Copy expected verbatim from query."
)


def _prepare_exact_text_example() -> dict[str, Any]:
    literal = "Ready."
    return {
        "query": f'Set the label to "{literal}"',
        "exact_text": [{"path": ["items", 0, "label"], "expected": literal}],
    }


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
    'For exhaustive browsing, pass "selection": "agent" and "query": the original text. '
    "Supply all search fields together at the top level or in one query object. "
    'To open a family, set "family" to its returned ID in open_family.arguments. '
    "Follow next_offset. For lexical or required evidence searches, "
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


def _resource_read_call(uri: str, project: dict[str, str]) -> dict:
    params = {"uri": uri, **({"_meta": project} if project else {})}
    return {"method": "resources/read", "params": params}


def _describe_call(name: str, project: dict[str, str], **selection) -> dict:
    return {
        "tool": "manage_tools",
        "arguments": {"action": "describe", "tool": name, **selection, **project},
    }


def _operation_choice_page(name, description, project, offset, limit):
    values = description["catalog"]["actions"]
    if not values:
        # A bare operation value must identify exactly one advertised input field.
        frequencies = Counter(
            value for field in description.get("input_choices", {}).values() for value in field
        )
        values = [value for value, count in frequencies.items() if count == 1]
    values = list(
        dict.fromkeys(value for value in values if 1 <= len(value) <= OPERATION_SELECTOR_MAX_LENGTH)
    )
    end = offset + limit
    return {
        "operation_choices": [
            _describe_call(name, project, operation=value) for value in values[offset:end]
        ],
        "next_offset": end if end < len(values) else None,
        **(
            {"next_call": _describe_call(name, project, limit=limit, offset=end)}
            if end < len(values)
            else {}
        ),
    }


async def _resource_readers(ctx: Context, request=None) -> set[str]:
    """Offer only serving built-ins exposed by the connection and the supplied host inventory."""
    from fastmcp.exceptions import AuthorizationError
    from services.operation_permissions import OperationPermissionError
    from transport.unity_instance_middleware import _trusted_builtin_callback

    visible = await _selection_inventory(ctx) or {}
    readers = set()
    for name in ("manage_tools", "read_unity_resource"):
        if name not in visible:
            continue
        if request is not None and (
            request.host_tool_names is not None
            and name not in request.host_tool_names
            or request.host_tool_evidence is not None
            and request.host_tool_evidence.received(name) is not True
        ):
            continue
        try:
            selected = await ctx.fastmcp.get_tool(name)
        except (AuthorizationError, OperationPermissionError):
            continue
        if (
            selected is not None
            and selected.key == visible[name].key
            and getattr(visible[name], "fn", None) is getattr(selected, "fn", None)
            and _trusted_builtin_callback(getattr(selected, "fn", None), name, None)
        ):
            readers.add(name)
    return readers


def _bind_resource_read(
    ctx: Context,
    row: dict,
    project: dict[str, str],
    *,
    readers: set[str],
    observations: dict[str, dict],
    skill: bool = False,
) -> None:
    if "uri_template" in row or row.get("kind") == "resource_template":
        template = row.get("uri_template") or row["family"]
        row["native_read_after_expansion"] = {
            "method": "resources/read",
            "params": {"_meta": project} if project else {},
            "set_param": "uri",
        }
        if template not in observations:
            return
        constraints = observations[template]
        if constraints:
            row["native_read_after_expansion"]["authorization"] = "checked_for_expanded_uri"
        if "read_unity_resource" in readers:
            row["preferred_read_after_expansion"] = {
                "tool": "read_unity_resource",
                "arguments": dict(project),
                "set_argument": "uri",
                **constraints,
            }
        if "manage_tools" in readers and (
            "family" not in row or "read_unity_resource" not in readers
        ):
            row["read_after_expansion"] = {
                "tool": "manage_tools",
                "arguments": {"action": "read_resource", **project},
                "set_argument": "uri",
                **constraints,
            }
        if readers and constraints:
            row["read_guidance"] = (
                "For the tool, match fixed_uri_variables using URI text, supply required_uri_variables and omit any omit_uri_variables. "
                "For evaluate_getters=true, pass the requested URI to native resources/read with its required grants."
            )
        elif readers and "family" not in row:
            row["read_guidance"] = (
                "Expand the template with the observed identifiers and declared options."
            )
        return
    uri = row["uri"]
    if connection_inventory(ctx.fastmcp, uri):
        project = {}
    row["read_call"] = _resource_read_call(uri, project)
    if uri not in observations:
        return
    if "read_unity_resource" in readers:
        row["preferred_read_tool"] = {
            "tool": "read_unity_resource",
            "arguments": {"uri": uri, **project},
        }
    if "manage_tools" in readers:
        row["read_tool"] = {
            "tool": "manage_tools",
            "arguments": {
                **(
                    {"action": "read_skill", "skill_uri": uri}
                    if skill
                    else {"action": "read_resource", "uri": uri}
                ),
                **project,
            },
        }


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    input_conditions=(SEARCH_INPUT,),
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
    limit: _RequestedPageSize = 5,
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
        "limit": min(limit, _MAX_DISCOVERY_PAGE) if type(limit) is int else limit,
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
    # Resolve the caller's inventory on its MCP context before browsing declarations.
    resources = await _catalog_resources(ctx)
    observations = _observation_domains(resources)
    outcome = await asyncio.to_thread(
        search_catalog,
        request,
        list(entries.values()),
        visible_names=set(visible),
        resources=resources,
    )
    if outcome.get("view") in {"families", "operations"}:
        page = request.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        project = await _retry_project_binding(ctx)
        page.update(selection="agent", limit=request.limit, **project)
        if outcome["view"] == "families":
            outcome["open_family"] = {
                "tool": "search_unity_tools",
                "arguments": {k: v for k, v in page.items() if k != "offset"},
                "set_argument": "family",
            }
            if (
                request.route_surfaces != "resources"
                and host_tool_names is None
                and host_tool_evidence is None
                and "manage_tools" in visible
            ):
                outcome["selectable_catalog"] = {
                    "tool": "manage_tools",
                    "arguments": {"action": "catalog", **project},
                }
        if (offset_next := outcome.get("next_offset")) is not None:
            outcome["next_call"] = {
                "tool": "search_unity_tools",
                "arguments": {**page, "offset": offset_next},
            }
        can_manage = (
            "manage_tools" in visible
            and (request.host_tool_names is None or "manage_tools" in request.host_tool_names)
            and (
                request.host_tool_evidence is None
                or request.host_tool_evidence.received("manage_tools") is True
            )
        )
        operations = outcome.get("operations", [])
        template_families = [
            row for row in outcome.get("families", ()) if row["kind"] == "resource_template"
        ]
        readers = (
            await _resource_readers(ctx, request)
            if template_families or any(row["kind"] != "tool" for row in operations)
            else set()
        )
        for row in template_families:
            _bind_resource_read(ctx, row, project, readers=readers, observations=observations)
        for binding in operations:
            if binding["kind"] == "tool" and can_manage:
                selected = {"operation": binding["action"]} if binding["action"] is not None else {}
                binding["describe_call"] = _describe_call(binding["tool"], project, **selected)
            if binding["kind"] == "tool" and project:
                schema = entries[binding["tool"]].get("input_schema", {})
                if (
                    "unity_instance" in schema.get("properties", {})
                    and "unity_instance" not in binding["forbidden_parameters"]
                ):
                    binding["invoke"]["arguments"].update(project)
            if binding["kind"] in {"resource", "resource_template"}:
                _bind_resource_read(
                    ctx, binding, project, readers=readers, observations=observations
                )
                if binding["kind"] == "resource_template":
                    binding["input_contract"] = RESOURCE_TEMPLATE_EXPANSION
        return outcome
    ranked = [*outcome.get("routes", ()), *outcome.get("no_match", {}).get("alternatives", ())]
    for group in outcome.get("route_groups", ()):
        ranked.extend((group["representative"], *group["alternatives"]))
    resource_rows = [row for row in ranked if row["kind"] in {"resource", "resource_template"}]
    if resource_rows:
        project = await _retry_project_binding(ctx)
        readers = await _resource_readers(ctx, request)
        for row in resource_rows:
            _bind_resource_read(ctx, row, project, readers=readers, observations=observations)
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
    input_conditions=(
        {
            "if": {"properties": {"action": {"enum": ["read_resource", "read_skill"]}}},
            "then": {
                "properties": {
                    **{
                        field: {"type": "null"}
                        for field in (
                            "group",
                            "tool",
                            "query",
                            "catalog_query",
                            "operation",
                            "intent",
                            "observation_source",
                            "required_values",
                            "route_surfaces",
                            "host_tool_names",
                            "host_tool_evidence",
                            "family",
                            "arguments",
                            "exact_text",
                        )
                    },
                    "include_schema": {"const": False},
                    "offset": {"const": 0},
                    "selection": {"const": "ranked"},
                }
            },
        },
        {
            "if": {"required": ["skill_uri"], "properties": {"skill_uri": {"type": "string"}}},
            "then": {"properties": {"action": {"const": "read_skill"}}},
        },
        {
            "if": {
                "anyOf": [
                    {"properties": {"action": {"const": "search"}}},
                    {
                        "required": ["query"],
                        "properties": {
                            "action": {"const": "catalog"},
                            "query": {"type": "string"},
                            "group": {"type": "null"},
                            "tool": {"type": "null"},
                        },
                    },
                ]
            },
            "then": {
                "allOf": [SEARCH_INPUT],
                "properties": {
                    **{
                        field: {"type": "null"}
                        for field in ("group", "tool", "operation", "arguments")
                    },
                    "include_schema": {"const": False},
                },
            },
        },
        {
            "if": {
                "required": ["query"],
                "properties": {"action": {"const": "catalog"}, "query": {"type": "string"}},
                "anyOf": [
                    {"required": [selector], "properties": {selector: {"type": "string"}}}
                    for selector in ("group", "tool")
                ],
            },
            "then": {
                "not": {
                    "required": ["group", "tool"],
                    "properties": {selector: {"type": "string"} for selector in ("group", "tool")},
                },
                "properties": {
                    "query": {"pattern": r"\S"},
                    "group": {"pattern": r"\S", "maxLength": 128},
                    "tool": {"pattern": r"\S", "maxLength": 128},
                    "selection": {"const": "ranked"},
                    "include_schema": {"const": False},
                    **{
                        field: {"type": "null"}
                        for field in (
                            "arguments",
                            "catalog_query",
                            "exact_text",
                            "family",
                            "host_tool_evidence",
                            "host_tool_names",
                            "observation_source",
                            "required_values",
                            "route_surfaces",
                        )
                    },
                },
            },
        },
        {
            "if": {"properties": {"action": {"not": {"enum": ["search", "catalog"]}}}},
            "then": {"properties": {"limit": {"maximum": _MAX_DISCOVERY_PAGE}}},
        },
        {
            "if": {
                "required": ["operation"],
                "properties": {"action": {"const": "catalog"}, "operation": {"type": "string"}},
            },
            "then": {
                "required": ["tool"],
                "properties": {
                    "tool": {"type": "string", "pattern": r"\S", "maxLength": 128},
                    "operation": {"pattern": r"\S"},
                    "group": {"type": "null"},
                },
            },
        },
        {
            "if": {"properties": {"action": {"const": "prepare"}}},
            "then": {
                "required": ["tool", "arguments"],
                "properties": {
                    "tool": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object"},
                },
            },
        },
        {
            "if": {
                "required": ["exact_text"],
                "properties": {"exact_text": {"type": "array"}},
            },
            "then": {
                "required": ["query"],
                "properties": {
                    "action": {"const": "prepare"},
                    "query": {"type": "string", "minLength": 1},
                },
            },
        },
    ),
    description=(
        'Find Unity tools and resources. If the guide is listed, call manage_tools(action="read_skill") without a document selector. '
        "When unavailable, use the host's installed skill loader. "
        'For another published skill file, follow read_tool from manage_tools(action="catalog",group="skills").'
        "\n- read_resource: Read a published observation using its catalog tool binding. "
        "Use native resources/read for other providers and evaluate_getters=true.\n"
        "Actions: list_instances (list connected Editors on this connection, even without a resource browser), "
        "list_groups (show all groups and their status), "
        "activate/deactivate (select exactly one group or tool), "
        "sync (refresh availability from Unity Editor's toggle states), "
        "reset (restore configured profile), describe (start with tool alone and follow a returned operation_choices call for its inputs), "
        "prepare (return schema-checked arguments and current public permission evidence without executing the call), "
        "catalog (browse or filter by group or tool, or search by query alone), "
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
            "list_instances",
            "activate",
            "deactivate",
            "sync",
            "reset",
            "describe",
            "search",
            "catalog",
            "inspect_inventory",
            "prepare",
            "read_skill",
            "read_resource",
        ],
        "Action to perform.",
    ],
    group: Annotated[
        str | None,
        "Group name for activate/deactivate/catalog. Catalog also supports resources for fixed Unity URIs, "
        "resource_templates for parameterized observations and skills for published skill files. "
        "Valid groups: " + ", ".join(sorted(TOOL_GROUPS.keys())),
    ] = None,
    tool: Annotated[
        str | None,
        "Single exact tool name for activate/deactivate/describe/prepare/catalog/inspect_inventory.",
    ] = None,
    query: _ManageQuery = None,
    limit: _ManagePageSize = 5,
    include_schema: Annotated[
        bool,
        Field(strict=True),
        "Describe always includes the schema. On single-tool activation attach schema and readiness. On inspect_inventory include the advertised inputSchema and its SHA-256.",
    ] = False,
    catalog_query: _CatalogQuery = None,
    offset: Annotated[
        int,
        Field(
            strict=True,
            ge=0,
            description="Page offset for catalog, tool-only describe or selection=agent search.",
        ),
    ] = 0,
    operation: Annotated[
        str | None,
        Field(min_length=1, max_length=OPERATION_SELECTOR_MAX_LENGTH),
        "Omit when only the tool name is known. Copy a returned operation_choices or describe_call binding to select an action or a unique advertised input choice. For an ambiguous choice, draft the actual input field without operation.",
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
        "For search, prepare or inspect_inventory, supply the host's complete callable tool names. Omit if unknown.",
    ] = None,
    host_tool_evidence: Annotated[
        HostToolEvidence | None,
        "For search, prepare or inspect_inventory, check exact names on this host's same connection "
        "and target. Use instead of host_tool_names. Incomplete misses stay unknown.",
    ] = None,
    selection: _Selection = "ranked",
    family: _Family = None,
    arguments: Annotated[
        dict[str, Any] | None,
        Field(max_length=128),
        "Prepare only: proposed input object for the exact tool. Pass the original user text in query when needed.",
    ] = None,
    exact_text: Annotated[
        list[ExactTextAssertion] | None,
        Field(
            min_length=1,
            max_length=16,
            description=_EXACT_TEXT_DESCRIPTION,
            examples=[_prepare_exact_text_example()["exact_text"]],
        ),
    ] = None,
    skill_uri: Annotated[
        str | None,
        Field(min_length=1, max_length=2048, pattern=r"^skill://[^{}]+$"),
        "For read_skill, supply a listed skill:// URI. Omit to read the packaged guide.",
    ] = None,
    uri: Annotated[
        str | None,
        Field(min_length=1, max_length=4096, pattern=r"^(mcpforunity|skill)://[^{}]+$"),
        input_actions("read_resource"),
        required_for_actions("read_resource"),
        "For read_resource, follow a catalog observation binding. Use native resources/read for getter execution.",
    ] = None,
) -> dict[str, Any]:
    from services.tool_discovery import REFRESH_GUIDANCE, UNSCOPED_GUIDANCE

    discovery = getattr(ctx.fastmcp, "_tool_discovery", None)
    modern = is_sessionless(ctx)
    if skill_uri is not None and action != "read_skill":
        return {"success": False, "error": "skill_uri requires read_skill."}
    if uri is not None and action != "read_resource":
        return {"success": False, "error": "uri requires read_resource."}
    if action == "catalog" and query is not None and group is None and tool is None:
        action = "search"
    if type(limit) is int and limit > _MAX_DISCOVERY_PAGE:
        if action == "catalog" or (action == "describe" and operation is None):
            limit = _MAX_DISCOVERY_PAGE
        elif action != "search":
            return {
                "success": False,
                "error": "A larger page request applies only to search, catalog or tool-only describe.",
            }
    if action == "prepare":
        if not tool or arguments is None:
            return not_prepared("Supply one exact tool name and its draft arguments.")
        if (
            any(
                value is not None
                for value in (
                    group,
                    family,
                    intent,
                    observation_source,
                    required_values,
                    route_surfaces,
                    catalog_query,
                )
            )
            or selection != "ranked"
            or offset
            or limit != 5
            or include_schema
        ):
            return not_prepared(
                "Put target call parameters in arguments. Use tool, operation, query or host evidence on prepare."
            )
        if host_tool_evidence is not None and host_tool_names is not None:
            return not_prepared("Supply host_tool_names or host_tool_evidence.")
        if (host_tool_names is not None and tool not in host_tool_names) or (
            host_tool_evidence is not None and host_tool_evidence.received(tool) is False
        ):
            return not_prepared("The tool is absent from the reported host inventory.")
        return await _prepare_binding(ctx, tool, arguments, query, operation, exact_text=exact_text)
    if arguments is not None:
        return {"success": False, "error": "arguments is supported only for prepare."}
    if exact_text is not None:
        return {"success": False, "error": "exact_text is supported only for prepare."}
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
    if (
        offset
        and action not in {"catalog", "search"}
        and not (action == "describe" and operation is None)
    ):
        return {
            "success": False,
            "error": "offset requires catalog, tool-only describe or selection=agent search.",
        }
    if operation is not None and action not in {"catalog", "describe"}:
        return {"success": False, "error": "Select an operation with catalog, describe or prepare."}
    if include_schema and (action not in {"activate", "describe"} or not tool or group is not None):
        return {
            "success": False,
            "error": "include_schema requires one tool for describe, activate or inspect_inventory.",
        }
    if action in {"read_resource", "read_skill"}:
        from services.resource_inventory import read_published_document

        if group is not None or tool is not None or query is not None:
            selector = "skill_uri" if action == "read_skill" else "uri"
            return {"success": False, "error": f"Select the published resource with {selector}."}
        if action == "read_skill":
            if skill_uri is not None and not skill_uri.startswith("skill://"):
                return {"success": False, "error": "Select a published skill URI from the catalog."}
            return await read_published_document(ctx, skill_uri, concrete_skill=True)
        if not uri:
            return {"success": False, "error": "Supply a concrete or expanded resource URI."}
        return await read_published_document(ctx, uri)

    entries = get_registered_tools()
    if action == "catalog":
        from services.registry.action_catalog import CatalogRequest, browse_catalog

        try:
            request = CatalogRequest(
                group=group,
                tool=tool,
                operation=operation,
                query=query,
                offset=offset,
                limit=limit,
                intent=intent,
            )
            if request.group == "skills":
                resources = await _catalog_resources(ctx, concrete_scheme="skill")
                page = browse_catalog(request, [], visible_names=set(), resources=resources)
                page["hint"] += (
                    " Follow a returned tool binding on this connection or read_call with a resource client."
                )
                project = await _retry_project_binding(ctx)
                observations = _observation_domains(resources)
                readers = await _resource_readers(ctx) if observations else set()
                for item in page["skills"]:
                    _bind_resource_read(
                        ctx, item, project, readers=readers, observations=observations, skill=True
                    )
                return page
            if request.query is not None and not request.includes_resources:
                visible = await _selection_inventory(ctx)
                if visible is None:
                    return {
                        "success": False,
                        "error": "The current MCP tool inventory is unavailable.",
                    }
                entries = _public_catalog_entries(discovery, visible)
            else:
                entries, visible = await _catalog_inventory(ctx, entries, discovery)
            resources = await _catalog_resources(ctx) if request.includes_resources else []
            page = browse_catalog(
                request, list(entries.values()), visible_names=set(visible), resources=resources
            )
            if request.includes_resources:
                project = await _retry_project_binding(ctx)
                for group_name in ("resources", "resource_templates", "skills"):
                    branch = page.get(group_name)
                    if isinstance(branch, dict):
                        branch["browse"].update(project)
                if request.group in {"resources", "resource_templates"}:
                    observations = _observation_domains(resources)
                    readers = await _resource_readers(ctx) if observations else set()
                    for item in page[request.group]:
                        _bind_resource_read(
                            ctx,
                            item,
                            project,
                            readers=readers,
                            observations=observations,
                        )
            return page
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
        for key in ("next_call", "open_family"):
            if key in result:
                result[key]["tool"] = "manage_tools"
                result[key]["arguments"]["action"] = "search"
        return result
    if action in ("activate", "deactivate", "reset") and modern:
        if discovery is None or await discovery.scope_key(ctx) is None:
            return {"success": False, "error": UNSCOPED_GUIDANCE}
    if action == "describe":
        if group is not None or not tool:
            return {"success": False, "error": "describe requires a tool name and no group."}
        visible = await _selection_inventory(ctx)
        entries = _catalog_entries(entries, discovery, visible or {})
        name = tool.strip()
        definition = visible.get(name) if visible is not None else None
        if definition is None and discovery is not None:
            definition = discovery.definitions.get(name)
        if definition is None or name not in entries:
            return {
                "success": False,
                "error": "Unknown tool in this catalog and the current advertised inventory. Browse catalog or refresh the client tool list.",
            }
        registered = discovery.definitions.get(name) if discovery is not None else None
        result = _describe_binding(entries[name], definition, operation, visible, registered)
        if not visible or name not in visible or "manage_tools" not in visible:
            return result
        if result.get("success") is False:
            if operation is not None:
                result["next_call"] = _describe_call(name, await _retry_project_binding(ctx))
            return result
        if operation is None:
            result.update(
                _operation_choice_page(
                    name, result, await _retry_project_binding(ctx), offset, limit
                )
            )
        card = result.get("operation")
        if card is not None:
            draft = dict(card["invoke"]["arguments"])
            project = (
                {}
                if "unity_instance" in card["forbidden_parameters"]
                else await _retry_project_binding(ctx)
            )
            if "unity_instance" in definition.parameters.get("properties", {}):
                card["invoke"]["arguments"].update(project)
            card["prepare_call"] = {
                "tool": "manage_tools",
                "arguments": {
                    "action": "prepare",
                    "tool": name,
                    **({"operation": operation} if operation is not None else {}),
                    "arguments": draft,
                    **project,
                },
            }
            card["prepare_exact_text"] = {
                "description": _EXACT_TEXT_DESCRIPTION,
                "example": _prepare_exact_text_example(),
            }
            card["input_contract"] += (
                " Start with this operation's prepare_call.arguments.arguments. Add only its documented "
                "inputs and use the returned prepared_call after ready=true."
            )
        return result
    if action == "list_instances":
        from services.resource_inventory import read_inventory

        if group is not None or tool is not None or query is not None or limit != 5:
            return {"success": False, "error": "list_instances takes only its action."}
        return await read_inventory(
            ctx,
            "mcpforunity://instances",
            "Editor inventory",
            reader="manage_tools.list_instances",
            project_scoped=False,
        )
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
        visible = await _selection_inventory(ctx)
        advertised = tool in visible if visible is not None else None
        result = {
            "activated_tool" if action == "activate" else "deactivated_tool": tool,
            "changed": changed,
            "server_advertised": advertised,
            "client_inventory_status": "unconfirmed",
            "message": REFRESH_GUIDANCE,
        }
        if visible is None:
            result["message"] = (
                "Selection processed. The server inventory was not observed for this caller."
            )
            return result
        binding = await _retry_project_binding(ctx)
        result["refresh_request"] = _tool_list_request(binding)
        diagnostic = {"resource": f"mcpforunity://tool-diagnostics/{tool}"}
        if binding:
            diagnostic["_meta"] = binding
        if action == "activate" and not advertised:
            result.update(
                message="Selected for this scope, but absent from its advertised inventory. Check the tool diagnostic for Editor availability and permission.",
                next_action=diagnostic,
            )
        if include_schema:
            from services.registry.action_catalog import declared_actions

            definition = visible.get(tool)
            if definition is None and discovery is not None:
                definition = discovery.definitions.get(tool)
            if definition is not None:
                registered = discovery.definitions.get(tool) if discovery is not None else None
                result["description"] = _describe_binding(
                    entry, definition, None, visible, registered
                )
            actions = declared_actions(entry)
            result["readiness"] = {
                "status": "unobserved",
                "next_action": {"tool": tool, "action": "capabilities", **binding}
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
        visible = await _selection_inventory(ctx)
        return {
            "activated" if enabled else "deactivated": group,
            "tools": names if visible is not None else None,
            "server_advertised_tools": sorted(visible.keys() & set(names))
            if visible is not None
            else None,
            "changed": changed,
            "client_inventory_status": "unconfirmed",
            **(await _selection_refresh(ctx, visible)),
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
        visible = await _selection_inventory(ctx)
        return {
            "reset": True,
            "default_groups": sorted(
                discovery.default_groups if discovery else DEFAULT_ENABLED_GROUPS
            )
            if visible is not None
            else None,
            "visible_tools": sorted(visible) if visible is not None else None,
            "client_inventory_status": "unconfirmed",
            **(await _selection_refresh(ctx, visible)),
        }

    return {"success": False, "error": f"Unknown action '{action}'"}


def _describe_binding(entry, definition, operation, visible, registered):
    from services.registry.action_catalog import describe_action

    descriptor = definition.to_mcp_tool()
    if visible is not None and definition.name in visible:
        same_registration = registered is not None and PreviewBinding.capture(registered).matches(
            definition
        )
        # tools/list may inline local references from this same registration.
        same_schema = same_registration and (
            registered.parameters == definition.parameters
            or dereference_refs(registered.parameters) == definition.parameters
        )
        if not same_schema:
            entry = {
                "name": definition.name,
                "group": entry.get("group") if same_registration else None,
                "wire_only": True,
            }
        entry = {
            **entry,
            "title": descriptor.title,
            "description": descriptor.description,
            "kwargs": {"annotations": descriptor.annotations or {}, "meta": descriptor.meta or {}},
        }
    try:
        return describe_action(
            entry,
            descriptor.model_dump(mode="json", exclude_none=True),
            operation,
            visible_names=set(visible) if visible is not None else None,
        )
    except ValueError as error:
        return {"success": False, "error": str(error)}


async def _prepare_binding(ctx, name, draft, original, operation, *, exact_text=None):
    visible = await _selection_inventory(ctx)
    if visible is None or name not in visible:
        return not_prepared("The tool is unavailable on this connection.")
    definition = visible[name]
    entry = next((item for item in get_registered_tools() if item["name"] == name), None)
    discovery = getattr(ctx.fastmcp, "_tool_discovery", None)
    registered = discovery.definitions.get(name) if discovery else None
    if registered is not None and not PreviewBinding.capture(registered).matches(definition):
        entry = None
    schema = definition.parameters
    selected_action = None
    if operation is not None:
        descriptor = _describe_binding(
            entry or {"name": name, "kwargs": {}}, definition, operation, visible, registered
        )
        if descriptor.get("success") is False:
            choices = descriptor.get("input_choices")
            return not_prepared(
                descriptor["error"], **({"input_choices": choices} if choices else {})
            )
        target = descriptor["tool"]
        schema = target.get("inputSchema", target.get("input_schema"))
        if not isinstance(schema, dict):
            return not_prepared("The selected operation has no advertised input schema.")
        card = descriptor["operation"]
        selected_action = card["action"]
        if variant := card.get("input_variant"):
            draft = {variant["selector"]: variant["value"], **draft}
    func = entry.get("func") if entry else None
    closed = func is not None and all(
        p.kind is not inspect.Parameter.VAR_KEYWORD
        for p in inspect.signature(func).parameters.values()
    )
    project = (await _retry_project_binding(ctx)).get("unity_instance")
    selector = selected_action if operation is not None else draft.get("action")
    if "unity_instance" in forbidden_action_parameters(
        name if func else None, selector, schema
    ) or (func and connection_inventory_call(ctx.fastmcp, name, {**draft, "action": selector})):
        if draft.get("unity_instance") is not None:
            return not_prepared("This operation does not accept a target unity_instance.")
        project = None
    elif "unity_instance" in draft and (project is None or draft["unity_instance"] != project):
        from transport.unity_instance_middleware import UnityInstanceMiddleware

        router = next(
            (m for m in ctx.fastmcp.middleware if isinstance(m, UnityInstanceMiddleware)), None
        )
        if router is not None:
            # A nested selector may bind the same Editor whose inventory we just read.
            # Resolve it even on an exact match when the outer call omitted the selector.
            project = project or await ctx.get_state("unity_instance")
            if not isinstance(project, str) or not project:
                return not_prepared("Select the intended project on the prepare request.")
            try:
                target = await router._resolve_instance_value(
                    draft["unity_instance"], ctx, allow_reconnect=False
                )
            except ValueError as exc:
                return not_prepared(str(exc))
            draft = {**draft, "unity_instance": target}
    result = prepare_tool_call(
        name,
        schema,
        draft,
        operation=selected_action,
        original=original,
        project=project,
        registered=func is not None,
        closed_signature=closed,
        exact_text=exact_text,
    )
    if not result["ready"] or not entry:
        return result
    # The registry's unity_target controls availability. It does not establish
    # where the selected action will run or whether it writes a client file.
    arguments = result["prepared_call"]["arguments"]
    if (
        name == "read_unity_resource"
        or name == "manage_tools"
        and arguments.get("action") == "read_resource"
    ) and arguments.get("uri") is not None:
        from transport.unity_instance_middleware import _trusted_builtin_callback

        if _trusted_builtin_callback(
            getattr(definition, "fn", None), name, None
        ) and not await can_prepare_observation(ctx, arguments["uri"], project):
            return not_prepared(
                "The requested URI or options are outside this observation tool's read policy. Getter execution requires native resources/read and its required grants."
            )
    execution = await prepared_binding_execution(ctx, registered, definition, arguments)
    if execution is not None:
        result["execution"] = execution
    return result


def _tool_list_request(binding: dict[str, str]) -> dict:
    return {"method": "tools/list", "params": {"_meta": binding} if binding else {}}


async def _selection_refresh(ctx: Context, visible) -> dict:
    if visible is None:
        return {
            "message": "Selection processed. The server inventory was not observed for this caller."
        }
    from services.tool_discovery import REFRESH_GUIDANCE

    return {
        "refresh_request": _tool_list_request(await _retry_project_binding(ctx)),
        "message": REFRESH_GUIDANCE,
    }


async def _selection_inventory(ctx: Context):
    """Selection and optional inventory observation have independent admission."""
    from fastmcp.exceptions import AuthorizationError
    from fastmcp.utilities.versions import dedupe_with_versions
    from services.operation_permissions import OperationPermissionError, remote_operation_allowed

    if not await remote_operation_allowed("manage_tools", {"action": "inspect_inventory"}, ctx=ctx):
        return None
    try:
        with internal_catalog(ctx.fastmcp, "tools/list"):
            tools = await ctx.fastmcp.list_tools()
    except (OperationPermissionError, AuthorizationError):
        return None
    return {item.name: item for item in dedupe_with_versions(tools, lambda item: item.name)}


async def _catalog_resources(
    ctx: Context, *, concrete_scheme: str | None = None
) -> list[CatalogResource]:
    from fastmcp.exceptions import AuthorizationError
    from fastmcp.utilities.versions import dedupe_with_versions
    from services.operation_permissions import authorize_remote_resource, remote_operation_allowed
    from services.resource_inventory import (
        catalog_observation,
        same_resource_binding,
    )

    await authorize_remote_resource()
    with internal_resource_catalog(ctx.fastmcp):
        listed = await ctx.fastmcp.list_resources()
        templates = [] if concrete_scheme else await ctx.fastmcp.list_resource_templates()
    resources: list[CatalogResource] = []
    by_uri = {}
    for item in listed:
        by_uri.setdefault(str(item.uri), []).append(item)
    for uri, versions in by_uri.items():
        if not uri.startswith(("mcpforunity://", "skill://")) or (
            concrete_scheme and not uri.startswith(f"{concrete_scheme}://")
        ):
            continue
        try:
            # A listed descriptor can still have a separate resource read policy.
            selected = await ctx.fastmcp.get_resource(uri)
            item = next(
                (version for version in versions if same_resource_binding(selected, version)), None
            )
            if item is None:
                continue
            await ctx.fastmcp._check_resource_access(uri)
        except AuthorizationError:
            continue
        resources.append(
            {
                "uri": uri,
                "name": item.name,
                "description": item.description or "",
                "kwargs": {"meta": item.meta},
                "catalog_observation": catalog_observation(ctx.fastmcp, selected)
                and observation_uri_allowed(uri)
                and await remote_operation_allowed("read_unity_resource", {"uri": uri}, ctx=ctx),
            }
        )
    domains = {}
    for item in templates:
        domain = (
            observation_template_domain(item) if catalog_observation(ctx.fastmcp, item) else None
        )
        # An unversioned route cannot promise a domain that differs across its published versions.
        uri = item.uri_template
        domains[uri] = domain if domains.get(uri, domain) == domain else None
    resources.extend(
        {
            "uri": item.uri_template,
            "name": item.name,
            "description": item.description or "",
            "kwargs": {"meta": item.meta},
            "schema_required": tuple(item.parameters.get("required", ())),
            "catalog_observation": domains[item.uri_template] is not None,
            "observation_expansion": domains[item.uri_template],
        }
        for item in dedupe_with_versions(templates, lambda item: item.uri_template)
    )
    return resources


def _observation_domains(resources):
    return {
        r["uri"]: r.get("observation_expansion") or {}
        for r in resources
        if r.get("catalog_observation")
    }


async def _catalog_inventory(ctx, declarations, discovery):
    visible = {item.name: item for item in await _visible_tools(ctx)}
    return _catalog_entries(declarations, discovery, visible), visible


def _catalog_entries(declarations, discovery, visible):
    """Retain selectable built-ins and add wire descriptors visible to this request only."""
    registered = discovery.definitions if discovery is not None else visible
    entries = {entry["name"]: entry for entry in declarations if entry["name"] in registered}
    for name, item in visible.items():
        if name in entries:
            entries[name] = {**entries[name], "input_schema": item.parameters}
        else:
            entries[name] = {
                "name": name,
                "group": None,
                "description": item.description,
                "kwargs": {"annotations": item.annotations},
                "input_schema": item.parameters,
            }
    return entries


def _public_catalog_entries(discovery, visible: dict[str, Tool]) -> dict[str, CatalogEntry]:
    """Use advertised descriptors; retain group membership for established registrations."""
    entries: dict[str, CatalogEntry] = {}
    for name, item in visible.items():
        descriptor = item.to_mcp_tool()
        original = discovery.definitions.get(name) if discovery is not None else None
        same_registration = original is not None and PreviewBinding.capture(original).matches(item)
        entries[name] = {
            "name": name,
            "group": discovery.groups.get(name) if same_registration else None,
            "title": descriptor.title,
            "description": descriptor.description,
            "kwargs": {"annotations": descriptor.annotations or {}, "meta": descriptor.meta or {}},
            "input_schema": descriptor.input_schema,
            "wire_only": True,
        }
    return entries


async def _visible_tools(ctx: Context):
    """Read the same final availability cap used by tools/list."""
    # Server/session transforms and authenticated Unity catalogs both affect visibility.
    # Reuse this request's resolved routing instead of reinjecting it via tools/list.
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    async def collect():
        return await ctx.fastmcp.list_tools(run_middleware=False)

    for middleware in ctx.fastmcp.middleware:
        if isinstance(middleware, UnityInstanceMiddleware):
            return await middleware.list_tools_for_context(ctx, collect)
    return await collect()


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
