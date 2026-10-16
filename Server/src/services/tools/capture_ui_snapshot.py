"""Read-only bounded semantic UI capture; persistence belongs to the explicit CLI output."""

import re
from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    ValidationError,
    model_validator,
)

from models.models import MCPResponse
from models.ui_snapshots import validate_ui_snapshot
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class UiIdentityAssignment(BaseModel):
    """One current-path association, supplied by the caller rather than inferred."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)
    element_path: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            description="Current relative child-index path, e.g. /0/1. uGUI uses Transform order; Toolkit uses snapshot content-container order. Root is not assignable.",
        ),
    ]
    id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            description="Case-sensitive nonsecret item ID using ASCII letters, digits, period, underscore, colon or hyphen. Caller must update the current-path association when rows rebind.",
        ),
    ]

    @model_validator(mode="before")
    @classmethod
    def closed_assignment(cls, value):
        # Reject unknown names before Pydantic records them in an error location.
        if not isinstance(value, dict) or set(value) != {"element_path", "id"}:
            raise ValueError("identity_map assignment fields are invalid")
        return value

    @model_validator(mode="after")
    def canonical_values(self):
        if not re.fullmatch(
            r"/(?:0|[1-9][0-9]{0,2})(?:/(?:0|[1-9][0-9]{0,2})){0,126}", self.element_path
        ) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.id):
            raise ValueError("identity_map paths or IDs are invalid")
        return self


class UiSnapshotRequest(BaseModel):
    """Reject coercion and unbounded selectors before contacting Unity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)
    framework: Literal["ugui", "uitoolkit"]
    target: Annotated[str, Field(min_length=1, max_length=4096)] | StrictInt
    max_nodes: Annotated[int, Field(ge=1, le=1000)] = 200
    identity_map: Annotated[list[UiIdentityAssignment], Field(min_length=1, max_length=999)] = None

    @model_validator(mode="after")
    def bounded_unique_map(self):
        if self.identity_map is not None:
            entries = self.identity_map
            if (
                len(entries) > self.max_nodes - 1
                or sum(len(e.element_path) + len(e.id) for e in entries) > 65536
                or len({e.element_path for e in entries}) != len(entries)
                or len({e.id for e in entries}) != len(entries)
            ):
                raise ValueError("identity_map duplicates or aggregate bounds are invalid")
        return self


@mcp_for_unity_tool(
    group="ui",
    description=(
        "Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit. "
        "Returns stable hierarchy identities, text, active/render eligibility/interactable state, "
        "current bounds and renderer-cull evidence where available. Does not write files or change "
        "resolution/layout. Ambiguous identities, overflow and unavailable live UI trees fail. "
        "Actual occlusion, pixel visibility and event delivery are not proven."
        " Default captures use schema version 2 with fixed typed control states. Recognized editable "
        "input contents and owned text renderers are omitted before text access; ordinary labels "
        "remain. Native inventory allocation/time is separate from managed processing bounds. "
        "Use matching updated plugin/server versions; legacy v1 is supported only for offline comparison. "
        "Optional identity_map associates current child paths with nonsecret caller item IDs in either framework, "
        "emitting schema version 3 comparison identities. Update associations after row rebinding/reorder; "
        "valid stale associations cannot be detected. Mapped IDs are not mutation handles. "
        "Omission preserves schema version 2 and semantic ambiguity refusal."
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
    identity_map: Annotated[
        list[UiIdentityAssignment],
        Field(
            min_length=1,
            max_length=999,
            description="Optional current-path to nonsecret item-ID map. At most max_nodes minus one entries and 65536 aggregate path/ID characters; duplicate paths/IDs refuse. Omit for semantic v2 capture; explicit null/empty refuse. Mapped v3 identities remain parent-scoped and require caller rebinding; they are not mutation handles.",
        ),
    ] = None,
) -> dict[str, JsonValue]:  # noqa: DICT_OK - MCP transport envelope
    """Validate the capture boundary, then dispatch one selected Editor read."""
    try:
        arguments = {"framework": framework, "target": target, "max_nodes": max_nodes}
        if identity_map is not None:
            arguments["identity_map"] = identity_map
        request = UiSnapshotRequest(**arguments)
        if type(target) is str:
            target.encode("utf-8")
            if not target.strip():
                raise ValueError("Target must contain a hierarchy selector")
    except (ValidationError, ValueError, UnicodeError) as exc:
        if identity_map is not None:
            return {
                "success": False,
                "error": "identity_map or capture parameters are invalid or exceed supported bounds.",
            }
        return {"success": False, "error": str(exc)[:2048]}
    blocked = await preflight(ctx, wait_for_no_compile=True)
    if blocked is not None:
        return blocked.model_dump()
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "capture_ui_snapshot",
        request.model_dump(mode="json", exclude_none=True),
    )
    if isinstance(response, MCPResponse):
        response = response.model_dump(exclude_none=True)
    if not isinstance(response, dict) or response.get("success") is not True:
        return {
            "success": False,
            "error": "UI snapshot capture failed; native evidence is unavailable.",
        }
    if isinstance(response, dict) and response.get("success") is True:
        try:
            validate_ui_snapshot(
                response.get("data"), expected_version=3 if identity_map is not None else 2
            )
        except ValueError:
            return {
                "success": False,
                "error": "UI snapshot schema/privacy/identity validation failed for the requested capture mode.",
            }
    return response
