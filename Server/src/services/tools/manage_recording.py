"""Bounded, explicitly polled Editor video recording jobs."""

import math
from pathlib import PurePosixPath
from typing import Annotated, Any, Final, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

MAX_PIXEL_FRAMES: Final = 2_000_000_000


@mcp_for_unity_tool(
    description=(
        "Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. "
        "start returns job_id immediately; poll status explicitly or stop to finalize. Game View requires "
        "running unpaused Play Mode and a visible focused Game View; Scene View must stay visible. "
        "Windows/macOS interactive Editors only, subject to native MediaEncoder codec availability; "
        "capabilities reports support. Frames carry actual capture timestamps (variable frame rate); "
        "status reports actual frames and skipped sample slots. Output paths are local to the Unity host, "
        "inside project Captures/Recordings, with no overwrite or inline base64. Interrupted/failed jobs "
        "discard partial video. Resolution is scaled to width/height (aspect ratio differences stretch). "
        "Both encoded pixels and source plus encoded pixels have a 2 billion total pixel-frame budget."
    ),
    annotations=ToolAnnotations(title="Manage Recording", readOnlyHint=False, destructiveHint=True),
)
async def manage_recording(
    ctx: Context,
    action: Annotated[Literal["capabilities", "start", "status", "stop"], "Recording operation."],
    job_id: Annotated[str | None, "Required for status/stop; returned by start."] = None,
    capture_source: Annotated[Literal["game_view", "scene_view"], "View to capture."] = "game_view",
    duration_seconds: Annotated[
        float, Field(ge=0.1, le=60, allow_inf_nan=False), "Maximum wall-clock recording duration."
    ] = 10,
    fps: Annotated[
        int, Field(ge=1, le=30), "Maximum sampling rate; missing frames are not synthesized."
    ] = 15,
    width: Annotated[int, Field(ge=16, le=1920), "Even output width."] = 1280,
    height: Annotated[int, Field(ge=16, le=1920), "Even output height."] = 720,
    output_folder: Annotated[
        str | None,
        "Project-relative directory within Captures/Recordings, or an absolute path inside it.",
    ] = None,
    file_name: Annotated[
        str | None, "New plain filename, optionally ending in .mp4; generated if omitted."
    ] = None,
) -> dict[str, Any]:
    """Validate recording bounds and route one operation to the selected Unity instance."""
    params: dict[str, Any] = {"action": action}
    match action:
        case "start":
            if width % 2 or height % 2:
                return {"success": False, "error": "Recording width/height must be even integers."}
            if width * height * math.ceil(duration_seconds * fps) > MAX_PIXEL_FRAMES:
                return {
                    "success": False,
                    "error": "Recording exceeds the total pixel-frame budget; reduce resolution, fps or duration.",
                }
            if (
                output_folder is not None
                and ".." in PurePosixPath(output_folder.replace("\\", "/")).parts
            ):
                return {
                    "success": False,
                    "error": "Recording output paths must not contain traversal segments.",
                }
            if file_name is not None and (
                not file_name.strip()
                or len(file_name) > 128
                or file_name != file_name.strip()
                or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in file_name)
            ):
                return {
                    "success": False,
                    "error": "file_name must be a plain filename without paths or reserved characters (up to 128 characters).",
                }
            params.update(
                capture_source=capture_source,
                duration_seconds=duration_seconds,
                fps=fps,
                width=width,
                height=height,
            )
            if output_folder is not None:
                params["output_folder"] = output_folder
            if file_name is not None:
                params["file_name"] = file_name
        case "status" | "stop":
            if job_id is None or not job_id.strip():
                return {"success": False, "error": "job_id is required for recording status/stop."}
            params["job_id"] = job_id
        case "capabilities":
            pass
        case unreachable:
            assert_never(unreachable)
    unity_instance = await get_unity_instance_from_context(ctx)
    return await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_recording",
        params,
    )
