"""Explicit scene-scoped navigation authoring and native path observations."""

from typing import Annotated, Final, Literal, get_args

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import JsonValue

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

NavigationAction = Literal[
    "capabilities",
    "list",
    "read",
    "add",
    "configure",
    "bake",
    "bake_status",
    "cancel_bake",
    "sample_position",
    "calculate_path",
]
NavigationKind = Literal["surface", "agent", "obstacle", "link"]
ALL_ACTIONS: Final = get_args(NavigationAction)


@mcp_for_unity_tool(
    group="core",
    description=(
        "Read and explicitly author navigation surfaces, agents, obstacles and links. "
        "Use capabilities to detect the optional AI Navigation package/version. "
        "Scene operations require an explicit loaded scene_handle; targets are exact component "
        "or GameObject instance IDs. add creates one component, configure applies supported "
        "public properties with Undo. Bake requires an active surface using Children collection "
        "and returns a queued job; poll bake_status for native completion. Queued jobs can be "
        "cancelled; the synchronous native bake cannot be interrupted. No automatic global bake "
        "or project-setting changes. Path and sample queries use agent_type_id/area_mask and "
        "report scene attribution limits of Unity's global NavMesh. Save authored scenes explicitly."
    ),
    annotations=ToolAnnotations(title="Manage Navigation", destructiveHint=True),
)
async def manage_navigation(
    ctx: Context,
    action: Annotated[NavigationAction, "Explicit navigation action."],
    scene_handle: Annotated[
        int | None, "Loaded scene handle; required for scene operations."
    ] = None,
    kind: Annotated[NavigationKind | None, "Component kind."] = None,
    target: Annotated[int | None, "Exact component or scene GameObject instance ID."] = None,
    properties: Annotated[
        dict[str, JsonValue] | None,
        "Supported component public properties; vectors are [x,y,z], enums use names. "
        "Surface: agentTypeID/collectObjects/useGeometry/layerMask/defaultArea/center/size, "
        "tile and voxel overrides. Agent: agentTypeID/areaMask/movement/avoidance. "
        "Obstacle: shape/center/size/radius/height/carving. "
        "Link: agentTypeID/area/startPoint/endPoint/width/bidirectional/costModifier/autoUpdate.",
    ] = None,
    job_id: Annotated[str | None, "Bake job ID for bake_status or cancel_bake."] = None,
    position: Annotated[list[float] | None, "World point for sample_position."] = None,
    start: Annotated[list[float] | None, "World start for calculate_path."] = None,
    end: Annotated[list[float] | None, "World end for calculate_path."] = None,
    agent_type_id: Annotated[
        int | None, "Configured NavMesh agent type; required for queries."
    ] = None,
    area_mask: Annotated[int | None, "Query area bitmask; default all areas (-1)."] = None,
    max_distance: Annotated[
        float | None, "Sample radius, greater than zero and at most 100."
    ] = None,
    offset: Annotated[int | None, "List offset, nonnegative."] = None,
    limit: Annotated[int | None, "List page size 1..100 (default 50)."] = None,
) -> dict[str, JsonValue]:
    """Send bounded navigation commands through the selected Unity transport."""
    if action not in ALL_ACTIONS:
        return {"success": False, "message": f"Unknown navigation action: {action}."}
    if limit is not None and (isinstance(limit, bool) or not 1 <= limit <= 100):
        return {"success": False, "message": "limit must be an integer between 1 and 100."}
    if offset is not None and (isinstance(offset, bool) or not 0 <= offset <= 2_147_483_647):
        return {"success": False, "message": "offset must be a nonnegative 32-bit integer."}
    params: dict[str, JsonValue] = {"action": action}
    values: dict[str, JsonValue] = {
        "scene_handle": scene_handle,
        "kind": kind,
        "target": target,
        "properties": properties,
        "job_id": job_id,
        "position": position,
        "start": start,
        "end": end,
        "agent_type_id": agent_type_id,
        "area_mask": area_mask,
        "max_distance": max_distance,
        "offset": offset,
        "limit": limit,
    }
    params.update({key: value for key, value in values.items() if value is not None})
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, instance, "manage_navigation", params
    )
    # The transport boundary returns JSON envelopes; malformed transport replies are failures.
    if isinstance(response, dict):
        return response
    return {"success": False, "message": str(response)}
