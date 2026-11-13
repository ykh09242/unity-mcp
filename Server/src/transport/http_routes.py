"""Install the HTTP control routes against one explicitly owned server runtime."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import config
from core.server_build import RUNNING_SERVER
from models.response_limits import bound_response
from models.connection_diagnosis import no_unity_instances_response
from core.server_runtime import ServerRuntime
from services.execution_receipt_context import owned_to_thread
from services.execution_receipt_identity import ReceiptConflict, ReceiptInvalid
from transport.execution_receipts import rest_receipt_control
from transport.local_project_context import (
    ContextCommand,
    LocalProjectContext,
    full_hash,
    normalized_root,
    local_project_context,
)
from transport.local_server_lifecycle import request_local_shutdown
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import (
    CommandNotDispatchedError,
    PluginHub,
    _CommandBudget,
    _HttpRequestBudget,
    http_request_budget,
    command_outcome_unknown,
)
from transport.rest_command_input import InputFailure, ReceiptedCommand, RestCommandInput

logger = logging.getLogger("mcp-for-unity-server")


def _normalize_instance_token(instance_token: str | None) -> tuple[str | None, str | None]:
    if not instance_token:
        return None, None
    if "@" in instance_token:
        name_part, _, hash_part = instance_token.rpartition("@")
        return (name_part or None), (hash_part or None)
    return None, instance_token


def _select_local_session(
    sessions: SessionList,
    instance_token: str | None,
) -> tuple[str | None, SessionDetails | None]:
    """Resolve one unique target; composite names must agree with the exact hash."""
    if instance_token is None:
        return (
            next(iter(sessions.sessions.items())) if len(sessions.sessions) == 1 else (None, None)
        )
    if not isinstance(instance_token, str) or not instance_token:
        return None, None
    instance_name, instance_hash = _normalize_instance_token(instance_token)
    matches = [pair for pair in sessions.sessions.items() if pair[1].hash == instance_hash]
    if matches:
        if "@" in instance_token and (
            not instance_name or any(pair[1].project != instance_name for pair in matches)
        ):
            return None, None
        return matches[0] if len(matches) == 1 else (None, None)
    if "@" in instance_token:
        return None, None
    matches = [
        pair
        for pair in sessions.sessions.items()
        if pair[1].project in (instance_name, instance_token)
    ]
    return matches[0] if len(matches) == 1 else (None, None)


def _local_target_refusal(sessions: SessionList, instance_token: str | None) -> JSONResponse:
    """Expose selectable IDs without dispatching an ambiguous or absent target."""
    if instance_token is not None and (not isinstance(instance_token, str) or not instance_token):
        return JSONResponse(
            {
                "success": False,
                "error": "Unity instance must be a nonempty string or null",
                "data": {"dispatch": "not_dispatched"},
            },
            status_code=400,
        )
    _, target_hash = _normalize_instance_token(instance_token)
    exists = not instance_token or any(
        details.hash == target_hash
        or ("@" not in instance_token and details.project == instance_token)
        for details in sessions.sessions.values()
    )
    instance_name, _ = _normalize_instance_token(instance_token)
    conflicting_name = bool(
        instance_token
        and "@" in instance_token
        and any(
            details.hash == target_hash and details.project != instance_name
            for details in sessions.sessions.values()
        )
    )
    if conflicting_name:
        error = (
            "Unity instance name and hash do not match; copy the complete Name@hash from discovery."
        )
    else:
        error = (
            "Multiple Unity instances match or no target was selected; supply an exact --instance hash."
            if exists
            else f"Unity instance '{instance_token}' not found"
        )
    return JSONResponse(
        {
            "success": False,
            "error": error,
            "data": {
                "dispatch": "not_dispatched",
                "available_instances": [
                    f"{details.project}@{details.hash}" for details in sessions.sessions.values()
                ],
            },
        },
        status_code=409 if exists else 404,
    )


def install_http_routes(
    mcp: FastMCP,
    runtime: ServerRuntime,
    *,
    managed_instance_token: str | None,
    resolve_project_id_for_unity_instance: Callable[[str | None], str | None],
    execute_receipted_rest: Callable[..., Awaitable[JSONResponse]],
    server_version: Callable[[], str],
) -> None:
    """Capture this server's owners and explicit compatibility callbacks once."""

    @mcp.custom_route("/api/reference-graph", methods=["POST"])
    async def reference_graph_operations_route(request: Request) -> JSONResponse:
        from services.reference_graph.http import handle

        service = runtime.reference_graph
        if service is None:
            return JSONResponse(
                {"success": False, "error": "reference_graph_unavailable"}, status_code=503
            )
        return await handle(request, service)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_http(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "timestamp": time.time(),
                "version": server_version(),
                "server_build": RUNNING_SERVER.command_metadata(),
                "message": "Unity MCP (ykh09242) server is running",
            }
        )

    @mcp.custom_route("/api/auth/login-url", methods=["GET"])
    async def auth_login_url(_: Request) -> JSONResponse:
        """Return the login URL for users to obtain/manage API keys."""
        if not config.api_key_login_url:
            return JSONResponse(
                {
                    "success": False,
                    "error": "API key management not configured. Contact your server administrator.",
                },
                status_code=404,
            )
        return JSONResponse(
            {
                "success": True,
                "login_url": config.api_key_login_url,
            }
        )

    # Only expose CLI routes if running locally (not in remote hosted mode)
    if not config.http_remote_hosted:
        command_inputs = RestCommandInput()

        async def parsed_rest_command(request, path, forward, execute):
            async with command_inputs.parse(
                request, guarded=path == "/api/context-command"
            ) as body:
                if isinstance(body, InputFailure):
                    return body.response()
                if isinstance(body, ReceiptedCommand):
                    return await execute_receipted_rest(
                        runtime.execution_receipts,
                        request,
                        body.body,
                        path,
                        forward,
                        options=body.options,
                    )
                if isinstance(body, dict) and body["type"] == "get_execution_receipt":
                    try:
                        return await rest_receipt_control(runtime.execution_receipts, request, body)
                    except ReceiptInvalid:
                        return InputFailure.INVALID.response()
                return await execute(body)

        @mcp.custom_route("/api/server/shutdown", methods=["POST"])
        async def local_shutdown_route(request: Request) -> JSONResponse:
            return await request_local_shutdown(request, managed_instance_token)

        @mcp.custom_route("/api/command", methods=["POST"])
        async def cli_command_route(request: Request) -> JSONResponse:
            """REST endpoint for CLI commands to Unity."""
            try:
                return await parsed_rest_command(
                    request, "/api/command", cli_command_route, cli_command
                )
            except Exception as e:
                logger.error("CLI command failed (%s)", type(e).__name__)
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        async def cli_command(body: dict) -> JSONResponse:
            command_type = body["type"]
            params = body.get("params", {})
            unity_instance = body.get("unity_instance")

            policy_command = command_type
            policy_params = params
            if command_type == "execute_custom_tool":
                policy_command = (
                    policy_params.get("tool_name") or policy_params.get("name") or command_type
                )
                policy_params = policy_params.get("parameters") or policy_params.get("params") or {}
                if not isinstance(policy_params, dict):
                    policy_params = {}
            started_at = asyncio.get_running_loop().time()
            budget = _CommandBudget(
                started_at + PluginHub._command_timeouts(policy_command, policy_params)[1]
            )
            scope = asyncio.timeout_at(budget.expires_at)
            token = http_request_budget.set(_HttpRequestBudget(started_at, budget, scope))
            try:
                async with scope:
                    return await _execute_cli_command(command_type, params, unity_instance)
            except TimeoutError:
                if not scope.expired():
                    raise
                return JSONResponse(bound_response(budget.timeout_response(policy_command)))
            finally:
                http_request_budget.reset(token)

        @mcp.custom_route("/api/context-command", methods=["POST"])
        async def context_command_route(request: Request) -> JSONResponse:
            """Fail closed on old/mismatched registrations; never change MCP selection."""
            started_at = asyncio.get_running_loop().time()
            try:
                return await parsed_rest_command(
                    request,
                    "/api/context-command",
                    context_command_route,
                    partial(context_command, started_at=started_at),
                )
            except ReceiptConflict:
                return JSONResponse(LocalProjectContext().refuse("project_context_invalid"))

        async def context_command(body: ContextCommand, *, started_at: float) -> JSONResponse:
            target_hash = full_hash(body.unity_instance)
            root = normalized_root(body.expected_project.root)
            context = LocalProjectContext()
            if target_hash is None or root is None:
                return JSONResponse(context.refuse("project_context_invalid"))
            policy_command = body.type
            policy_params = body.params
            if body.type == "execute_custom_tool":
                name = body.params.get("tool_name") or body.params.get("name")
                policy_command = name if isinstance(name, str) else body.type
                policy_params = body.params.get("parameters") or body.params.get("params") or {}
                if not isinstance(policy_params, dict):
                    policy_params = {}
            budget = _CommandBudget(
                started_at + PluginHub._command_timeouts(policy_command, policy_params)[1]
            )
            scope = asyncio.timeout_at(budget.expires_at)
            budget_token = http_request_budget.set(_HttpRequestBudget(started_at, budget, scope))
            context_token = local_project_context.set(context)
            try:
                async with scope:
                    selected = await PluginHub.capture_local_project_context(
                        context, target_hash, root
                    )
                    if selected is None:
                        return JSONResponse(
                            context.refuse(context.refusal or "project_context_unavailable")
                        )
                    if "@" in body.unity_instance:
                        instance_name, _ = _normalize_instance_token(body.unity_instance)
                        if not instance_name or instance_name != selected[1].project:
                            return JSONResponse(context.refuse("project_context_mismatch"))
                    result = await _execute_cli_command(
                        body.type, body.params, body.unity_instance, _selected=selected
                    )
                if context.refusal is not None:
                    evidence = budget.execution_evidence()
                    if evidence.dispatched:
                        identifiers = (
                            {"job_id": context.job_id} if context.job_id is not None else {}
                        )
                        return JSONResponse(
                            bound_response(
                                command_outcome_unknown(
                                    evidence.command or body.type,
                                    "Project-bound command could not continue after an earlier dispatch",
                                    session_id=evidence.session_id,
                                    command_id=evidence.command_id,
                                    cause=context.refusal,
                                    **identifiers,
                                )
                            )
                        )
                    return JSONResponse(context.refuse(context.refusal))
                return result
            except TimeoutError:
                if not scope.expired():
                    raise
                return JSONResponse(bound_response(budget.timeout_response(policy_command)))
            finally:
                local_project_context.reset(context_token)
                http_request_budget.reset(budget_token)

        async def _execute_cli_command(
            command_type, params, unity_instance, _selected=None
        ) -> JSONResponse:
            if _selected is not None:
                session_id, session_details = _selected
            else:
                # Get available sessions
                sessions = await PluginHub.get_sessions()
                if not sessions.sessions:
                    return JSONResponse(
                        no_unity_instances_response(),
                        status_code=503,
                    )

                session_id, session_details = _select_local_session(sessions, unity_instance)
                if not session_id:
                    return _local_target_refusal(sessions, unity_instance)

            # Custom tool execution - must be checked BEFORE the final PluginHub.send_command call
            # This applies to both cases: with or without explicit unity_instance
            if command_type == "execute_custom_tool":
                # session_id and session_details are already set above
                if not session_id or not session_details:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "No valid Unity session available for custom tool execution",
                        },
                        status_code=503,
                    )
                tool_name = None
                tool_params = {}
                if isinstance(params, dict):
                    tool_name = params.get("tool_name") or params.get("name")
                    tool_params = params.get("parameters") or params.get("params") or {}

                if not tool_name:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "Missing 'tool_name' for execute_custom_tool",
                        },
                        status_code=400,
                    )
                if tool_params is None:
                    tool_params = {}
                if not isinstance(tool_params, dict):
                    return JSONResponse(
                        {"success": False, "error": "Tool parameters must be an object/dict"},
                        status_code=400,
                    )

                # Prefer a concrete hash for project-scoped tools.
                unity_instance_hint = unity_instance
                if session_details and session_details.hash:
                    unity_instance_hint = session_details.hash

                project_id = await owned_to_thread(
                    resolve_project_id_for_unity_instance, unity_instance_hint
                )
                if not project_id:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "Could not resolve project id for custom tool",
                        },
                        status_code=400,
                    )

                service = runtime.custom_tools
                result = await service.execute_tool(
                    project_id, tool_name, unity_instance_hint, tool_params
                )
                context = local_project_context.get()
                failure = result.data if isinstance(result.data, dict) else {}
                request_budget = http_request_budget.get()
                if (
                    context is not None
                    and request_budget is not None
                    and result.success is False
                    and result.hint == "inspect_state_before_retry"
                    and (
                        failure.get("dispatched") is False
                        or failure.get("poll_dispatched") is False
                    )
                    and failure.get("reason")
                    in {
                        "custom_tool_catalog_changed",
                        "custom_tool_schema_changed",
                    }
                    and request_budget.budget.execution_evidence().dispatched
                ):
                    # Catalog admission may refuse a poll before it reaches the Hub guard.
                    context.refuse(failure["reason"], failure)
                return JSONResponse(bound_response(result.model_dump()))

            # Send command to Unity
            request_budget = http_request_budget.get()
            budget = request_budget.command_budget(
                command_type, PluginHub._command_timeouts(command_type, params)[1]
            )
            try:
                result = await PluginHub.send_command(
                    session_id, command_type, params, _budget=budget
                )
            except CommandNotDispatchedError as exc:
                return JSONResponse(
                    bound_response(
                        {
                            "success": False,
                            "error": str(exc),
                            "hint": "retry",
                            "data": {
                                "reason": "command_not_dispatched",
                                "dispatch": "not_dispatched",
                                "command": command_type,
                                "unity_instance": unity_instance,
                            },
                        }
                    ),
                    status_code=503,
                )
            return JSONResponse(bound_response(result))

        @mcp.custom_route("/api/instances", methods=["GET"])
        async def cli_instances_route(_: Request) -> JSONResponse:
            """REST endpoint to list connected Unity instances."""
            try:
                sessions = await PluginHub.get_sessions()
                instances = []
                for session_id, details in sessions.sessions.items():
                    instances.append(
                        {
                            "session_id": session_id,
                            "project": details.project,
                            "hash": details.hash,
                            "unity_version": details.unity_version,
                            "connected_at": details.connected_at,
                        }
                    )
                return JSONResponse({"success": True, "instances": instances})
            except Exception as e:
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        @mcp.custom_route("/api/custom-tools", methods=["GET"])
        async def cli_custom_tools_route(request: Request) -> JSONResponse:
            """REST endpoint to list custom tools for the active Unity project."""
            try:
                unity_instance = request.query_params.get("instance")
                sessions = await PluginHub.get_sessions()
                if not sessions.sessions:
                    return JSONResponse(
                        no_unity_instances_response(),
                        status_code=503,
                    )

                _, session_details = _select_local_session(sessions, unity_instance)
                if not session_details:
                    return _local_target_refusal(sessions, unity_instance)

                unity_instance_hint = unity_instance
                if session_details and session_details.hash:
                    unity_instance_hint = session_details.hash

                project_id = await owned_to_thread(
                    resolve_project_id_for_unity_instance, unity_instance_hint
                )
                if not project_id:
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "Could not resolve project id for custom tools",
                        },
                        status_code=400,
                    )

                service = runtime.custom_tools
                tools = await service.list_registered_tools(project_id)
                tools_payload = [
                    tool.model_dump() if hasattr(tool, "model_dump") else tool for tool in tools
                ]

                return JSONResponse(
                    {
                        "success": True,
                        "project_id": project_id,
                        "tool_count": len(tools_payload),
                        "tools": tools_payload,
                    }
                )
            except Exception as e:
                logger.error("CLI custom tools failed (%s)", type(e).__name__)
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)
