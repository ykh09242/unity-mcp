"""MCP boundary for Canvas, RectTransform and optional uGUI components."""

import math
import struct
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

# Only request-level contracts shared by all supported Unity component versions.
# Component availability and reflected enum names remain Editor validations.
_RECT_KEYS: Final = frozenset("anchorMin anchorMax pivot anchoredPosition sizeDelta offsetMin offsetMax localScale localEulerAngles".split())
_TEXT_KEYS: Final = frozenset("text fontSize color alignment enableAutoSizing fontSizeMin fontSizeMax raycastTarget".split())
_CANVAS_KEYS: Final = frozenset("renderMode sortingOrder overrideSorting pixelPerfect worldCamera planeDistance scaleFactor referencePixelsPerUnit uiScaleMode referenceResolution screenMatchMode matchWidthOrHeight".split())
_LINEAR_KEYS: Final = frozenset("padding spacing childAlignment childControlWidth childControlHeight childForceExpandWidth childForceExpandHeight childScaleWidth childScaleHeight reverseArrangement".split())
_GRID_KEYS: Final = frozenset("padding childAlignment cellSize spacing startCorner startAxis constraint constraintCount".split())
_ELEMENT_KEYS: Final = frozenset("ignoreLayout minWidth minHeight preferredWidth preferredHeight flexibleWidth flexibleHeight layoutPriority".split())
_FITTER_KEYS: Final = frozenset("horizontalFit verticalFit".split())
_BOOL_KEYS: Final = frozenset("enableAutoSizing raycastTarget overrideSorting pixelPerfect childControlWidth childControlHeight childForceExpandWidth childForceExpandHeight childScaleWidth childScaleHeight reverseArrangement ignoreLayout".split())
_ENUM_KEYS: Final = frozenset("alignment renderMode uiScaleMode screenMatchMode childAlignment startCorner startAxis constraint horizontalFit verticalFit".split())
_INT_KEYS: Final = frozenset({"sortingOrder", "constraintCount", "layoutPriority"})
_POSITIVE_KEYS: Final = frozenset({"fontSize", "fontSizeMin", "fontSizeMax", "planeDistance", "scaleFactor", "referencePixelsPerUnit"})
_FLOAT_MAX: Final = 3.4028234663852886e38


def _finite_float(value: JsonValue) -> bool:
    return type(value) in (int, float) and -_FLOAT_MAX <= value <= _FLOAT_MAX and math.isfinite(value)


def _float32(value: int | float) -> float:
    """Match native Number's double-to-Single cast for subsequent domain checks."""
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        # Derived RectTransform arithmetic can overflow otherwise valid inputs.
        return math.copysign(math.inf, value)


def _vector(value: JsonValue, names: tuple[str, ...]) -> list[float] | None:
    """Read exact JSON vector shapes without Boolean or string conversion."""
    match value:
        case list() if len(value) == len(names):
            items = value
        case dict() if set(value) == set(names):
            items = [value[name] for name in names]
        case _:
            return None
    if not all(_finite_float(item) for item in items):
        return None
    return [_float32(item) for item in items]


def _create_offsets_finite(
    props: dict[str, JsonValue], vectors: dict[str, list[float]], stretch: bool,
) -> bool:
    """Mirror native ValidateRect's offset projection using creation defaults only."""
    size = [0.0, 0.0] if stretch else [160.0, 80.0]
    position = [0.0, 0.0]
    pivot = vectors.get("pivot", [0.5, 0.5])
    for key in props:
        if key not in {"offsetMin", "offsetMax"}:
            continue
        is_min = key == "offsetMin"
        for axis, value in enumerate(vectors[key]):
            inverse_pivot = _float32(1 - pivot[axis])
            edge_factor = pivot[axis] if is_min else inverse_pivot
            edge = _float32(size[axis] * edge_factor)
            reference = _float32(position[axis] + (-edge if is_min else edge))
            offset = _float32(value - reference)
            size[axis] = _float32(size[axis] + (-offset if is_min else offset))
            move_factor = inverse_pivot if is_min else pivot[axis]
            position[axis] = _float32(position[axis] + _float32(offset * move_factor))
            if not math.isfinite(size[axis]) or not math.isfinite(position[axis]):
                return False
    return True


