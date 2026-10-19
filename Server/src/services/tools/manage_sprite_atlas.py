"""Explicit, guarded SpriteAtlas V2 authoring and scoped editor preview packing."""

import json
import math
import re
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import JsonValue, StrictInt, StrictStr

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

AtlasAction = Literal[
    "capabilities", "create", "read", "configure", "add_packables", "remove_packables", "pack"
]
_WRITES = {"configure", "add_packables", "remove_packables", "pack"}


def _identity(value: Any) -> bool:
    return (
        type(value) is dict
        and set(value) == {"guid", "file_id"}
        and type(value["guid"]) is str
        and re.fullmatch(r"[0-9a-fA-F]{32}", value["guid"]) is not None
        and type(value["file_id"]) is str
        and re.fullmatch(r"-?[1-9][0-9]{0,18}", value["file_id"]) is not None
    )


def _error(action: str, values: dict[str, Any]) -> str | None:
    try:
        if len(json.dumps(values, allow_nan=False)) > 65536:
            return "SpriteAtlas request exceeds 64 KiB."
    except (TypeError, ValueError):
        return "SpriteAtlas requests require finite JSON values."
    if action == "capabilities":
        return None
    path = values.get("path")
    if (
        type(path) is not str
        or not path.startswith("Assets/")
        or not path.endswith(".spriteatlasv2")
        or ".." in path.split("/")
    ):
        return "path must be a contained Assets/*.spriteatlasv2 path."
    if action in _WRITES:
        if not _identity({"guid": values.get("guid"), "file_id": values.get("file_id")}):
            return "Existing atlas writes require exact guid/file_id identity from read."
    if action in _WRITES or values.get("offset", 0):
        revision = values.get("expected_revision")
        if type(revision) is not str or not 1 <= len(revision) <= 128:
            return "Writes and continuation require expected_revision from read."
    if action == "pack" and not values.get("build_target"):
        return "pack requires the active build_target reported by capabilities."
    size, offset = values.get("page_size"), values.get("offset")
    if size is not None and (type(size) is not int or not 1 <= size <= 100):
        return "page_size must be an integer between 1 and 100."
    if offset is not None and (type(offset) is not int or not 0 <= offset <= 256):
        return "offset must be an integer between 0 and 256."
    platforms = values.get("platforms")
    if platforms is not None and (
        type(platforms) is not list
        or len(platforms) > 4
        or len(set(platforms)) != len(platforms)
        or any(x not in {"Standalone", "Android", "iPhone", "WebGL"} for x in platforms)
    ):
        return "platforms accepts distinct Standalone, Android, iPhone and WebGL profiles."
    props = values.get("properties")
    if action == "pack" and props is not None:
        return "pack accepts no authoring properties; configure explicitly before packing."
    if action in {"create", "configure", "add_packables", "remove_packables"}:
        if type(props) is not dict or not props:
            return "This authoring action requires nonempty properties."
        allowed = {
            "include_in_build",
            "master",
            "master_revision",
            "variant_scale",
            "packing",
            "texture",
            "platforms",
        }
        if action == "create":
            allowed.add("packables")
            if type(props.get("include_in_build")) is not bool:
                return "create requires explicit include_in_build true or false."
        elif action in {"add_packables", "remove_packables"}:
            allowed = {"packables"}
        if set(props) - allowed:
            return "Unsupported SpriteAtlas authoring property."
        if "include_in_build" in props and type(props["include_in_build"]) is not bool:
            return "include_in_build must be a boolean."
        if "master" in props and (
            not _identity(props["master"])
            or type(props.get("master_revision")) is not str
            or not props["master_revision"]
        ):
            return "master requires an exact atlas reference and master_revision from read."
        if "master_revision" in props and "master" not in props:
            return "master_revision requires master."
        if "variant_scale" in props and (
            type(props["variant_scale"]) not in (int, float)
            or not 0 < props["variant_scale"] <= 1
            or not math.isfinite(props["variant_scale"])
        ):
            return "variant_scale must be a finite number greater than zero and at most one."
        if "packables" in props:
            refs = props["packables"]
            if (
                type(refs) is not list
                or not 1 <= len(refs) <= 128
                or not all(_identity(x) for x in refs)
            ):
                return "packables requires 1-128 exact Sprite guid/file_id references."
        if action in {"add_packables", "remove_packables"} and "packables" not in props:
            return "packables is required."
        for section in ("packing", "texture", "platforms"):
            if section in props and (type(props[section]) is not dict or not props[section]):
                return f"{section} must be a nonempty object."
        for section, settings in (
            ("packing", props.get("packing", {})),
            ("texture", props.get("texture", {})),
        ):
            for key, value in settings.items():
                bools = {
                    "enableRotation",
                    "enableTightPacking",
                    "enableAlphaDilation",
                    "generateMipMaps",
                    "readable",
                    "sRGB",
                }
                if key in bools and type(value) is not bool:
                    return f"{key} must be a boolean."
                if key == "padding" and (type(value) is not int or value not in {2, 4, 8}):
                    return "padding must be 2, 4 or 8 pixels."
                if key == "anisoLevel" and (type(value) is not int or not 0 <= value <= 16):
                    return "anisoLevel must be an integer between 0 and 16."
                allowed_fields = (
                    {"padding", "enableRotation", "enableTightPacking", "enableAlphaDilation"}
                    if section == "packing"
                    else {"anisoLevel", "filterMode", "generateMipMaps", "readable", "sRGB"}
                )
                if key not in allowed_fields:
                    return f"Unsupported {section} setting: {key}."
        for platform, settings in props.get("platforms", {}).items():
            if (
                platform not in {"Standalone", "Android", "iPhone", "WebGL"}
                or type(settings) is not dict
                or not settings
            ):
                return "Unsupported platform override profile or empty settings."
            for key, value in settings.items():
                if key not in {
                    "overridden",
                    "maxTextureSize",
                    "format",
                    "textureCompression",
                    "compressionQuality",
                }:
                    return f"Unsupported platform setting: {key}."
                if key == "overridden" and type(value) is not bool:
                    return "overridden must be a boolean."
                if key == "maxTextureSize" and (
                    type(value) is not int or not 32 <= value <= 4096 or value & (value - 1)
                ):
                    return "maxTextureSize must be a power of two between 32 and 4096."
                if key == "compressionQuality" and (
                    type(value) is not int or not 0 <= value <= 100
                ):
                    return "compressionQuality must be an integer between 0 and 100."
    return None


