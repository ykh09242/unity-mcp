"""Play and stop existing scene AudioSources through Unity's native audio tool."""

from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import StrictInt, StrictStr

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


@mcp_for_unity_tool(
    group="core",
    description=(
        "Play or stop an existing scene AudioSource in Play mode. "
        "Play requires an active, enabled source and a usable AudioClip; optionally supply "
        "a clip asset path under Assets/ or Packages/. Stop also supports inactive, disabled "
        "or clipless sources. No component is created. A successful response confirms the "
        "request was issued, not audible output. Omit search_method for automatic ID, path "
        "or name resolution."
    ),
    annotations=ToolAnnotations(title="Manage Audio", destructiveHint=True),
)
async def manage_audio(
    ctx: Context,
    action: Annotated[Literal["play", "stop"], "Playback action to perform."],
    target: Annotated[StrictStr | StrictInt, "Scene GameObject name, hierarchy path or integer instance ID."],
    clip: Annotated[str | None, "Optional AudioClip asset path for play only; omit to use the source's current clip."] = None,
    search_method: Annotated[
        Literal["by_id", "by_name", "by_path", "by_id_or_name_or_path"] | None,
        "Target selector. Omitted or null uses automatic ID, path or name resolution.",
    ] = None,
) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    params = {"action": action, "target": target}
    if clip is not None:
        params["clip"] = clip
    if search_method is not None:
        params["searchMethod"] = search_method

    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "manage_audio", params,
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
