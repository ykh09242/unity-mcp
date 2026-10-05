"""MCP boundary for Canvas, RectTransform and optional uGUI components."""

from typing import Annotated, Final, Literal, get_args

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    TypeAdapter,
    ValidationError,
)
from typing_extensions import assert_never

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from services.tools.refresh_unity import send_mutation
from services.tools.utils import normalize_properties
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

UGUIAction = Literal[
    "ping",
    "get_hierarchy",
    "create",
    "set_rect",
    "set_layout",
    "set_text",
    "set_canvas",
    "diagnose",
]
ElementType = Literal["canvas", "panel", "image", "button", "text"]
_ACTIONS: Final = get_args(UGUIAction)
_MUTATIONS: Final = frozenset({"create", "set_rect", "set_layout", "set_text", "set_canvas"})
ResolutionDimension = Annotated[int, Field(strict=True, ge=64, le=8192)]


class Resolution(BaseModel):
    """A bounded screen size for isolated diagnostic evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    width: ResolutionDimension
    height: ResolutionDimension


_RESOLUTIONS: Final = TypeAdapter(
    Annotated[list[Resolution], Field(min_length=1, max_length=8)]
)
_PROPERTIES: Final = TypeAdapter(
    dict[str, JsonValue], config=ConfigDict(allow_inf_nan=False)
)


def _validate_unicode_strings(value: JsonValue) -> None:
    # JsonValue validation has already bounded nesting and excluded reference cycles.
    match value:
        case str():
            value.encode("utf-8")
        case dict():
            for key, item in value.items():
                key.encode("utf-8")
                _validate_unicode_strings(item)
        case list():
            for item in value:
                _validate_unicode_strings(item)
        case None | bool() | int() | float():
            pass
        case _:
            assert_never(value)


@mcp_for_unity_tool(
    group="ui",
    description=(
        "Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings. "
        "Read-only actions: ping, get_hierarchy, diagnose. Mutating actions: create, set_rect, set_layout, "
        "set_text, set_canvas. Optional uGUI and TextMeshPro support is detected in the Editor. "
        "Diagnostics evaluate bounded screen-size candidates and report limitations; they do not render screenshots."
    ),
    annotations=ToolAnnotations(
        title="Manage uGUI",
        read_only_hint=False,
        destructive_hint=True,
    ),
)
async def manage_ugui(
    ctx: Context,
    action: Annotated[UGUIAction, "Action to perform."],
    target: Annotated[
        str | StrictInt | None,
        "GameObject name, hierarchy path, or instance ID. Required for get_hierarchy, setters and diagnose.",
    ] = None,
    parent: Annotated[
        str | StrictInt | None,
        "Parent GameObject for create, resolved by name, hierarchy path, or instance ID.",
    ] = None,
    name: Annotated[str | None, "Name of the created GameObject."] = None,
    element_type: Annotated[
        ElementType | None,
        "Element to create: canvas, panel, image, button, or text.",
    ] = None,
    properties: Annotated[
        dict[str, JsonValue] | str | None,
        "Action-specific camelCase properties, as a dict or JSON object string. Rect: anchorMin, anchorMax, "
        "pivot, anchoredPosition, sizeDelta, offsetMin, offsetMax. Layout: type plus component properties. "
        "Text: text, fontSize, color, alignment, raycastTarget. Canvas: Canvas and CanvasScaler properties.",
    ] = None,
    include_inactive: Annotated[
        bool,
        Field(
            strict=True,
            description="Include inactive GameObjects in hierarchy and diagnostics.",
        ),
    ] = False,
    max_nodes: Annotated[
        int,
        Field(
            strict=True,
            ge=1,
            le=1000,
            description="Maximum hierarchy nodes to inspect, 1 to 1000.",
        ),
    ] = 200,
    resolutions: Annotated[
        list[dict[str, ResolutionDimension]] | str | None,
        "For diagnose: 1 to 8 {width,height} objects, or a JSON array string. Integer dimensions 64 to 8192. "
        "Omit to use Editor diagnostic defaults.",
    ] = None,
) -> dict[str, JsonValue]:  # noqa: DICT_OK - existing MCP/Unity JSON response contract
    """Validate a request and dispatch it through the selected Unity transport.

    The explicit parameters define the public MCP schema, which needs separate
    action-specific inputs rather than an opaque options object.
    """
    action_lower = action.strip().lower()
    if action_lower not in _ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(_ACTIONS)}.",
        }
    if type(max_nodes) is not int or not 1 <= max_nodes <= 1000:
        return {"success": False, "message": "max_nodes must be an integer from 1 to 1000."}
    if type(include_inactive) is not bool:
        return {"success": False, "message": "include_inactive must be a boolean."}
    for key, value in (("target", target), ("parent", parent)):
        if value is not None and (
            type(value) not in (str, int)
            or (type(value) is str and not value.strip())
        ):
            return {
                "success": False,
                "message": f"{key} must be a non-empty GameObject name/path or integer instance ID.",
            }
    if (
        action_lower.startswith("set_")
        or action_lower in ("get_hierarchy", "diagnose")
    ) and target is None:
        return {"success": False, "message": f"target is required for {action_lower}."}
    if action_lower == "create" and element_type not in get_args(ElementType):
        return {
            "success": False,
            "message": "element_type is required for create: canvas, panel, image, button, or text.",
        }
    if action_lower == "create" and element_type != "canvas" and parent is None:
        return {
            "success": False,
            "message": "parent is required for a non-canvas element; use a RectTransform beneath a Canvas.",
        }
    if name is not None and (
        not name.strip()
        or any(character in name for character in "/\\\x00\r\n")
    ):
        return {
            "success": False,
            "message": "name must be a non-empty single hierarchy name without path separators or control characters.",
        }

    try:
        properties, properties_error = normalize_properties(properties)
        if properties is not None:
            properties = _PROPERTIES.validate_python(properties)
            _validate_unicode_strings(properties)
        for value in (target, parent, name):
            if isinstance(value, str):
                value.encode("utf-8")
    except UnicodeEncodeError:
        return {
            "success": False,
            "message": "target, parent, name and properties keys/string values must contain valid Unicode; remove unpaired surrogate escapes or provide a complete character.",
        }
    except (RecursionError, ValidationError):
        return {
            "success": False,
            "message": "properties must contain supported JSON values with finite numbers and bounded nesting; check numbers and reduce the property structure.",
        }
    if properties_error:
        return {"success": False, "message": properties_error}
    if action_lower.startswith("set_") and not properties:
        return {
            "success": False,
            "message": f"properties is required for {action_lower}; supply a non-empty JSON object.",
        }

    params: dict[str, JsonValue] = {
        "action": action_lower,
        "include_inactive": include_inactive,
        "max_nodes": max_nodes,
    }
    if resolutions is not None:
        try:
            parsed = (
                _RESOLUTIONS.validate_json(resolutions)
                if type(resolutions) is str
                else _RESOLUTIONS.validate_python(resolutions)
            )
        except ValidationError:
            return {
                "success": False,
                "message": "Invalid resolutions: supply 1 to 8 objects containing only width and height, each an integer from 64 to 8192.",
            }
        params["resolutions"] = [size.model_dump() for size in parsed]
    for key, value in (
        ("target", target),
        ("parent", parent),
        ("name", name),
        ("element_type", element_type),
        ("properties", properties),
    ):
        if value is not None:
            params[key] = value

    is_mutation = action_lower in _MUTATIONS
    if action_lower != "ping":
        blocked = await preflight(ctx, requires_no_tests=is_mutation, wait_for_no_compile=True)
        if blocked is not None:
            return blocked.model_dump()
    unity_instance = await get_unity_instance_from_context(ctx)
    result = (
        await send_mutation(ctx, unity_instance, "manage_ugui", params)
        if is_mutation
        else await send_with_unity_instance(
            async_send_command_with_retry,
            unity_instance,
            "manage_ugui",
            params,
        )
    )
    result = result.model_dump() if isinstance(result, MCPResponse) else result
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
