"""Bounded TerrainData inspection and guarded region/index authoring."""

import json
import math
import re
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import StrictBool, StrictInt

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

ALL_ACTIONS = (
    "get_info",
    "get_region",
    "set_region",
    "get_trees",
    "set_trees",
    "get_layer",
    "set_layer",
)
_REVISION = re.compile(r"terrain1:[0-9a-f]{64}\Z")
_GUID = re.compile(r"[0-9a-f]{32}\Z")


def _number(value: Any, low: float, high: float) -> bool:
    return type(value) in (int, float) and low <= value <= high and math.isfinite(value)


def _validate(action: str, args: dict[str, Any]) -> str | None:
    if action not in ALL_ACTIONS:
        return "Unknown Terrain action."
    if any(type(args[key]) is not bool for key in ("allow_shared_data", "allow_boundary")):
        return "Terrain acknowledgements must be booleans."
    if not isinstance(args.get("target"), str) or not 1 <= len(args["target"]) <= 512:
        return "target must contain1..512 characters."
    if args.get("searchMethod") not in (None, "by_id", "by_name", "by_path"):
        return "Unsupported Terrain selector."
    write = action.startswith("set_")
    if write:
        if not isinstance(args.get("expected_revision"), str) or not _REVISION.fullmatch(
            args["expected_revision"]
        ):
            return "A scoped expected_revision is required."
        if not isinstance(args.get("expected_data_guid"), str) or not _GUID.fullmatch(
            args["expected_data_guid"]
        ):
            return "A persistent expected_data_guid is required."
    allowed = {"action", "target", "searchMethod", "allow_shared_data", "allow_boundary"}
    if write:
        allowed |= {"expected_revision", "expected_data_guid"}
    if action.endswith("_region"):
        allowed |= {"channel", "region", "detail_layer"}
        if write:
            allowed.add("values")
        channel, region = args.get("channel"), args.get("region")
        if channel not in ("heights", "alphamaps", "holes", "details"):
            return "channel must be heights, alphamaps, holes or details."
        if not isinstance(region, dict) or set(region) != {"x", "y", "width", "height"}:
            return "region requires exact x, y, width and height."
        if any(type(v) is not int for v in region.values()):
            return "region values must be strict integers."
        if not (
            0 <= region["x"] <= 65535
            and 0 <= region["y"] <= 65535
            and 1 <= region["width"] <= 32
            and 1 <= region["height"] <= 32
        ):
            return "Region exceeds the coordinate/32x32 bounds."
        if channel == "details":
            if type(args.get("detail_layer")) is not int or not 0 <= args["detail_layer"] < 16:
                return "details requires detail_layer from 0 through 15."
        elif "detail_layer" in args:
            return "detail_layer applies only to details."
        if write:
            rows = args.get("values")
            if not isinstance(rows, list) or len(rows) != region["height"]:
                return "values must exactly match region height."
            work = 0
            layers = None
            for row in rows:
                if not isinstance(row, list) or len(row) != region["width"]:
                    return "values must exactly match region width."
                for cell in row:
                    if channel == "alphamaps":
                        if (
                            not isinstance(cell, list)
                            or not 1 <= len(cell) <= 16
                            or (layers is not None and layers != len(cell))
                        ):
                            return "Alphamap cells must have a consistent 1..16 layer vector."
                        layers = len(cell)
                        if not all(_number(v, 0, 1) for v in cell) or abs(sum(cell) - 1) > 0.00001:
                            return "Every alphamap cell must sum to one."
                        work += len(cell)
                    else:
                        work += 1
                        valid = (
                            type(cell) is bool
                            if channel == "holes"
                            else type(cell) is int and 0 <= cell <= 16
                            if channel == "details"
                            else _number(cell, 0, 1)
                        )
                        if not valid:
                            return "Invalid Terrain cell value."
            if work > 4096:
                return "Region exceeds the 4096-scalar budget."
    elif action.endswith("_trees"):
        allowed.add("indices")
        if write:
            allowed.add("values")
        indices = args.get("indices")
        if (
            not isinstance(indices, list)
            or not 1 <= len(indices) <= 64
            or any(type(i) is not int or not 0 <= i <= 10000000 for i in indices)
            or len(set(indices)) != len(indices)
        ):
            return "Supply 1..64 distinct nonnegative tree indices."
        if write:
            rows = args.get("values")
            if not isinstance(rows, list) or len(rows) != len(indices):
                return "Tree values must match indices."
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or not row
                    or set(row)
                    - {
                        "position",
                        "width_scale",
                        "height_scale",
                        "rotation",
                        "color",
                        "lightmap_color",
                    }
                ):
                    return "Tree edits accept position, scales, rotation and colors only."
                for name, value in row.items():
                    if name == "position":
                        valid = (
                            isinstance(value, list)
                            and len(value) == 3
                            and all(_number(v, 0, 1) for v in value)
                        )
                    elif name in ("color", "lightmap_color"):
                        valid = (
                            isinstance(value, list)
                            and len(value) == 4
                            and all(type(v) is int and 0 <= v <= 255 for v in value)
                        )
                    else:
                        valid = _number(value, 0, math.tau if name == "rotation" else 10) and (
                            name == "rotation" or value > 0
                        )
                    if not valid:
                        return "Invalid tree field value."
    elif action.endswith("_layer"):
        allowed.add("index")
        if type(args.get("index")) is not int or not 0 <= args["index"] < 16:
            return "index must be from 0 through 15."
        if write:
            allowed |= {"layer_path", "layer_guid"}
            path, guid = args.get("layer_path"), args.get("layer_guid")
            if (
                not isinstance(path, str)
                or len(path) > 512
                or not path.startswith(("Assets/", "Packages/"))
                or "\\" in path
                or any(p in ("", ".", "..") for p in path.split("/"))
            ):
                return "layer_path must be a contained Unity asset path."
            if not isinstance(guid, str) or not _GUID.fullmatch(guid):
                return "layer_guid must identify the persistent TerrainLayer."
    if set(args) - allowed:
        return "A supplied field does not apply to this Terrain action."
    return None


