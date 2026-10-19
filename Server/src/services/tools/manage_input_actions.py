"""Bounded InputAction asset authoring, separate from Play Mode input simulation."""

import json
from typing import Annotated, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from models.models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


AuthoringAction = Literal[
    "capabilities",
    "read",
    "create_asset",
    "add_map",
    "add_action",
    "add_binding",
    "set_binding",
    "remove_binding",
]
GuidText = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    ),
]
Revision = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(strict=True, max_length=512)]
Name = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
AssetPath = Annotated[str, Field(strict=True, min_length=1, max_length=2048)]
PageSize = Annotated[int, Field(strict=True, ge=1, le=100)]
Cursor = Annotated[int, Field(strict=True, ge=0, le=2048)]


class InputActionRequest(BaseModel):
    """Reject stale-selector shapes and action-inapplicable fields before transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: AuthoringAction
    path: AssetPath | None = None
    expected_revision: Revision | None = None
    map_id: GuidText | None = None
    action_id: GuidText | None = None
    binding_id: GuidText | None = None
    name: Name | None = None
    action_type: Literal["Button", "Value", "PassThrough"] | None = None
    expected_control_type: Text | None = None
    binding_path: Text | None = None
    groups: Text | None = None
    interactions: Text | None = None
    processors: Text | None = None
    page_size: PageSize | None = None
    cursor: Cursor | None = None

    @model_validator(mode="after")
    def action_contract(self) -> "InputActionRequest":
        """Keep omission distinct from supplied empty strings in supported optional fields."""
        values = self.model_dump(exclude_none=True)
        for value in values.values():
            if isinstance(value, str) and "\0" in value:
                raise ValueError("InputAction strings cannot contain NUL")
        common = {"action", "path", "expected_revision"}
        match self.action:
            case "capabilities":
                allowed, required = {"action"}, set()
            case "read":
                allowed, required = common | {"page_size", "cursor"}, {"path"}
                if self.cursor and self.expected_revision is None:
                    raise ValueError("Paged continuation requires expected_revision")
            case "create_asset":
                allowed, required = {"action", "path"}, {"path"}
            case "add_map":
                allowed = common | {"name"}
                required = {"path", "expected_revision", "name"}
            case "add_action":
                allowed = common | {
                    "map_id",
                    "name",
                    "action_type",
                    "expected_control_type",
                    "interactions",
                    "processors",
                }
                required = {"path", "expected_revision", "map_id", "name", "action_type"}
            case "add_binding":
                allowed = common | {
                    "map_id",
                    "action_id",
                    "binding_path",
                    "groups",
                    "interactions",
                    "processors",
                }
                required = {"path", "expected_revision", "map_id", "action_id", "binding_path"}
            case "set_binding":
                allowed = common | {
                    "map_id",
                    "binding_id",
                    "binding_path",
                    "groups",
                    "interactions",
                    "processors",
                }
                required = {"path", "expected_revision", "map_id", "binding_id", "binding_path"}
            case "remove_binding":
                allowed = common | {"map_id", "binding_id"}
                required = {"path", "expected_revision", "map_id", "binding_id"}
            case unreachable:
                assert_never(unreachable)
        if set(values) - allowed or required - set(values):
            raise ValueError("InputAction request has missing or action-inapplicable fields")
        if self.path is not None:
            if (
                not self.path.startswith("Assets/")
                or "\\" in self.path
                or not self.path.lower().endswith(".inputactions")
                or any(part in {"", ".", ".."} for part in self.path.split("/"))
            ):
                raise ValueError("path must be a canonical Assets .inputactions source path")
        if self.name is not None and (not self.name.strip() or self.name.find("/") >= 0):
            raise ValueError("Map/action names must be nonempty and cannot contain '/'")
        if self.binding_path is not None and not self.binding_path.strip():
            raise ValueError("binding_path must be nonempty")
        if len(json.dumps(values, ensure_ascii=False).encode("utf-8")) > 8192:
            raise ValueError("InputAction request exceeds 8 KiB")
        return self


@mcp_for_unity_tool(
    group="core",
    description=(
        "Author optional Input System1.7+ .inputactions assets in idle Edit Mode. capabilities/read "
        "are bounded reads; read returns exact map/action/binding GUIDs and a raw source revision. "
        "create_asset requires an unoccupied Assets path and existing parent. add_map/add_action/"
        "add_binding/set_binding/remove_binding require expected_revision; exact GUID selectors avoid "
        "name ambiguity. Simple bindings only; existing composite/control-scheme data and PlayerInput "
        "asset linkage are preserved. Edits save/import immediately; configured importer wrapper "
        "generation may compile scripts. Conditional file-backed Undo/Redo is limited to32 edits in "
        "the current Editor domain, refuses external drift and does not survive domain reload. "
        "Asset creation is not Undoable. Does not simulate input, pair devices, enable actions or install packages."
    ),
    annotations=ToolAnnotations(title="Manage Input Actions", destructiveHint=True),
)
async def manage_input_actions(
    ctx: Context,
    action: AuthoringAction,
    path: AssetPath | None = None,
    expected_revision: Revision | None = None,
    map_id: GuidText | None = None,
    action_id: GuidText | None = None,
    binding_id: GuidText | None = None,
    name: Name | None = None,
    action_type: Literal["Button", "Value", "PassThrough"] | None = None,
    expected_control_type: Text | None = None,
    binding_path: Text | None = None,
    groups: Text | None = None,
    interactions: Text | None = None,
    processors: Text | None = None,
    page_size: PageSize | None = None,
    cursor: Cursor | None = None,
) -> dict[str, JsonValue]:
    """Parse strict action-specific authoring values and preserve the selected Editor route."""
    try:
        request = InputActionRequest(
            action=action,
            path=path,
            expected_revision=expected_revision,
            map_id=map_id,
            action_id=action_id,
            binding_id=binding_id,
            name=name,
            action_type=action_type,
            expected_control_type=expected_control_type,
            binding_path=binding_path,
            groups=groups,
            interactions=interactions,
            processors=processors,
            page_size=page_size,
            cursor=cursor,
        )
    except ValidationError as exc:
        return {
            "success": False,
            "code": "invalid_input_action_request",
            "error": str(exc.errors(include_url=False, include_input=False)),
        }
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_input_actions",
        request.model_dump(mode="json", exclude_none=True),
        retry_on_reload=False,
    )
    return response.model_dump(exclude_none=True) if isinstance(response, MCPResponse) else response
