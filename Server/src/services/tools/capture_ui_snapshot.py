"""Read-only bounded semantic UI capture; persistence belongs to the explicit CLI output."""

from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, ValidationError

from models.models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class UiSnapshotRequest(BaseModel):
    """Reject coercion and unbounded selectors before contacting Unity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    framework: Literal["ugui", "uitoolkit"]
    target: Annotated[str, Field(min_length=1, max_length=4096)] | StrictInt
    max_nodes: Annotated[int, Field(ge=1, le=1000)] = 200


@mcp_for_unity_tool(
    group="ui",
    description=(
        "Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit. "
        "Returns stable hierarchy identities, text, active/render eligibility/interactable state, "
        "current bounds and renderer-cull evidence where available. Does not write files or change "
        "resolution/layout. Ambiguous identities, overflow and unavailable live UI trees fail. "
        "Actual occlusion, pixel visibility and event delivery are not proven."
    ),
    annotations=ToolAnnotations(
        title="Capture UI Snapshot", readOnlyHint=True, destructiveHint=False
    ),
)
async def capture_ui_snapshot(
    ctx: Context,
    framework: Literal["ugui", "uitoolkit"],
    target: str | StrictInt,
    max_nodes: Annotated[int, Field(strict=True, ge=1, le=1000)] = 200,
) -> dict[str, JsonValue]:  # noqa: DICT_OK - MCP transport envelope
    """Validate the capture boundary, then dispatch one selected Editor read."""
    try:
        request = UiSnapshotRequest(framework=framework, target=target, max_nodes=max_nodes)
        if type(target) is str:
            target.encode("utf-8")
            if not target.strip():
                raise ValueError("Target must contain a hierarchy selector")
    except (ValidationError, ValueError, UnicodeError) as exc:
        return {"success": False, "error": str(exc)[:2048]}
    blocked = await preflight(ctx, wait_for_no_compile=True)
    if blocked is not None:
        return blocked.model_dump()
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "capture_ui_snapshot",
        request.model_dump(mode="json"),
    )
    if isinstance(response, MCPResponse):
        return response.model_dump(exclude_none=True)
    return response
