"""Bounded InputAction asset authoring, separate from Play Mode input simulation."""

import json
from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from models.models import MCPResponse
from services.action_inputs import INPUT_ACTION_REQUIRED
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


AuthoringAction = Literal[
    "capabilities",
    "read",
    "create_asset",
    "edit",
    "add_map",
    "add_action",
    "add_binding",
    "set_binding",
    "remove_binding",
]
_BINDING_OPTIONS = ("groups", "interactions", "processors")
_OPTIONAL_INPUTS = {
    "read": ("page_size", "cursor", "expected_revision"),
    "edit": ("expected_revision",),
    "add_action": ("expected_control_type", "interactions", "processors"),
    "add_binding": _BINDING_OPTIONS,
    "set_binding": _BINDING_OPTIONS,
}
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
LocalSelector = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|\$[A-Za-z0-9_]{1,64})$",
    ),
]


class InputActionCommand(BaseModel):
    """One strictly bounded candidate edit with optional local result identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal[
        "create_asset", "add_map", "add_action", "add_binding", "set_binding", "remove_binding"
    ]
    ref: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_]{1,64}$")] | None = None
    map_id: LocalSelector | None = None
    action_id: LocalSelector | None = None
    binding_id: LocalSelector | None = None
    name: Name | None = None
    action_type: Literal["Button", "Value", "PassThrough"] | None = None
    expected_control_type: Text | None = None
    binding_path: Text | None = None
    groups: Text | None = None
    interactions: Text | None = None
    processors: Text | None = None

    @model_validator(mode="after")
    def command_contract(self) -> "InputActionCommand":
        values = self.model_dump(exclude_none=True, exclude={"ref"})
        for key in ("map_id", "action_id", "binding_id"):
            if key in values:
                values[key] = "00000000-0000-0000-0000-000000000001"
        values["path"] = "Assets/Commands.inputactions"
        if self.action != "create_asset":
            values["expected_revision"] = "0" * 64
        InputActionRequest.model_validate(values)
        if self.ref is not None and self.action not in {"add_map", "add_action", "add_binding"}:
            raise ValueError("Only identity-creating commands accept ref")
        return self


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
    commands: Annotated[list[InputActionCommand], Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def action_contract(self) -> "InputActionRequest":
        """Keep omission distinct from supplied empty strings in supported optional fields."""
        values = self.model_dump(exclude_none=True)
        for value in values.values():
            if isinstance(value, str) and "\0" in value:
                raise ValueError("InputAction strings cannot contain NUL")
        required = INPUT_ACTION_REQUIRED[self.action]
        allowed = {"action", *required, *_OPTIONAL_INPUTS.get(self.action, ())}
        if values.keys() - allowed or set(required) - values.keys():
            raise ValueError("InputAction request has missing or action-inapplicable fields")
        if self.action == "read" and self.cursor and self.expected_revision is None:
            raise ValueError("Paged continuation requires expected_revision")
        if self.action == "edit":
            references: dict[str, str] = {}
            assert self.commands is not None
            creating = False
            for index, command in enumerate(self.commands):
                if index == 0:
                    creating = command.action == "create_asset"
                if command.action == "create_asset" and index != 0:
                    raise ValueError("create_asset must be the first command")
                for key, kind in (
                    ("map_id", "add_map"),
                    ("action_id", "add_action"),
                    ("binding_id", "add_binding"),
                ):
                    selector = getattr(command, key)
                    if selector is not None and selector.startswith("$"):
                        if references.get(selector[1:]) != kind:
                            raise ValueError(
                                "Local selectors require an earlier identity of the same kind"
                            )
                if command.ref is not None:
                    if command.ref in references:
                        raise ValueError("Local result refs must be unique")
                    references[command.ref] = command.action
            if creating == (self.expected_revision is not None):
                raise ValueError("Existing edits require expected_revision; creation forbids it")
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
        "Author optional Input System .inputactions assets in idle Edit Mode. capabilities/read "
        "are bounded reads; read returns stored GUIDs and a raw source revision. Legacy binding rows can lack IDs. "
        "create_asset requires an unoccupied Assets path and existing parent. add_map/add_action/"
        "add_binding/set_binding/remove_binding require expected_revision; exact GUID selectors avoid "
        "name ambiguity. edit accepts 1..64 ordered commands, validates the final candidate and imports once. "
        "Use ref on creation commands and $ref selectors for subsequent commands. Start with create_asset "
        "for a new file; existing edits require expected_revision. Commands share the 8 KiB request limit. "
        "Simple bindings only; existing composite/control-scheme data and PlayerInput "
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
    commands: Annotated[list[InputActionCommand], Field(min_length=1, max_length=64)] | None = None,
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
            commands=commands,
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
