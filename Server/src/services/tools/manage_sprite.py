"""
2D sprite animation tool.
Automates: sprite sheet slicing, AnimationClip creation from sliced frames,
and AnimatorController generation.
"""

import json
import math
import re
from typing import Annotated, Any, Literal, get_args

from pydantic import JsonValue, StrictInt, StrictStr

from fastmcp import Context
from fastmcp.server.server import ToolResult
from mcp.types import ImageContent, TextContent, ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

SpriteAction = Literal[
    "get_info",
    "slice_sheet",
    "setup_clips",
    "setup_controller",
    "full_setup",
    "get_geometry",
    "set_geometry",
]

VALID_ACTIONS: list[str] = list(get_args(SpriteAction))


def _geometry_error(action: str, values: dict[str, Any]) -> str | None:
    """Keep strict JSON scalar values intact; Unity validates native bounds and identities."""
    try:
        if len(json.dumps(values, allow_nan=False)) > 262144:
            return "Geometry request exceeds 256 KiB."
    except (ValueError, TypeError):
        return "Geometry requires finite JSON values."
    if action == "get_geometry":
        size, cursor = values.get("page_size"), values.get("cursor")
        if size is not None and (type(size) is not int or not 1 <= size <= 100):
            return "Geometry page_size must be an integer between 1 and 100."
        if cursor is not None and (type(cursor) is not int or not 0 <= cursor <= 4096):
            return "Geometry cursor must be an integer between 0 and 4096."
        if cursor and not values.get("expected_revision"):
            return "Geometry continuation requires expected_revision."
        return None
    for key in ("guid", "sprite_id"):
        if type(values.get(key)) is not str or not re.fullmatch(r"[0-9a-fA-F]{32}", values[key]):
            return f"{key} must be an exact 32-digit hexadecimal identity from get_geometry."
    if type(values.get("file_id")) is not str or not re.fullmatch(
        r"-?[1-9][0-9]{0,18}", values["file_id"]
    ):
        return "file_id must be an exact nonzero integer string from get_geometry."
    if (
        type(values.get("expected_revision")) is not str
        or not 1 <= len(values["expected_revision"]) <= 128
    ):
        return "set_geometry requires expected_revision from get_geometry."
    geometry = values.get("geometry")
    if (
        type(geometry) is not dict
        or not geometry
        or set(geometry) - {"rect", "pivot", "border", "outlines"}
    ):
        return "geometry accepts only rect, pivot, border and render outlines; supply at least one."
    for key, count in (("rect", 4), ("pivot", 2), ("border", 4)):
        if key not in geometry:
            continue
        data = geometry[key]
        if (
            type(data) is not list
            or len(data) != count
            or any(
                type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 3.402823466e38
                for x in data
            )
        ):
            return f"{key} requires exactly {count} finite numbers, without bool/string coercion."
        if key == "pivot" and any(x < 0 or x > 1 for x in data):
            return "pivot must be normalized between 0 and 1."
        if key == "border" and any(x < 0 for x in data):
            return "border values must be nonnegative."
        if key == "rect" and (data[0] < 0 or data[1] < 0 or data[2] <= 0 or data[3] <= 0):
            return "rect needs a nonnegative origin and positive size."
    if "outlines" in geometry:
        paths = geometry["outlines"]
        if type(paths) is not list or not 1 <= len(paths) <= 16:
            return "outlines requires 1-16 render polygons."
        for polygon in paths:
            if type(polygon) is not list or not 3 <= len(polygon) <= 256:
                return "Each outline requires 3-256 points."
            for point in polygon:
                if (
                    type(point) is not list
                    or len(point) != 2
                    or any(
                        type(x) not in (int, float)
                        or not math.isfinite(x)
                        or abs(x) > 3.402823466e38
                        for x in point
                    )
                ):
                    return "Outline points require two finite numbers."
    return None


def _sprite_image_result(result: dict[str, Any], image_base64: str) -> ToolResult:
    mime = "image/png"
    payload = image_base64
    if image_base64.startswith("data:") and ";base64," in image_base64:
        prefix, payload = image_base64.split(";base64,", 1)
        mime = prefix[5:] or "image/png"

    meta = result.copy()
    meta.pop("image_base64")
    return ToolResult(
        content=[
            TextContent(type="text", text=json.dumps(meta)),
            ImageContent(type="image", data=payload, mimeType=mime),
        ]
    )


