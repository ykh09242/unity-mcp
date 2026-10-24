"""Bounded optional Timeline authoring and owned manual preview lifecycle."""

import json
import math
import re
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import JsonValue, StrictFloat, StrictInt, StrictStr

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

TimelineAction = Literal[
    "capabilities",
    "create",
    "read",
    "add_track",
    "add_clip",
    "assign",
    "bind",
    "read_director",
    "preview_start",
    "seek",
    "preview_stop",
]
_WRITES = {"add_track", "add_clip", "assign", "bind", "preview_start"}
_DIRECTOR = {"assign", "bind", "read_director", "preview_start"}


def _id(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"-?[1-9][0-9]{0,18}", value) is not None


def _reference(value: Any) -> bool:
    return (
        type(value) is dict
        and set(value) == {"guid", "file_id"}
        and type(value["guid"]) is str
        and re.fullmatch(r"[0-9a-fA-F]{32}", value["guid"]) is not None
        and _id(value["file_id"])
    )


def _number(value: Any, minimum: float, maximum: float) -> bool:
    return type(value) in (int, float) and minimum <= value <= maximum and math.isfinite(value)


def _error(action: str, values: dict[str, Any]) -> str | None:
    try:
        if len(json.dumps(values, allow_nan=False).encode("utf-8")) > 65536:
            return "Timeline request exceeds 64 KiB."
    except (TypeError, ValueError, OverflowError):
        return "Timeline requires finite JSON values."
    if action == "capabilities":
        return None
    if action in {"seek", "preview_stop"}:
        token = values.get("preview_id")
        if type(token) is not str or not 1 <= len(token) <= 1024:
            return "A preview_id from preview_start is required."
        if action == "seek" and not _number(values.get("time"), 0, 86400):
            return "seek requires finite time between 0 and 86400 seconds."
        return None
    if action in _DIRECTOR:
        for field in ("target", "scene_handle"):
            value = values.get(field)
            if type(value) is not int or not -(2**31) <= value < 2**31 or value == 0:
                return f"{field} requires an exact nonzero scene/component integer ID."
        revision = values.get("expected_director_revision")
        if action != "read_director" and (
            type(revision) is not str or not 1 <= len(revision) <= 1024
        ):
            return "Director edits/preview require expected_director_revision from read_director."
    for field, minimum, maximum in (
        ("offset", 0, 2**31 - 1),
        ("page_size", 1, 32),
        ("clip_offset", 0, 2**31 - 1),
        ("clip_page_size", 1, 32),
    ):
        value = values.get(field)
        if value is not None and (type(value) is not int or not minimum <= value <= maximum):
            return f"{field} must be an integer between {minimum} and {maximum}."
    if action == "read_director":
        revision = values.get("expected_director_revision")
        if values.get("offset", 0) and (
            type(revision) is not str or not 1 <= len(revision) <= 1024
        ):
            return "Director binding continuation requires expected_director_revision."
        return None
    path = values.get("path")
    if (
        type(path) is not str
        or not path.startswith("Assets/")
        or not path.endswith(".playable")
        or ".." in path.split("/")
        or "\\" in path
        or ":" in path
    ):
        return "path must be a contained Assets/*.playable path."
    if action in _WRITES and not _reference(
        {"guid": values.get("guid"), "file_id": values.get("file_id")}
    ):
        return "Existing Timeline edits/preview require exact guid/file_id from read."
    if action in _WRITES or values.get("offset", 0) or values.get("clip_offset", 0):
        revision = values.get("expected_revision")
        if type(revision) is not str or not 1 <= len(revision) <= 1024:
            return "Existing edits/preview and continuation require expected_revision from read."
    track_id = values.get("track_id")
    if track_id is not None and not _id(track_id):
        return "track_id requires the exact persistent fileID string from read."
    if values.get("clip_offset", 0) and track_id is None:
        return "Clip continuation requires track_id."
    if track_id is not None and values.get("offset", 0):
        return "A track_id clip page does not accept a track offset."
    props = values.get("properties")
    if action in {"create", "add_track", "add_clip", "bind"}:
        if props is None and action == "create":
            props = {}
        if type(props) is not dict:
            return "This authoring action requires a properties object."
        allowed = {
            "create": {"name"},
            "add_track": {"kind", "name"},
            "add_clip": {
                "track_id",
                "source",
                "start",
                "duration",
                "clip_in",
                "time_scale",
                "display_name",
            },
            "bind": {"track_id", "binding_target"},
        }[action]
        if set(props) - allowed:
            return "Unsupported Timeline property."
        for key in ("name", "display_name"):
            if key in props and (
                type(props[key]) is not str or not props[key].strip() or len(props[key]) > 1024
            ):
                return f"{key} requires a nonempty string of at most 1024 characters."
        if action == "add_track" and (
            props.get("kind") not in ("animation", "audio") or not props.get("name")
        ):
            return "add_track requires kind animation/audio and a nonempty name."
        if action in {"bind", "add_clip"} and not _id(props.get("track_id")):
            return "track_id requires the exact persistent fileID string from read."
        if action == "bind" and (
            type(props.get("binding_target")) is not int
            or not -(2**31) <= props["binding_target"] < 2**31
            or props["binding_target"] == 0
        ):
            return "binding_target requires an exact Animator/AudioSource component instance ID."
        if action == "add_clip":
            if not _reference(props.get("source")):
                return "source requires exact {guid,file_id} from an existing saved AnimationClip/AudioClip."
            for key, minimum, maximum, default in (
                ("start", 0, 86400, None),
                ("duration", 0.001, 86400, None),
                ("clip_in", 0, 86400, 0),
                ("time_scale", 0.001, 8, 1),
            ):
                if not _number(props.get(key, default), minimum, maximum):
                    return f"{key} requires a finite number between {minimum} and {maximum}."
    elif props is not None:
        return "This action accepts no properties."
    return None