@mcp_for_unity_tool(
    group="animation",
    description=(
        "Inspect and explicitly author SpriteAtlas V2 Assets/*.spriteatlasv2 files. "
        "Actions: capabilities, create, read, configure, add_packables, remove_packables, pack. "
        "create requires properties.include_in_build; no overwrite or implicit folder creation. "
        "Existing writes require guid, file_id and expected_revision from read. "
        "Direct Sprite packables only, 128 references per request and 256 per atlas; no folders or texture expansion. "
        "properties supports include_in_build, packing, texture, platforms and exact master/master_revision with variant_scale. "
        "Read returns bounded packables pages, settings, master/variant and stored platform profiles; continuation requires revision. "
        "pack is synchronous editor preview for the explicit active build_target; a variant declares its master in the pack scope. "
        "Packing writes native caches/imported output and may unload/reload Sprites. It is not a Player build or proof of runtime delivery. "
        "Disabled/non-V2 modes refuse packing; no global settings, prebuild hooks or Addressables changes. Asset saves do not promise Undo or arbitrary callback rollback."
    ),
    annotations=ToolAnnotations(title="Manage Sprite Atlas", destructiveHint=True),
)
async def manage_sprite_atlas(
    ctx: Context,
    action: Annotated[AtlasAction, "Explicit authoring, inspection or preview-pack action."],
    path: Annotated[
        StrictStr | None, "Existing or new contained Assets/*.spriteatlasv2 asset path."
    ] = None,
    guid: Annotated[StrictStr | None, "Existing writes: exact atlas GUID from read."] = None,
    file_id: Annotated[
        StrictStr | None, "Existing writes: exact persistent local fileID string from read."
    ] = None,
    expected_revision: Annotated[
        StrictStr | None, "Writes/continuation: exact revision from read."
    ] = None,
    properties: Annotated[
        dict[str, JsonValue] | None,
        "create/configure settings or add/remove packables. Booleans are strict; enums use names. Packing: padding(2,4,8), enableRotation, enableTightPacking, enableAlphaDilation if supported. Texture: anisoLevel,filterMode,generateMipMaps,readable,sRGB. Platform profiles Standalone/Android/iPhone/WebGL: overridden,maxTextureSize(32-4096 power of two),format(Automatic/RGBA32/RGB24/Alpha8),textureCompression,compressionQuality. master uses {guid,file_id} plus master_revision; variant_scale is >0..1. Omitted settings persist.",
    ] = None,
    platforms: Annotated[
        list[StrictStr] | None,
        "read: selected stored platform profiles, max4; default Standalone, plus default profile.",
    ] = None,
    build_target: Annotated[
        StrictStr | None,
        "pack: exact active BuildTarget name from capabilities; no platform switch.",
    ] = None,
    offset: Annotated[
        StrictInt | None, "Packables page offset 0-256; continuation requires expected_revision."
    ] = None,
    page_size: Annotated[StrictInt | None, "Packables page size1-100, default50."] = None,
) -> dict[str, Any]:
    """Send only explicit bounded authoring arguments through the selected Unity transport."""
    if action not in {
        "capabilities",
        "create",
        "read",
        "configure",
        "add_packables",
        "remove_packables",
        "pack",
    }:
        return {"success": False, "message": "Unknown SpriteAtlas action."}
    values = {
        "path": path,
        "guid": guid,
        "file_id": file_id,
        "expected_revision": expected_revision,
        "properties": properties,
        "platforms": platforms,
        "build_target": build_target,
        "offset": offset,
        "page_size": page_size,
    }
    error = _error(action, values)
    if error:
        return {"success": False, "message": error}
    instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "manage_sprite_atlas",
        {"action": action, **{key: value for key, value in values.items() if value is not None}},
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
