"""MCP tools package - auto-discovery and Unity routing helpers."""

import logging
import os
from pathlib import Path
from typing import TypeVar

from fastmcp import Context, FastMCP
from fastmcp.tools import FunctionTool, Tool
from core.telemetry_decorator import telemetry_tool
from core.logging_decorator import log_execution
from utils.module_discovery import discover_modules
from services.registry import get_registered_tools, TOOL_GROUPS
from services.registry.action_contracts import ACTION_RULES, action_input_schema
from services.registry.routing_schema import with_unity_instance
from services.registry.operation_invocation import wrap_tool

logger = logging.getLogger("mcp-for-unity-server")

# Export decorator and helpers for easy imports within tools
__all__ = [
    "register_all_tools",
    "sync_tool_visibility_from_unity",
    "get_unity_instance_from_context",
]


def register_all_tools(mcp: FastMCP, *, project_scoped_tools: bool = True):
    """
    Auto-discover and register all tools in the tools/ directory.

    Any .py file in this directory or subdirectories with @mcp_for_unity_tool decorated
    functions will be automatically registered.

    On local servers, non-default tool groups are disabled at the server level
    so that new sessions only see the *core* tools (plus always-visible meta-tools).
    Legacy clients and trusted project-bound modern scopes can adjust selection
    via ``manage_tools``. UNITY_MCP_TOOL_PROFILE selects minimal/core/full defaults.
    Hosted remote servers use each authenticated user's Unity catalog instead;
    request middleware filters discovery and rejects disabled tool calls.
    A session override cannot grant access to a tool absent from that catalog.
    """
    logger.info("Auto-discovering MCP for Unity Server tools...")
    # Dynamic import of all modules in this directory
    tools_dir = Path(__file__).parent

    # Discover and import all modules
    list(discover_modules(tools_dir, __package__))

    tools = get_registered_tools()

    if not tools:
        logger.warning("No MCP tools registered!")
        return

    definitions = {}
    groups = {}
    for tool_info in tools:
        func = tool_info["func"]
        tool_name = tool_info["name"]
        description = tool_info["description"]
        kwargs = tool_info["kwargs"]

        if not project_scoped_tools and tool_name == "execute_custom_tool":
            logger.info("Skipping execute_custom_tool registration (project-scoped tools disabled)")
            continue

        # Apply decorators: logging -> telemetry -> mcp.tool
        # Note: Parameter normalization (camelCase -> snake_case) is handled by
        # ParamNormalizerMiddleware before FastMCP validation
        admitted = wrap_tool(func, tool_name, tool_info.get("unity_target"))
        wrapped = log_execution(tool_name, "Tool")(admitted)
        wrapped = telemetry_tool(tool_name)(wrapped)
        # Keep the global declaration unchanged: each server owns its decorators.
        factory = FunctionTool if tool_name in ACTION_RULES else Tool
        registered = factory.from_function(
            wrapped, name=tool_name, description=description, run_in_thread=True, **kwargs
        )
        if tool_name in ACTION_RULES:
            registered.parameters = action_input_schema(tool_name, registered.parameters)
        if tool_name != "get_execution_receipt":
            registered.parameters = with_unity_instance(registered.parameters)
        mcp.add_tool(registered)
        definitions[tool_name] = registered
        groups[tool_name] = tool_info.get("group")
        logger.debug(f"Registered tool: {tool_name} - {description}")

    logger.info(f"Registered {len(tools)} MCP tools")

    from core.config import config as server_config
    from services.tool_discovery import SETUP_GUIDANCE, ToolDiscovery

    discovery = ToolDiscovery(
        mcp,
        definitions,
        groups,
        hosted=server_config.transport_mode.lower() == "http" and server_config.http_remote_hosted,
    )
    mcp._tool_discovery = discovery
    for group_name in sorted(set(TOOL_GROUPS) - discovery.default_groups):
        mcp.disable(tags={f"group:{group_name}"}, components={"tool"})
    mcp.add_transform(discovery)
    logger.info("Tool discovery profile: %s. %s", discovery.profile, SETUP_GUIDANCE)


