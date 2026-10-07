"""Bounded in-editor Play Mode input simulation; never sends OS input."""

from typing import Annotated, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator
from typing_extensions import TypedDict

from models.models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

InputAction = Literal["status", "ui_click", "key", "mouse", "touch", "release_all"]
InputState = Literal["press", "release", "move"]
InputButton = Literal["left", "right", "middle"]
FrameCount = Annotated[int, Field(strict=True, ge=1, le=600)]
TouchId = Annotated[int, Field(strict=True, ge=1, le=10)]
InstanceId = Annotated[int, Field(strict=True, ge=-(2**31), le=2**31 - 1)]
HierarchyPath = Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
ScreenCoordinate = Annotated[float, Field(strict=True, ge=0, le=1_000_000, allow_inf_nan=False)]


class InputResponse(TypedDict, total=False):
    """Unity transport response, including structured capability failures."""

    success: bool
    message: str
    error: str
    code: str
    hint: str
    data: JsonValue


class InputCommand(BaseModel):
    """Parse action-specific arguments before a command reaches Unity."""

    model_config = ConfigDict(frozen=True)
    action: InputAction
    target: InstanceId | HierarchyPath | None = None
    key: Annotated[str, Field(strict=True, min_length=1, max_length=64)] | None = None
    state: InputState = "press"
    frames: FrameCount = 1
    position: tuple[ScreenCoordinate, ScreenCoordinate] | None = None
    button: InputButton = "left"
    touch_id: TouchId = 1

    @model_validator(mode="after")
    def check_action_arguments(self) -> "InputCommand":
        """Reject commands whose supplied state cannot be meaningfully applied."""
        match self.action:
            case "ui_click":
                if self.target is None:
                    raise ValueError(
                        "ui_click requires an integer scene instance ID or exact root hierarchy path"
                    )
            case "key":
                if self.key is None or not self.key.strip() or self.state == "move":
                    raise ValueError("key requires a Key enum name and state press or release")
            case "mouse":
                if self.state == "move" and self.position is None:
                    raise ValueError("mouse move requires position")
            case "touch":
                if self.state != "release" and self.position is None:
                    raise ValueError("touch press/move requires position")
            case "status" | "release_all":
                pass
            case unreachable:
                assert_never(unreachable)
        return self


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. "
        "ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path "
        "(independent of raw input backend; does not test raycast occlusion). "
        "key/mouse/touch require com.unity.inputsystem 1.7+ and Active Input Handling Input System or Both. "
        "press holds for 1..600 game input updates then releases; use release for an earlier release. "
        "touch_id 1..10 permits concurrent contacts across calls; move preserves the original release deadline. "
        "release_all removes only MCP virtual devices. A hard 30-second lease and play exit/reload cleanup "
        "prevent stuck input. Paused Play Mode accepts release_all only. Device-paired PlayerInput actions "
        "may require pairing the virtual devices. Legacy UnityEngine.Input and OS input are unsupported."
    ),
    annotations=ToolAnnotations(title="Manage Input", readOnlyHint=False, destructiveHint=True),
)
async def manage_input(
    ctx: Context,
    action: Annotated[InputAction, "status, ui_click, key, mouse, touch or release_all"],
    target: Annotated[
        InstanceId | HierarchyPath | None, "ui_click scene instance ID or exact root hierarchy path"
    ] = None,
    key: Annotated[
        str | None, "Input System Key enum name, for example A, Space or LeftShift"
    ] = None,
    state: Annotated[InputState, "press or release; mouse/touch also support move"] = "press",
    frames: Annotated[
        FrameCount, "Hold for this many game Input System updates (dynamic or fixed)"
    ] = 1,
    position: Annotated[
        tuple[ScreenCoordinate, ScreenCoordinate] | None,
        "Screen pixel x,y; required for mouse move and touch press/move",
    ] = None,
    button: Annotated[InputButton, "Mouse button"] = "left",
    touch_id: Annotated[TouchId, "Independent touch contact ID (1..10)"] = 1,
) -> InputResponse:
    """Validate bounded input parameters and forward using the selected Unity instance."""
    try:
        command = InputCommand(
            action=action,
            target=target,
            key=key,
            state=state,
            frames=frames,
            position=position,
            button=button,
            touch_id=touch_id,
        )
    except ValidationError as exc:
        return {"success": False, "error": f"Invalid input simulation parameters: {exc}"}
    unity_instance = await get_unity_instance_from_context(ctx)
    # Input events are effects; a reload retry must not replay a click or press.
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_input",
        command.model_dump(mode="json", exclude_none=True),
        retry_on_reload=False,
    )
    # The legacy bridge returns a model for connection/preflight failures.
    if isinstance(response, MCPResponse):
        return response.model_dump(exclude_none=True)
    return response