def _properties_error(action: str, element_type: str | None, props: dict[str, JsonValue]) -> str | None:
    """Reject request-intrinsic uGUI errors before inspecting Editor readiness."""
    kind = props.get("type")
    match action:
        case "create":
            allowed = _RECT_KEYS
            if element_type == "text":
                allowed |= _TEXT_KEYS
            if element_type in ("panel", "image", "button"):
                allowed |= {"color"}
        case "set_rect":
            allowed = _RECT_KEYS
        case "set_text":
            allowed = _TEXT_KEYS
        case "set_canvas":
            allowed = _CANVAS_KEYS
        case "set_layout":
            if not isinstance(kind, str):
                return "properties.type is required for set_layout."
            kind = kind.lower()
            match kind:
                case "vertical" | "horizontal":
                    allowed = _LINEAR_KEYS
                case "grid":
                    allowed = _GRID_KEYS
                case "layout_element":
                    allowed = _ELEMENT_KEYS
                case "content_size_fitter":
                    allowed = _FITTER_KEYS
                case _:
                    return "properties.type must be vertical, horizontal, grid, layout_element or content_size_fitter."
            allowed |= {"type"}
        case _:
            return None
    unknown = set(props) - allowed
    if unknown:
        return f"Unsupported properties for {action}: {', '.join(sorted(unknown))}."
    vectors: dict[str, list[float]] = {}
    numbers: dict[str, float] = {}
    for key, value in props.items():
        error = f"properties.{key} has an invalid value."
        if key == "type":
            continue
        if key in _BOOL_KEYS:
            if type(value) is not bool:
                return error + " Expected a boolean."
            continue
        if key in _ENUM_KEYS or key == "text":
            if type(value) is not str or (key != "text" and not value):
                return error + " Expected a string."
            continue
        if key == "worldCamera":
            if value is not None and (type(value) not in (str, int) or (type(value) is int and not -(2**31) <= value < 2**31)):
                return error + " Expected a GameObject name/path, integer instance ID, or null."
            continue
        if key == "padding":
            if not isinstance(value, dict) or set(value) != {"left", "right", "top", "bottom"} or any(type(item) is not int or not 0 <= item <= 100000 for item in value.values()):
                return error + " Expected left, right, top and bottom integers in 0..100000."
            continue
        if key in _INT_KEYS:
            lower, upper = (-32768, 32767) if key == "sortingOrder" else (-(2**31), 2**31 - 1)
            if key == "constraintCount":
                lower = 1
            if type(value) is not int or not lower <= value <= upper:
                return error + f" Expected an integer in {lower}..{upper}."
            continue
        names = None
        if key in _RECT_KEYS or key in {"referenceResolution", "cellSize"} or (key == "spacing" and kind == "grid"):
            names = ("x", "y", "z") if key in {"localScale", "localEulerAngles"} else ("x", "y")
        if key == "color":
            names = ("r", "g", "b", "a")
        if names is not None:
            vector = _vector(value, names)
            if vector is None:
                return error + f" Expected {len(names)} finite numeric components."
            if key in {"pivot", "color"} and any(item < 0 or item > 1 for item in vector):
                return error + " Components must be in 0..1."
            if key in {"referenceResolution", "cellSize"} and any(item <= 0 for item in vector):
                return error + " Dimensions must be positive."
            vectors[key] = vector
            continue
        if not _finite_float(value):
            return error + " Expected a finite float."
        number = _float32(value)
        numbers[key] = number
        if key in _POSITIVE_KEYS and number <= 0:
            return error + " Expected a positive number."
        if key == "matchWidthOrHeight" and not 0 <= number <= 1:
            return error + " Expected a number in 0..1."
        if key in _ELEMENT_KEYS and number < -1:
            return error + " Expected a number of at least -1."
    anchor_min, anchor_max = vectors.get("anchorMin"), vectors.get("anchorMax")
    if action == "create":
        # Native creation validates the proposed values before constructing components.
        anchor_min = anchor_min if anchor_min is not None else ([0, 0] if element_type == "panel" else [0.5, 0.5])
        anchor_max = anchor_max if anchor_max is not None else ([1, 1] if element_type == "panel" else [0.5, 0.5])
    if anchor_min is not None and anchor_max is not None and any(low > high for low, high in zip(anchor_min, anchor_max)):
        return "properties.anchorMin must not exceed anchorMax."
    if ({"offsetMin", "offsetMax"} & props.keys()) and ({"sizeDelta", "anchoredPosition"} & props.keys()):
        return "Use offsets or sizeDelta/anchoredPosition in one request, since these properties overlap."
    if action == "create" and not _create_offsets_finite(props, vectors, element_type == "panel"):
        return "Offsets would overflow RectTransform position or size. Use smaller finite offsets."
    font_min, font_max = numbers.get("fontSizeMin"), numbers.get("fontSizeMax")
    if action == "create" and element_type == "text":
        font_min = font_min if font_min is not None else 8
        font_max = font_max if font_max is not None else 72
    if font_min is not None and font_max is not None and font_min > font_max:
        return "properties.fontSizeMin must not exceed fontSizeMax."
    return None


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
        "pivot, anchoredPosition, sizeDelta, offsetMin, offsetMax, localScale, localEulerAngles. "
        "Create: all elements accept Rect properties; panel/image/button also accept color; text also accepts "
        "Text properties. Image raycastTarget requires manage_components after creation. "
        "Layout: type must be vertical, horizontal, grid, layout_element or content_size_fitter, plus that "
        "component's properties. Text: text, fontSize, color, alignment, enableAutoSizing, fontSizeMin, "
        "fontSizeMax, raycastTarget. Canvas: Canvas and CanvasScaler properties via set_canvas.",
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
            or (type(value) is int and not -(2**31) <= value < 2**31)
        ):
            return {
                "success": False,
                "message": f"{key} must be a non-empty GameObject name/path or Int32 instance ID.",
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
    property_error = _properties_error(action_lower, element_type, properties or {})
    if property_error:
        return {"success": False, "message": property_error}

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