async def sync_tool_visibility_from_unity(
    instance_id: str | None = None,
    notify: bool = True,
) -> dict:
    """Refresh source-owned availability without changing client selection.

    Stdio peers cannot push ``register_tools``. Query ``get_tool_states`` and
    publish only to the authenticated connection's project/generation.

    Args:
        instance_id: Optional Unity instance identifier.
        notify: If True, send ``tools/list_changed`` to connected MCP sessions.

    Returns:
        dict with sync results (enabled/disabled groups, tool count).
    """
    from core.config import config

    if config.http_remote_hosted:
        return {
            "error": "Tool sync is unavailable in remote-hosted mode; Unity pushes its catalog through the authenticated plugin session.",
            "remote_sync_disabled": True,
        }

    from transport.legacy.unity_connection import async_send_command_with_retry
    from transport.plugin_hub import PluginHub
    from transport.unity_transport import send_with_unity_instance

    service = None
    captured_sources = {}
    capture = []
    diagnostic_socket = None
    diagnostic_reason = "metadata_invalid"
    diagnostic_names = []
    diagnostic_offending = None
    diagnostic_sequence = 0
    http_revision = None

    async def record_failure():
        if service is None:
            return
        if config.transport_mode.lower() == "http":
            if diagnostic_socket is not None:
                await PluginHub._record_tool_diagnostic_failure(
                    diagnostic_socket,
                    diagnostic_reason,
                    diagnostic_names,
                    diagnostic_sequence,
                    expected_revision=http_revision,
                    offending=diagnostic_offending,
                )
            return
        if not capture:
            return
        from transport.legacy.unity_connection import _commit_stdio_catalog

        connection, generation = capture[0]
        project = connection.instance_id.rsplit("@", 1)[-1]

        def publish_failure():
            previous = captured_sources.get(("stdio", None, project))
            with service._catalog_lock:
                if service._sources.get(("stdio", None, project)) is not previous:
                    return
                if service.diagnostic_attempt_is_newer(
                    "stdio", project, None, generation, diagnostic_sequence
                ):
                    return
                service.record_diagnostic_attempt(
                    "stdio",
                    project,
                    generation,
                    names=diagnostic_names,
                    reason=diagnostic_reason,
                    offending=diagnostic_offending,
                    retained=previous is not None,
                    revision=previous.revision if previous is not None else 0,
                )

        try:
            _commit_stdio_catalog(connection, generation, publish_failure)
        except ValueError:
            logger.debug("Discarded stale/busy diagnostic source")

    try:
        registry = PluginHub._registry
        session_id = None
        http_revision = None
        if config.transport_mode.lower() == "http" and registry is not None:
            session_id = await PluginHub._resolve_session_id(instance_id, retry_on_reload=False)
            session = await registry.get_session(session_id)
            http_revision = session.tools_revision if session is not None else None
            diagnostic_socket = PluginHub._connections.get(session_id)

        from services.custom_tool_service import CustomToolService

        service = CustomToolService._instance
        captured_sources = dict(service._sources) if service is not None else {}
        diagnostic_sequence = service._diagnostic_sequence if service is not None else 0
        capture = []
        response = await send_with_unity_instance(
            async_send_command_with_retry,
            instance_id,
            "get_tool_states",
            {},
            retry_on_reload=False,
            **({"_catalog_capture": capture} if config.transport_mode.lower() != "http" else {}),
        )

        # Detect unsupported command (Unity package too old)
        if isinstance(response, dict):
            error_msg = response.get("error") or response.get("message") or ""
            if isinstance(error_msg, str) and (
                "unknown" in error_msg.lower() or "unsupported command" in error_msg.lower()
            ):
                logger.debug(
                    "Unity does not support get_tool_states yet — "
                    "update the MCPForUnity package to enable tool toggle sync"
                )
                return {
                    "error": "Unity package does not support get_tool_states. "
                    "Update MCPForUnity to the latest version to enable "
                    "tool toggle syncing from the Unity Editor GUI.",
                    "unsupported": True,
                }

        # Extract tool list from response
        tools = None
        if isinstance(response, dict):
            # SuccessResponse wraps data in "data" key
            data = response.get("data")
            if isinstance(data, dict):
                tools = data.get("tools")
            elif isinstance(data, list):
                tools = data
            # Fallback: maybe tools directly in response
            if tools is None:
                tools = response.get("tools")

        if not isinstance(tools, list):
            logger.debug(
                "sync_tool_visibility_from_unity: no tool data in Unity response",
            )
            await record_failure()
            return {"error": "No tool data returned from Unity"}

        from transport.models import ToolSettingsCorrelation

        correlation = None
        if isinstance(data, dict) and any(
            key in data for key in ("settings_epoch", "desired_revision")
        ):
            correlation = ToolSettingsCorrelation.model_validate(data)
        settings_sync = {"status": "unconfirmed", "reason": "peer_provenance_unavailable"}
        notification_status = "not_requested" if not notify else "not_needed"

        from models.models import ToolDefinitionModel
        import json

        if len(tools) > 256 or len(json.dumps(tools, allow_nan=False).encode("utf-8")) > 512 * 1024:
            diagnostic_reason = "diagnostic_limit"
            raise ValueError("Unity catalog exceeds supported bounds")
        # Validate the complete raw snapshot before filtering disabled tools.
        # Partial parsing must never turn a malformed member into a deletion.
        extended = any(isinstance(tool, dict) and "is_built_in" in tool for tool in tools)
        parsed_tools = []
        names = set()
        for tool in tools:
            candidate = tool.get("name") if isinstance(tool, dict) else None
            diagnostic_offending = (
                candidate
                if isinstance(candidate, str)
                and 0 < len(candidate) <= 128
                and all(c.isalnum() or c in "_.-" for c in candidate)
                else None
            )
            if diagnostic_offending is None:
                diagnostic_names = []
            else:
                diagnostic_names.append(diagnostic_offending)
            if not isinstance(tool, dict) or type(tool.get("enabled", True)) is not bool:
                raise ValueError("Malformed Unity catalog descriptor")
            if extended and type(tool.get("is_built_in")) is not bool:
                raise ValueError("Incomplete extended Unity catalog")
            parsed = ToolDefinitionModel.model_validate(tool)
            if parsed.name in names:
                diagnostic_reason = "duplicate_name"
                raise ValueError("Duplicate Unity catalog tool name")
            names.add(parsed.name)
            parsed_tools.append(parsed)
        diagnostic_offending = None
        diagnostic_reason = "publication_failed"
        enabled_tools = [tool for tool in tools if tool.get("enabled", True)]
        enabled_models = [
            parsed for raw, parsed in zip(tools, parsed_tools) if raw.get("enabled", True)
        ]
        custom_models = [
            parsed
            for raw, parsed in zip(tools, parsed_tools)
            if raw.get("enabled", True) and not raw.get("is_built_in", True)
        ]
        outcome = {"changed": False}
        if config.transport_mode.lower() == "http":
            hub_lock = PluginHub._lock
            if registry is None or session_id is None or hub_lock is None:
                raise ValueError("Unity catalog owner unavailable")
            scope = None
            async with hub_lock:  # pylint: disable=not-async-context-manager
                if PluginHub._registry is not registry:
                    raise ValueError("Unity catalog owner changed")
                session = await registry.get_session(session_id)
                if (
                    session is None
                    or session.tools_revision != http_revision
                    or PluginHub._connections.get(session_id) is None
                ):
                    raise ValueError("Unity catalog generation changed")
                previous = captured_sources.get(("http", session.user_id, session.project_hash))
                known_empty = (
                    previous is not None and previous.known and previous.generation == session_id
                )
                if not tools and not known_empty:
                    raise ValueError("Empty Unity catalog has no same-generation extended evidence")

                def publish(current, prepared):
                    nonlocal outcome, scope, settings_sync
                    outcome = PluginHub._publish_session_catalog(
                        current,
                        prepared,
                        registry._sessions,
                        known=extended or known_empty,
                        generation=current.session_id,
                    )
                    scope = dict(
                        user_id=current.user_id,
                        project_hash=current.project_hash,
                        session_id=current.session_id,
                        catalog_revision=current.tools_revision
                        + int(not current.tools_known or current.tools != prepared),
                    )
                    if correlation is not None and (extended or known_empty):
                        settings_sync = {
                            "status": "applied",
                            **correlation.model_dump(),
                            "source_kind": "http",
                            "source_generation": current.session_id,
                            "catalog_revision": scope["catalog_revision"],
                        }

                if not extended and tools:
                    # Old-format states carry toggles, not replacement schemas.
                    # Preserve known/omitted descriptors and admit new names
                    # only when the server already knows the Unity built-in.
                    unity_builtins = {
                        entry["unity_target"]
                        for entry in get_registered_tools()
                        if entry.get("unity_target")
                    }
                    toggled = dict(session.tools)
                    for raw in tools:
                        name = raw["name"]
                        if not raw.get("enabled", True):
                            toggled.pop(name, None)
                        elif name not in toggled and name in unity_builtins:
                            toggled[name] = ToolDefinitionModel(name=name)
                    enabled_models = list(toggled.values())
                    if (
                        len(enabled_models) > 256
                        or len(
                            json.dumps(
                                [tool.model_dump() for tool in enabled_models],
                                ensure_ascii=False,
                                allow_nan=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        )
                        > 512 * 1024
                    ):
                        raise ValueError("Unity catalog exceeds supported bounds")
                await registry.register_tools_for_session(
                    session_id,
                    enabled_models,
                    _publish=publish,
                )
            if notify:
                try:
                    await PluginHub._notify_committed_catalog(outcome, scope)
                    notification_status = (
                        "requested" if outcome and outcome.get("changed") else "not_needed"
                    )
                except Exception as exc:
                    notification_status = "failed"
                    logger.warning("Committed catalog notification failed (%s)", type(exc).__name__)
        else:
            from transport.legacy.unity_connection import (
                get_authenticated_stdio_generation,
                _commit_stdio_catalog,
            )

            if capture:
                connection, generation = capture[0]
                project = connection.instance_id.rsplit("@", 1)[-1]
                if (
                    not generation
                    or await get_authenticated_stdio_generation(connection.instance_id)
                    != generation
                ):
                    raise ValueError("Unity catalog generation changed during refresh")
                previous = captured_sources.get(("stdio", None, project))
                known_empty = (
                    previous is not None and previous.known and previous.generation == generation
                )
                authoritative = extended or (not tools and known_empty)
                if service is not None and (authoritative or tools):
                    catalog_models = enabled_models
                    known = authoritative
                    if not authoritative:
                        # Legacy replies are toggle deltas: retain omitted descriptors,
                        # and never invent a custom tool schema from just its name.
                        same_generation = previous is not None and previous.generation == generation
                        toggled = dict(previous.tools) if same_generation else {}
                        known = same_generation and previous.known
                        unity_builtins = {
                            entry["unity_target"]
                            for entry in get_registered_tools()
                            if entry.get("unity_target")
                        }
                        for raw in tools:
                            name = raw["name"]
                            if not raw.get("enabled", True):
                                toggled.pop(name, None)
                            elif name not in toggled and name in unity_builtins:
                                toggled[name] = ToolDefinitionModel(name=name)
                        catalog_models = list(toggled.values())
                    outcome = _commit_stdio_catalog(
                        connection,
                        generation,
                        lambda: service.replace_project_catalog(
                            project,
                            catalog_models,
                            kind="stdio",
                            generation=generation,
                            expected_revision=previous.revision if previous is not None else 0,
                            known=known,
                        ),
                    )
                    if correlation is not None and authoritative:
                        settings_sync = {
                            "status": "applied",
                            **correlation.model_dump(),
                            "source_kind": "stdio",
                            "source_generation": generation,
                            "catalog_revision": outcome["revision"],
                        }
                elif not tools:
                    raise ValueError("Empty Unity catalog has no same-generation extended evidence")
                else:
                    raise ValueError("Unity catalog service unavailable")
            elif tools and not extended:
                # An old peer without authenticated provenance can be inspected,
                # but cannot alter another project's catalog or server defaults.
                settings_sync = {"status": "unconfirmed", "reason": "peer_provenance_unavailable"}
            else:
                raise ValueError("Unity catalog has no authenticated connection provenance")
            if notify and outcome.get("changed"):
                try:
                    await PluginHub._notify_mcp_tool_list_changed()
                    notification_status = "requested"
                except Exception as exc:
                    notification_status = "failed"
                    logger.warning("Committed catalog notification failed (%s)", type(exc).__name__)
        custom_tool_count = len(custom_models) if extended and service is not None else 0

        # Build summary
        from services.registry import get_group_tool_names

        group_tools = get_group_tool_names()
        enabled_names = {t.get("name") for t in enabled_tools if t.get("name")}
        enabled_groups = []
        disabled_groups = []
        for group_name in sorted(TOOL_GROUPS.keys()):
            tool_names = group_tools.get(group_name, [])
            if any(n in enabled_names for n in tool_names):
                enabled_groups.append(group_name)
            else:
                disabled_groups.append(group_name)

        return {
            "synced": True,
            "enabled_groups": enabled_groups,
            "disabled_groups": disabled_groups,
            "enabled_tool_count": len(enabled_tools),
            "total_tool_count": len(tools),
            "custom_tool_count": custom_tool_count,
            "settings_sync": settings_sync,
            "notification_status": notification_status,
        }

    except Exception as exc:
        await record_failure()
        logger.warning(
            "Failed to sync tool visibility from Unity (%s)",
            type(exc).__name__,
        )
        return {"error": str(exc)}


async def get_unity_instance_from_context(
    ctx: Context,
    key: str = "unity_instance",
) -> str | None:
    """Extract the unity_instance value from middleware state.

    The instance is set via the set_active_instance tool and injected into
    request state by UnityInstanceMiddleware.
    """
    get_state_fn = getattr(ctx, "get_state", None)
    if callable(get_state_fn):
        try:
            return await get_state_fn(key)
        except Exception:  # pragma: no cover - defensive
            pass

    return None
