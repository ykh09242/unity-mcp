"""Optional pinned diagnostics for an explicit changed-file selection."""

from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from models.script_quality import QualityAction
from services.registry import mcp_for_unity_tool
from services.script_quality.service import ScriptQualityService


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Observe optional pinned Unity/C# diagnostics without writing source. capabilities reports explicit Editor setup/consent. "
        "analyze and capture_baseline require options.paths (1..256 Assets/Packages .cs files) XOR options.change_set_id; "
        "include_suppressed defaults false, timeout_seconds 1..300 defaults 120. They return a project-owned job_id. "
        "status requires job_id and accepts page_size 1..500 (default 100) and the returned opaque cursor. "
        "cancel requests owned job cancellation; status confirms settlement. impact requires analysis_id. "
        "Prepared change sets inspect existing original bytes; applied sets inspect verified candidate bytes. "
        "No implicit Git selection, refresh, package installation, formatting, or automatic fixes. "
        "Partial/stale/context diagnostics are distinct from a complete selected-file result."
    ),
    annotations=ToolAnnotations(
        title="Manage Script Quality", readOnlyHint=True, destructiveHint=False
    ),
)
async def manage_script_quality(
    ctx: Context,
    action: QualityAction,
    options: Annotated[
        dict[str, Any] | None, "Action-specific explicit selection, job or page parameters."
    ] = None,
) -> dict[str, Any]:
    service = getattr(ctx.fastmcp, "_script_quality_service", None)
    if service is None:
        service = ScriptQualityService()
        ctx.fastmcp._script_quality_service = service
    return await service.execute(ctx, action, options)
