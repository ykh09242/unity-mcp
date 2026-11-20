"""Narrow evidence and request contracts for camera inventory and saved UXML trials."""

from collections import Counter
import math
from pathlib import PurePosixPath
from xml.etree import ElementTree


CAMERA_TASK = "camera-depth"
UI_TASK = "saved-ui-controls"
MENU_TASK = "menu-declaration"
MENU_RESOURCE = "mcpforunity://menu-items"
FRAME_DEBUGGER = "Window/Analysis/Frame Debugger"
MENU_LOOKUPS = {"execute_menu_item.inspect", "execute_menu_item.exists"}
MENU_ROUTES = {MENU_RESOURCE, *MENU_LOOKUPS}
CAMERA_RESOURCE = "mcpforunity://scene/cameras"
CAMERA_READER = "inspect_scene_cameras"
CAMERA_ROUTES = {
    CAMERA_RESOURCE,
    CAMERA_READER,
    "manage_camera.list_cameras",
    "manage_cameras.list_cameras",
}
UI_ROUTES = {"ui_source.read", "manage_ui.read"}
SAVED_UI = "shell.read_saved_ui"
FIELDS = {
    CAMERA_TASK: ("camera", "depth"),
    UI_TASK: ("buttons", "text_fields"),
    MENU_TASK: ("menu_path",),
}


def valid_ui_path(path):
    if not isinstance(path, str) or not path or len(path) > 512:
        return False
    candidate = PurePosixPath(path)
    return (
        candidate.as_posix() == path
        and candidate.parts[:1] == ("Assets",)
        and ".." not in candidate.parts
        and all(part == part.rstrip(" .") for part in candidate.parts)
        and candidate.suffix.casefold() == ".uxml"
        and not any(c in path for c in '\\%:?#*[]<>"|')
        and all(c.isprintable() for c in path)
        and not path.casefold().startswith("assets/resources/gamedata/")
    )


def permitted(route, args, fixture):
    if route == MENU_RESOURCE:
        return fixture.get("task") == MENU_TASK and not args.get("url_query")
    if route in MENU_LOOKUPS:
        path = args.get("menu_path")
        return (
            fixture.get("task") == MENU_TASK
            and args.get("action") == route.split(".")[1]
            and isinstance(path, str)
            and 0 < len(path) <= 1024
            and path.isprintable()
            and set(args) <= {"action", "menu_path", "unity_instance"}
        )
    if route in UI_ROUTES:
        path = fixture.get("ui_asset")
        return (
            fixture.get("task") == UI_TASK
            and valid_ui_path(path)
            and args.get("path") == path
            and args.get("action") == "read"
            and set(args) <= {"path", "unity_instance", "action"}
        )
    if route == CAMERA_READER:
        return fixture.get("task") == CAMERA_TASK and set(args) <= {"unity_instance"}
    if route in CAMERA_ROUTES - {CAMERA_RESOURCE}:
        return (
            fixture.get("task") == CAMERA_TASK
            and args.get("action") == "list_cameras"
            and set(args) <= {"action", "unity_instance"}
        )
    return None


def camera_facts(data):
    """A tie, a truncated list or a malformed row cannot establish a unique maximum."""
    rows = data.get("unityCameras")
    if not isinstance(rows, list) or not rows or data.get("truncated"):
        return {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
            return {}
        depth = row.get("depth")
        if type(depth) not in (float, int) or (type(depth) is float and not math.isfinite(depth)):
            return {}
    highest = max(row["depth"] for row in rows)
    winners = [row["name"] for row in rows if row["depth"] == highest]
    return {"camera": winners[0], "depth": highest} if len(winners) == 1 else {}


def ui_facts(contents):
    """Count authored Unity UI Toolkit controls from one complete bounded UXML document."""
    if (
        not isinstance(contents, str)
        or len(contents) > 1_048_576
        or "<!DOCTYPE" in contents.upper()
    ):
        return {}
    try:
        root = ElementTree.fromstring(contents)
    except (ElementTree.ParseError, ValueError):
        return {}
    namespace = "{UnityEngine.UIElements}"
    if root.tag != namespace + "UXML":
        return {}
    tags = Counter(node.tag for node in root.iter())
    return {"buttons": tags[namespace + "Button"], "text_fields": tags[namespace + "TextField"]}