@mcp_for_unity_tool(
    group="animation",
    description=(
        "Inspect/create optional Timeline Assets/*.playable assets and explicitly add built-in animation/audio tracks and clips. "
        "Actions: capabilities, create, read, add_track, add_clip, assign, bind, read_director, preview_start, seek, preview_stop. "
        "Existing edits and preview require exact asset guid/file_id and expected_revision from read; director actions use target component instance ID, "
        "scene_handle and expected_director_revision from read_director. Clip sources use exact {guid,file_id}; timing is seconds. "
        "Track/clip/binding authoring uses native editor Undo; creating the asset does not promise Undo. No implicit overwrite, folders, packages or custom tracks. "
        "Manual preview supports bounded Transform-only nonhumanoid animation without events/object-reference curves and muted AudioSources. "
        "It refuses existing director graphs, external AnimationMode, custom/control tracks and markers; seek evaluates once, never starts realtime playback. "
        "preview_stop or 120-second idle expiry stops the owned graph and conditionally restores captured state, preserving/reporting external writers. "
        "No universal callback rollback, global Undo or audible playback guarantee. "
        "Read pages contain at most 32 tracks and 32 clips per track, within 256 KiB; continue next_offset using expected_revision. "
        "Continue a track's next_clip_offset using track_id,clip_offset and the same expected_revision. Display names are bounded to 1024 characters. "
        "read_director pages use offset,page_size and expected_director_revision; graph size is not an admission limit."
    ),
    annotations=ToolAnnotations(title="Manage Timeline", destructiveHint=True),
)
async def manage_timeline(
    ctx: Context,
    action: Annotated[TimelineAction, "Explicit inspection, authoring or owned-preview action."],
    path: Annotated[StrictStr | None, "Contained existing/new Assets/*.playable path."] = None,
    guid: Annotated[StrictStr | None, "Exact existing Timeline GUID from read."] = None,
    file_id: Annotated[StrictStr | None, "Exact existing Timeline fileID string from read."] = None,
    expected_revision: Annotated[
        StrictStr | None, "Asset/source revision from read; also required for continuation."
    ] = None,
    properties: Annotated[
        dict[str, JsonValue] | None,
        "create: name optional. add_track: kind(animation/audio),name. add_clip: track_id,source{guid,file_id},start,duration, optional clip_in(0),time_scale(1),display_name. bind: track_id,binding_target exact component ID.",
    ] = None,
    target: Annotated[StrictInt | None, "Exact PlayableDirector component instance ID."] = None,
    scene_handle: Annotated[StrictInt | None, "Exact loaded director scene handle."] = None,
    expected_director_revision: Annotated[
        StrictStr | None,
        "Revision from read_director before assign/bind/preview_start or binding continuation.",
    ] = None,
    preview_id: Annotated[
        StrictStr | None, "Owned session token from preview_start; required by seek/preview_stop."
    ] = None,
    time: Annotated[
        StrictInt | StrictFloat | None,
        "seek: finite absolute time in the supported Timeline range.",
    ] = None,
    offset: Annotated[
        StrictInt | None, "read/read_director: nonnegative track/binding offset, default0."
    ] = None,
    page_size: Annotated[StrictInt | None, "read/read_director: page size1-32, default16."] = None,
    track_id: Annotated[
        StrictStr | None, "read: exact saved track fileID for a clip page; omit offset."
    ] = None,
    clip_offset: Annotated[
        StrictInt | None,
        "read: nonnegative clip offset in track_id; continuation requires expected_revision.",
    ] = None,
    clip_page_size: Annotated[StrictInt | None, "read: clips per track1-32, default16."] = None,
) -> dict[str, Any]:
    """Validate the public boundary and preserve explicit identity/session values."""
    if action not in {
        "capabilities",
        "create",
        "read",
        "add_track",
        "add_clip",
        "assign",
        "bind",
        "read_director",
        "preview_start",
        "seek",
        "preview_stop",
    }:
        return {"success": False, "message": "Unknown Timeline action."}
    values = {
        "path": path,
        "guid": guid,
        "file_id": file_id,
        "expected_revision": expected_revision,
        "properties": properties,
        "target": target,
        "scene_handle": scene_handle,
        "expected_director_revision": expected_director_revision,
        "preview_id": preview_id,
        "time": time,
        "offset": offset,
        "page_size": page_size,
        "track_id": track_id,
        "clip_offset": clip_offset,
        "clip_page_size": clip_page_size,
    }
    error = _error(action, values)
    if error:
        return {"success": False, "message": error}
    instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "manage_timeline",
        {"action": action, **{key: value for key, value in values.items() if value is not None}},
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
