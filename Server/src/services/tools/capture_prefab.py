"""Static prefab capture with one owned preview lifetime."""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.server import ToolResult
from mcp.types import ToolAnnotations
from pydantic import Field

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from services.tools.utils import extract_screenshot_images
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


@mcp_for_unity_tool(
    group="prefabs",
    description=(
        "Render a prefab asset to PNG in an isolated preview scene, then clean up. "
        "Supports root RectTransform UI and front-view renderer bounds. Edit Mode and graphics required. "
        "Does not save the prefab, initialize gameplay, animate or switch locales. Edit-time callbacks may execute."
    ),
    annotations=ToolAnnotations(title="Capture Prefab", readOnlyHint=False, destructiveHint=False),
)
async def capture_prefab(
    ctx: Context,
    prefab_path: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4096,
            description="Project-relative Assets/... or Packages/... .prefab path.",
        ),
    ],
    width: Annotated[int, Field(strict=True, ge=64, le=2048)] = 1024,
    height: Annotated[int, Field(strict=True, ge=64, le=2048)] = 1024,
) -> ToolResult | dict:
    if (
        not isinstance(prefab_path, str)
        or len(prefab_path) > 4096
        or not prefab_path.startswith(("Assets/", "Packages/"))
        or not prefab_path.lower().endswith(".prefab")
        or "\\" in prefab_path
        or any(part in {"", ".", ".."} for part in prefab_path.split("/"))
        or any(type(value) is not int or not 64 <= value <= 2048 for value in (width, height))
    ):
        return {"success": False, "error": "Invalid prefab path or capture dimensions."}
    blocked = await preflight(ctx, wait_for_no_compile=True)
    if blocked is not None:
        return blocked.model_dump()
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "capture_prefab",
        {"prefab_path": prefab_path, "width": width, "height": height},
        retry_on_reload=False,
    )
    response = response.model_dump() if isinstance(response, MCPResponse) else response
    if not isinstance(response, dict):
        return {"success": False, "error": "Prefab capture response unavailable."}
    if response.get("success") is True:
        data = response.get("data")
        if (
            not isinstance(data, dict)
            or data.get("cleanup_completed") is not True
            or not data.get("imageBase64")
        ):
            return {"success": False, "error": "Prefab capture or cleanup evidence is incomplete."}
    return extract_screenshot_images(response) or response
