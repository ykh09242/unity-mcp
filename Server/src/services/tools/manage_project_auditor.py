"""Optional invocation-owned Project Auditor analysis and bounded diagnostic reads."""

import re
from typing import Annotated, Any, Final, Literal
from uuid import uuid4

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from models import MCPResponse

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

AuditAction = Literal["availability", "start", "status", "issues"]
AUDIT_ID_PATTERN: Final = re.compile(r"[a-f0-9]{32}")


@mcp_for_unity_tool(
    group="profiling",
    maturity="experimental",
    description=(
        "Optional Project Auditor 1.0.1 integration. availability checks the installed package, "
        "API and bundled rules without starting analysis. start requests one Editor-session audit "
        "and returns audit_id; reuse that ID after an uncertain response. status observes its "
        "queued/running/completed/failed/unavailable/interrupted state. issues pages captured "
        "diagnostics after completion (page_size 1..100, opaque cursor). Analysis uses the current "
        "build target and may compile Player assemblies and save Auditor settings. Missing rules "
        "and failed modules are distinct from completed zero diagnostics. The package is never "
        "installed automatically. No cancellation or arbitrary report file writes are supported."
    ),
    annotations=ToolAnnotations(
        title="Manage Project Auditor", readOnlyHint=False, destructiveHint=True
    ),
)
async def manage_project_auditor(
    ctx: Context,
    action: Annotated[AuditAction, "availability, start, status, or issues."],
    audit_id: Annotated[
        str | None,
        Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$"),
        "Editor-session audit identity. Omit on start to allocate once; required for status/issues.",
    ] = None,
    page_size: Annotated[
        int | None, Field(strict=True, ge=1, le=100), "issues only: row limit (default 50)."
    ] = None,
    cursor: Annotated[
        str | None,
        Field(max_length=128),
        "issues only: opaque cursor returned by the previous page.",
    ] = None,
    wait_timeout: Annotated[
        int, Field(strict=True, ge=0, le=60), "Optional bounded completion observation, in seconds."
    ] = 0,
) -> dict[str, Any]:
    """Validate the public request once and dispatch without implicitly repeating analysis."""
    from services.job_completion import prepare_wait, complete_job

    observation_target = await prepare_wait(ctx, wait_timeout)
    if action not in ("availability", "start", "status", "issues"):
        return {"success": False, "error": "Unsupported Project Auditor action."}
    if audit_id is not None and (
        not isinstance(audit_id, str) or AUDIT_ID_PATTERN.fullmatch(audit_id) is None
    ):
        return {"success": False, "error": "audit_id must be 32 lower-case hexadecimal characters."}
    if action in ("status", "issues") and audit_id is None:
        return {"success": False, "error": "audit_id is required for status and issues."}
    if action == "availability" and audit_id is not None:
        return {"success": False, "error": "audit_id is not used by availability."}
    if action != "issues" and (page_size is not None or cursor is not None):
        return {"success": False, "error": "page_size and cursor are only valid for issues."}
    if page_size is not None and (type(page_size) is not int or not 1 <= page_size <= 100):
        return {"success": False, "error": "page_size must be an integer from 1 to 100."}
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 128):
        return {
            "success": False,
            "error": "cursor must be a nonempty string of at most 128 characters.",
        }

    params: dict[str, Any] = {"action": action}
    if action == "start":
        audit_id = audit_id or uuid4().hex
    if audit_id is not None:
        params["audit_id"] = audit_id
    if page_size is not None:
        params["page_size"] = page_size
    if cursor is not None:
        params["cursor"] = cursor
    instance = await get_unity_instance_from_context(ctx)
    try:
        response = await send_with_unity_instance(
            async_send_command_with_retry, instance, "manage_project_auditor", params
        )
    except (TimeoutError, ConnectionError):
        return {
            "success": False,
            "code": "project_auditor_transport_unknown",
            "error": "The native audit outcome is unknown after transport interruption.",
            "audit_id": audit_id,
            "observation_hint": "Query status with this audit_id before requesting another audit.",
        }
    response = (
        response.model_dump(exclude_none=True) if isinstance(response, MCPResponse) else response
    )
    if action == "start" and isinstance(response, dict) and not response.get("success"):
        response = {
            **response,
            "audit_id": audit_id,
            "observation_hint": "Query status with this audit_id before requesting another audit.",
        }
    return await complete_job(ctx, observation_target, "auditor", response, wait_timeout)
