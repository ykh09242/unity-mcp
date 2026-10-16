"""MCP tools package - auto-discovery and Unity routing helpers."""

import logging
import os
from pathlib import Path
from typing import TypeVar

from fastmcp import Context, FastMCP
from core.telemetry_decorator import telemetry_tool
from core.logging_decorator import log_execution
from utils.module_discovery import discover_modules
from services.registry import get_registered_tools, TOOL_GROUPS, DEFAULT_ENABLED_GROUPS

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

    On local HTTP servers, non-default tool groups are disabled at the server level
    so that new sessions only see the *core* tools (plus always-visible meta-tools).
    Stateful clients can adjust their session's groups via ``manage_tools``.
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
        wrapped = log_execution(tool_name, "Tool")(func)
        wrapped = telemetry_tool(tool_name)(wrapped)
        # Keep the global declaration unchanged: each server owns its decorators.
        mcp.tool(name=tool_name, description=description, **kwargs)(wrapped)
        logger.debug(f"Registered tool: {tool_name} - {description}")

    logger.info(f"Registered {len(tools)} MCP tools")

    # In HTTP mode, disable non-default groups at the server level so new
    # sessions start lean.  Unity will re-enable groups via register_tools
    # (PluginHub._sync_server_tool_visibility) once it connects.
    # In stdio mode we skip this: the legacy TCP bridge has no register_tools
    # message, so disabled groups would stay invisible for the entire session.
    # Tools with group=None (no tag) are unaffected and always visible.
    from core.config import config as server_config

    transport_mode = (server_config.transport_mode or "stdio").lower()
    if transport_mode == "http" and server_config.http_remote_hosted:
        logger.info("Remote HTTP transport: tool availability follows each user's Unity catalog.")
    elif transport_mode == "http":
        groups_to_disable = set(TOOL_GROUPS.keys()) - DEFAULT_ENABLED_GROUPS
        for group_name in sorted(groups_to_disable):
            tag = f"group:{group_name}"
            mcp.disable(tags={tag}, components={"tool"})
            logger.debug(f"Disabled tool group at startup: {group_name}")
        logger.info(
            f"Default tool groups: {', '.join(sorted(DEFAULT_ENABLED_GROUPS))}. "
            f"Disabled: {', '.join(sorted(groups_to_disable))}. "
            "Use manage_tools to activate more."
        )
    else:
        logger.info(
            "Stdio transport: all tool groups enabled at startup. "
            "Will sync with Unity's tool states after connecting."
        )


async def sync_tool_visibility_from_unity(
    instance_id: str | None = None,
    notify: bool = True,
) -> dict:
    """Query Unity for tool enabled/disabled states and sync server-level visibility.

    This bridges the gap in stdio mode where Unity can't push ``register_tools``
    messages.  The Python server queries Unity's ``get_tool_states`` resource via
    the legacy TCP connection and feeds the result into
    ``PluginHub._sync_server_tool_visibility``.

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

    try:
        registry = PluginHub._registry
        session_id = None
        http_revision = None
        if config.transport_mode.lower() == "http" and registry is not None:
            session_id = await PluginHub._resolve_session_id(instance_id, retry_on_reload=False)
            session = await registry.get_session(session_id)
            http_revision = session.tools_revision if session is not None else None

        from services.custom_tool_service import CustomToolService

        service = CustomToolService._instance
        captured_sources = dict(service._sources) if service is not None else {}
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
            raise ValueError("Unity catalog exceeds supported bounds")
        # Validate the complete raw snapshot before filtering disabled tools.
        # Partial parsing must never turn a malformed member into a deletion.
        extended = any(isinstance(tool, dict) and "is_built_in" in tool for tool in tools)
        parsed_tools = []
        names = set()
        for tool in tools:
            if not isinstance(tool, dict) or type(tool.get("enabled", True)) is not bool:
                raise ValueError("Malformed Unity catalog descriptor")
            if extended and type(tool.get("is_built_in")) is not bool:
                raise ValueError("Incomplete extended Unity catalog")
            parsed = ToolDefinitionModel.model_validate(tool)
            if parsed.name in names:
                raise ValueError("Duplicate Unity catalog tool name")
            names.add(parsed.name)
            parsed_tools.append(parsed)
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

            plan = PluginHub._prepare_server_tool_visibility(enabled_tools)
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
                if service is not None and authoritative:
                    visibility = (
                        None
                        if plan is None
                        else (
                            lambda: PluginHub._commit_server_tool_visibility(plan),
                            lambda: PluginHub._rollback_server_tool_visibility(plan),
                        )
                    )
                    outcome = _commit_stdio_catalog(
                        connection,
                        generation,
                        lambda: service.replace_project_catalog(
                            project,
                            custom_models,
                            kind="stdio",
                            generation=generation,
                            expected_revision=previous.revision if previous is not None else 0,
                            visibility=visibility,
                        ),
                    )
                    if correlation is not None:
                        settings_sync = {
                            "status": "applied",
                            **correlation.model_dump(),
                            "source_kind": "stdio",
                            "source_generation": generation,
                            "catalog_revision": outcome["revision"],
                        }
                elif tools:
                    _commit_stdio_catalog(
                        connection,
                        generation,
                        lambda: PluginHub._commit_server_tool_visibility(plan),
                    )
                    outcome = {"changed": plan is not None}
                else:
                    raise ValueError("Empty Unity catalog has no same-generation extended evidence")
            elif tools and not extended:
                # Compatibility for unknown old peers: visibility only, never removal.
                PluginHub._commit_server_tool_visibility(plan)
                outcome = {"changed": plan is not None}
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