@mcp_for_unity_tool(
    group="animation",
    description=(
        "Slice 2D sprite sheets and build AnimationClips and an AnimatorController from the frames. "
        "Actions: get_info returns a sheet's import settings and slices (paged with page_size / cursor) "
        "and, for a PNG or JPEG source, the sheet as an image block for vision analysis; "
        "slice_sheet applies a grid, replacing the sheet's existing slices; "
        "setup_clips creates AnimationClips from the slices; "
        "setup_controller builds a controller from clip names (idle = default state, one walk/run "
        "clip = a plain state and two or more = a Speed-driven 1D blend tree, jump/attack/hurt-type "
        "names = trigger states that fire from any state, other names = plain states); "
        "full_setup runs slice → clips → controller in one call. "
        "get_geometry reads paged exact Sprite identities, rect, pivot, border and render outlines. "
        "set_geometry edits an existing Multiple TextureImporter sprite using guid, sprite_id, file_id and expected_revision. "
        "It preserves omitted fields, names, siblings, physics outlines and AnimationClip references. "
        "Geometry requires optional 2D Sprite providers; capability_mode distinguishes advertised flags from the verified built-in legacy provider. "
        "Missing/denied modern capabilities and unknown providers are refused. Asset reimport does not promise Undo or arbitrary callback rollback."
    ),
    annotations=ToolAnnotations(
        title="Manage Sprite",
        destructiveHint=True,
    ),
)
async def manage_sprite(
    ctx: Context,
    action: Annotated[SpriteAction, "Action to perform."],
    path: Annotated[
        str | None,
        "Sprite texture asset path (e.g. 'Assets/Sprites/hero_walk.png'). Required for get_info, slice_sheet, setup_clips, full_setup.",
    ] = None,
    cols: Annotated[
        int | None,
        "Number of columns in the sprite sheet grid. Used by slice_sheet and full_setup.",
    ] = None,
    rows: Annotated[
        int | None,
        "Number of rows in the sprite sheet grid. Default: 1, or derived from frame_height when that is given.",
    ] = None,
    frame_width: Annotated[
        int | None,
        "Frame width in pixels. Alternative to cols.",
    ] = None,
    frame_height: Annotated[
        int | None,
        "Frame height in pixels. Alternative to rows.",
    ] = None,
    base_name: Annotated[
        str | None,
        "Base name for sliced sprite frames (default: texture filename).",
    ] = None,
    filter_mode: Annotated[
        Literal["point", "bilinear", "trilinear"] | None,
        "slice_sheet and full_setup: texture filter the sliced sheet is imported with, in lowercase "
        "(get_info reports it as Point, Bilinear or Trilinear). Default: point, which keeps pixel art "
        "sharp; bilinear or trilinear suits high-resolution art. Every slice sets it, so a filter set "
        "by hand does not survive a re-slice.",
    ] = None,
    clips: Annotated[
        list[dict[str, Any]] | None,
        "Clip definitions: [{name, start_frame (default 0), end_frame (default the last frame), "
        "fps (default 12), loop (default from the name)}]. "
        "For setup_controller: [{name, path}] where path is the .anim asset path.",
    ] = None,
    animation_name: Annotated[
        str | None,
        "full_setup without clips: name of the one clip made from every frame at 12 fps "
        "(default: the sheet's file name). It loops only for an idle, walk or run-type name.",
    ] = None,
    output_dir: Annotated[
        str | None,
        "setup_clips and full_setup: folder for the .anim assets, and for full_setup's default "
        "controller (default: the sprite's folder).",
    ] = None,
    controller_path: Annotated[
        str | None,
        "Path for the .controller asset (e.g. 'Assets/Animators/Hero.controller'); '.controller' is "
        "appended if missing. Required for setup_controller; full_setup defaults to "
        "'<output_dir>/<sheet_name>_Controller.controller'.",
    ] = None,
    overwrite: Annotated[
        bool,
        "Replace an existing .anim or .controller at the target path. Off by default: an existing "
        "clip is skipped with a CLIP_EXISTS warning, and an existing controller fails the call with "
        "CONTROLLER_EXISTS. Slicing is not covered: it always replaces the sheet's slices.",
    ] = False,
    add_to_scene: Annotated[
        bool,
        "full_setup: give scene_target an Animator with the new controller, replacing any controller "
        "it had, and a SpriteRenderer if it has none (warning SCENE_SPRITE_RENDERER_ADDED).",
    ] = False,
    scene_target: Annotated[
        str | None,
        "full_setup with add_to_scene: name of exactly one existing GameObject (inactive ones count). "
        "A missing, unmatched or ambiguous target fails at step 'add_to_scene', after the clips and "
        "controller are written.",
    ] = None,
    guid: Annotated[StrictStr | None, "set_geometry: exact texture GUID from get_geometry."] = None,
    sprite_id: Annotated[
        StrictStr | None, "set_geometry: exact SpriteRect sprite ID; names are not selectors."
    ] = None,
    file_id: Annotated[
        StrictStr | None, "set_geometry: exact persistent local fileID string from get_geometry."
    ] = None,
    expected_revision: Annotated[
        StrictStr | None, "Geometry write or continuation: revision from the identical fresh read."
    ] = None,
    geometry: Annotated[
        dict[str, JsonValue] | None,
        "set_geometry: omitted fields stay unchanged. rect [x,y,width,height] in source pixels; pivot normalized [x,y]; border [left,bottom,right,top] in pixels; outlines 1-16 polygons of 3-256 centered source-pixel [x,y] points. Bounds are validated before writes; physics outlines are unchanged.",
    ] = None,
    # The numbers below are documentation, not enforcement: SpriteParams and
    # SpriteImportSetup.GetInfo are what actually refuse an out-of-range page_size, and
    # this text is what the generated reference publishes to callers. Two copies, so
    # changing the C# bounds means changing this line in the same commit.
    page_size: Annotated[
        StrictInt | None,
        "get_geometry uses 1-100 entries (default 50). get_info: how many entries of the 'slices' list to return (1-4096, default 512). "
        "A sheet sliced by hand can hold more slices than one response should carry.",
    ] = None,
    cursor: Annotated[
        StrictInt | None,
        "get_geometry requires expected_revision on continuation. get_info: index to start the 'slices' page at. Pass back the 'next_cursor' from "
        "the previous response; next_cursor is null on the last page. The image "
        "is returned only on the first page.",
    ] = None,
) -> dict[str, Any] | ToolResult:
    """2D sprite animation tool."""

    action_lower = action.lower() if action else ""

    if action_lower not in VALID_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid: {', '.join(VALID_ACTIONS)}",
        }

    # Python-side validation
    if (
        action_lower
        in ("get_info", "slice_sheet", "setup_clips", "full_setup", "get_geometry", "set_geometry")
        and not path
    ):
        return {"success": False, "message": f"'path' is required for action '{action}'."}

    if action_lower in ("slice_sheet", "full_setup") and cols is None and frame_width is None:
        return {
            "success": False,
            "message": f"'cols' or 'frame_width' is required for '{action}'. "
            "Use get_info first to view the sheet image, count the grid visually, then call full_setup with cols/rows.",
        }

    if action_lower == "setup_controller" and not controller_path:
        return {
            "success": False,
            "message": "'controller_path' is required for setup_controller (e.g. 'Assets/Animators/Hero.controller').",
        }

    if action_lower in ("get_geometry", "set_geometry"):
        error = _geometry_error(
            action_lower,
            {
                "guid": guid,
                "sprite_id": sprite_id,
                "file_id": file_id,
                "expected_revision": expected_revision,
                "geometry": geometry,
                "page_size": page_size,
                "cursor": cursor,
            },
        )
        if error:
            return {"success": False, "message": error}

    unity_instance = await get_unity_instance_from_context(ctx)

    # `or None` on the two flags, so a False is dropped rather than sent: the C# side
    # reads a missing key as the default, and forwarding every argument buries the real
    # ones in nulls on the wire.
    optional = {
        "path": path,
        "guid": guid,
        "sprite_id": sprite_id,
        "file_id": file_id,
        "expected_revision": expected_revision,
        "geometry": geometry,
        "cols": cols,
        "rows": rows,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "base_name": base_name,
        "filter_mode": filter_mode,
        "clips": clips,
        "animation_name": animation_name,
        "output_dir": output_dir,
        "controller_path": controller_path,
        "page_size": page_size,
        "cursor": cursor,
        "scene_target": scene_target,
        "overwrite": overwrite or None,
        "add_to_scene": add_to_scene or None,
    }

    params: dict[str, Any] = {"action": action_lower}
    params.update({k: v for k, v in optional.items() if v is not None})

    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_sprite",
        params,
    )
    if action_lower == "get_info" and isinstance(result, dict) and result.get("success") is True:
        image_base64 = result.get("image_base64")
        if isinstance(image_base64, str) and image_base64:
            return _sprite_image_result(result, image_base64)
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
