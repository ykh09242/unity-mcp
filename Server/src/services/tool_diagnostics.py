"""One bounded read of cached discovery and authenticated publication evidence."""

import re
import json
from hashlib import sha256

import anyio
from fastmcp import Context
from pydantic import ValidationError

from core.config import config
from models.tool_diagnostics import (
    NativeToolDiagnostics,
    ToolDiagnostics,
    ToolDiagnosticsResponse,
)
from services.capabilities import capability_read_scope, get_connection_evidence
from services.custom_tool_service import CustomToolService
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.plugin_hub import PluginHub
from transport.unity_transport import send_with_unity_instance


def valid_diagnostic_name(name: str | None) -> bool:
    """Names are one bounded URI component, never a path or an expression."""
    return name is None or bool(len(name) <= 128 and re.fullmatch(r"[\w.\-]+", name))


def _named(attempt, name):
    if attempt is None or name is None:
        return attempt
    return attempt.model_copy(
        update={"rows": [row for row in attempt.rows if row.tool_name == name][:16]}
    )


async def _server_attempts(connection, user_id, name):
    if connection.transport == "http":
        return await PluginHub.get_tool_diagnostic_attempts(connection.instance_id, user_id, name)
    from transport.legacy import unity_connection as legacy

    service = CustomToolService._instance
    pool = legacy._unity_connection_pool
    connection_object = pool._connections.get(connection.instance_id) if pool else None
    if service is None or connection_object is None:
        return []
    generation = connection_object.session_generation
    if not generation or sha256(generation.encode()).hexdigest() != connection.generation:
        return []
    project = connection_object.instance_id.rsplit("@", 1)[-1]

    def capture():
        result = [
            _named(service.get_diagnostic_attempts("stdio", project, generation=generation), name)
        ]
        alias = service.get_project_id_for_hash(project) or project
        result.append(_named(service.get_diagnostic_attempts("manual", alias), name))
        return [value for value in result if value is not None]

    try:
        return legacy._commit_stdio_catalog(connection_object, generation, capture)
    except ValueError:
        return []


async def query_tool_diagnostics(
    ctx: Context, tool_name: str | None = None
) -> ToolDiagnosticsResponse:
    """Discard all project observations unless the same owner survives bounded cleanup."""
    if not valid_diagnostic_name(tool_name):
        raise ValueError("Invalid diagnostic tool name")
    with capability_read_scope() as deadline:
        from services.capabilities import _query_timeout

        timeout = _query_timeout()
        grace = (
            min(1.0, float(PluginHub.CANCEL_TIMEOUT)) if config.transport_mode == "http" else 0.0
        )
        user = instance = connection = None
        native = NativeToolDiagnostics()
        server = []
        verified = False
        with anyio.move_on_at(deadline + grace):
            with anyio.move_on_at(deadline):
                user = await ctx.get_state("user_id") if config.http_remote_hosted else None
                instance = await get_unity_instance_from_context(ctx)
                connection = await get_connection_evidence(instance, user)
                if connection.status == "connected":
                    server = await _server_attempts(connection, user, tool_name)
                    if connection.admission_available is not False:
                        remaining = max(0.0, deadline - anyio.current_time())
                        probe_deadline = deadline - min(0.5, remaining * 0.5)
                        with anyio.move_on_at(probe_deadline):
                            try:
                                result = await send_with_unity_instance(
                                    async_send_command_with_retry,
                                    connection.instance_id,
                                    "get_tool_states",
                                    {
                                        "diagnostics": True,
                                        "tool_name": tool_name,
                                        "limit": 16 if tool_name else 64,
                                    },
                                    user_id=user,
                                    retry_on_reload=False,
                                    **(
                                        {"_deadline": probe_deadline}
                                        if connection.transport == "stdio"
                                        else {}
                                    ),
                                )
                                data = (
                                    result.get("data")
                                    if isinstance(result, dict) and result.get("success") is True
                                    else None
                                )
                                if (
                                    isinstance(data, dict)
                                    and data.get("schema_version")
                                    == "unity-mcp/native-tool-diagnostics@1"
                                    and len(
                                        json.dumps(
                                            data, ensure_ascii=False, allow_nan=False
                                        ).encode("utf-8")
                                    )
                                    <= 128 * 1024
                                ):
                                    native = NativeToolDiagnostics.model_validate(data)
                            except (ValidationError, ValueError, OSError, RuntimeError):
                                native = NativeToolDiagnostics()
            if connection is not None and connection.status == "connected":
                current = await get_connection_evidence(instance, user)
                current_user = await ctx.get_state("user_id") if config.http_remote_hosted else None
                verified = current_user == user and (
                    current.status,
                    current.instance_id,
                    current.project_hash,
                    current.generation,
                ) == (
                    "connected",
                    connection.instance_id,
                    connection.project_hash,
                    connection.generation,
                )
                if verified:
                    connection = current
        if connection is None:
            from models.capabilities import CapabilityConnection

            connection = CapabilityConnection(
                transport="http" if config.transport_mode == "http" else "stdio", status="unknown"
            )
        if not verified:
            connection = connection.model_copy(
                update={
                    "status": "unknown",
                    "instance_id": None,
                    "project_hash": None,
                    "generation": None,
                    "negotiated": None,
                    "admission_available": None,
                    "pending_commands": None,
                    "tenant_pending_commands": None,
                    "reason": "connection_changed_or_deadline",
                    "next_action": "retry_later",
                }
            )
            native, server = NativeToolDiagnostics(), []
        # A bounded round-robin preview preserves evidence from each source while
        # charging the advertised row cap once for the entire response.
        sources = [native, *server]
        counts = [0] * len(sources)
        remaining = 16 if tool_name else 64
        for _ in range(remaining):
            progressed = False
            for index, source in enumerate(sources):
                if remaining and counts[index] < len(source.rows):
                    counts[index] += 1
                    remaining -= 1
                    progressed = True
            if not remaining or not progressed:
                break
        bounded = [
            source.model_copy(
                update={
                    "rows": source.rows[:count],
                    "truncated": source.truncated or count < len(source.rows),
                }
            )
            for source, count in zip(sources, counts)
        ]
        native, server = bounded[0], bounded[1:]
        return ToolDiagnosticsResponse(
            data=ToolDiagnostics(
                connection=connection,
                native=native,
                server=server,
                partial=not verified
                or not native.complete
                or native.truncated
                or not server
                or any(attempt.truncated for attempt in server),
                reason="none" if verified else "connection_changed",
                query_read_seconds=timeout,
                query_cleanup_grace_seconds=grace,
                query_max_elapsed_seconds=timeout + grace,
            )
        )