@mcp_for_unity_tool(
    group="core",
    description=(
        "Inspect and edit existing TerrainData with bounded region/index requests. "
        "get_info lists identities/resolutions/layers/prototypes; get_region/set_region use "
        "heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays. "
        "Regions are at most32x32 and4096 scalars. get_trees/set_trees use1..64 exact existing indices; "
        "get_layer/set_layer replace one existing layer slot, preserving count and weights within "
        "a65536-scalar full-alphamap verification budget. Writes require expected_data_guid and "
        "the exact scoped expected_revision from the corresponding read. Shared loaded users and "
        "boundary patches require explicit acknowledgements; neighboring terrains are never changed. "
        "Authoring refuses native Undo snapshot estimates above16777216 scalar units. "
        "Native Undo is registered; failures use conditional compensation and preserve external writers. "
        "Edits mark TerrainData dirty; no global asset save is performed. "
        "No terrain creation/resolution change, tree/prototype insertion or whole-terrain dump."
    ),
    annotations=ToolAnnotations(title="Manage Terrain", destructiveHint=True, readOnlyHint=False),
)
async def manage_terrain(
    ctx: Context,
    action: Annotated[str, "Terrain action."],
    target: Annotated[str, "Existing Terrain GameObject id/name/path."],
    search_method: Literal["by_id", "by_name", "by_path"] | None = None,
    channel: str | None = None,
    region: dict[str, Any] | None = None,
    values: list[Any] | None = None,
    indices: list[StrictInt] | None = None,
    detail_layer: StrictInt | None = None,
    index: StrictInt | None = None,
    layer_path: str | None = None,
    layer_guid: str | None = None,
    expected_data_guid: str | None = None,
    expected_revision: str | None = None,
    allow_shared_data: StrictBool = False,
    allow_boundary: StrictBool = False,
) -> dict[str, Any]:
    args = {
        key: value
        for key, value in {
            "action": action.lower(),
            "target": target,
            "searchMethod": search_method,
            "channel": channel,
            "region": region,
            "values": values,
            "indices": indices,
            "detail_layer": detail_layer,
            "index": index,
            "layer_path": layer_path,
            "layer_guid": layer_guid,
            "expected_data_guid": expected_data_guid,
            "expected_revision": expected_revision,
            "allow_shared_data": allow_shared_data,
            "allow_boundary": allow_boundary,
        }.items()
        if value is not None
    }
    error = _validate(args["action"], args)
    if error:
        return {"success": False, "message": error}
    if len(json.dumps(args, allow_nan=False).encode("utf-8")) > 262144:
        return {"success": False, "message": "Terrain request exceeds256KiB."}
    instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, instance, "manage_terrain", args
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
