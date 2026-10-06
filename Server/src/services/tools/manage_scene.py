from typing import Annotated, Literal, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_int, coerce_bool
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from services.tools.preflight import preflight


@mcp_for_unity_tool(
    description=(
        "Performs CRUD operations on Unity scenes. "
        "Read-only actions: get_hierarchy, get_active, get_build_settings, get_loaded_scenes, scene_view_frame. "
        "Modifying actions: create (with optional template), load (with optional additive flag), save, "
        "close_scene, set_active_scene, move_to_scene, validate (with optional auto_repair). "
        "For build settings management (add/remove/enable scenes), use manage_build(action='scenes'). "
        "For screenshots, use manage_camera (screenshot, screenshot_multiview actions)."
    ),
    annotations=ToolAnnotations(
        title="Manage Scene",
        destructiveHint=True,
    ),
)
async def manage_scene(
    ctx: Context,
    action: Annotated[Literal[
        "create",
        "load",
        "save",
        "get_hierarchy",
        "get_active",
        "get_build_settings",
        "scene_view_frame",
        "close_scene",
        "set_active_scene",
        "get_loaded_scenes",
        "move_to_scene",
        "validate",
    ], "Perform CRUD operations on Unity scenes and control the Scene View camera."],
    name: Annotated[str, "Scene name."] | None = None,
    path: Annotated[str, "Scene path, under Assets/ or Packages/. A bare path is treated as relative to Assets/."] | None = None,
    build_index: Annotated[int | str,
                           "Unity build index (quote as string, e.g., '0')."] | None = None,
    # --- scene_view_frame params ---
    scene_view_target: Annotated[str | int,
                                 "GameObject reference for scene_view_frame (name, path, or instance ID)."] | None = None,
    # --- get_hierarchy paging/safety ---
    parent: Annotated[str | int,
                      "Optional parent GameObject reference (name/path/instanceID) to list direct children."] | None = None,
    page_size: Annotated[int | str,
                         "Page size for get_hierarchy paging."] | None = None,
    cursor: Annotated[int | str,
                      "Opaque cursor for paging (offset)."] | None = None,
    max_nodes: Annotated[int | str,
                         "Hard cap on returned nodes per request (safety)."] | None = None,
    max_depth: Annotated[int | str,
                         "Accepted for forward-compatibility; current paging returns a single level."] | None = None,
    max_children_per_node: Annotated[int | str,
                                     "Child paging hint (safety)."] | None = None,
    include_transform: Annotated[bool | str,
                                 "If true, include local transform in node summaries."] | None = None,
    # --- Multi-scene editing params ---
    scene_name: Annotated[str,
                          "Scene name for multi-scene operations."] | None = None,
    scene_path: Annotated[str,
                          "Full scene path (e.g. 'Assets/Scenes/Level2.unity')."] | None = None,
    target: Annotated[str | int,
                      "GameObject reference (name, path, or instanceID) for move_to_scene."] | None = None,
    remove_scene: Annotated[bool | str,
                            "For close_scene: true to fully remove, false to just unload."] | None = None,
    additive: Annotated[bool | str,
                        "For load: true to open scene additively (keeps current scene)."] | None = None,
    # --- Scene template ---
    template: Annotated[str,
                        "For create: scene template ('empty', 'default', '3d_basic', '2d_basic'). Omit for empty scene."] | None = None,
    # --- Scene validation ---
    auto_repair: Annotated[bool | str,
                           "For validate: true to auto-fix missing scripts (undoable)."] | None = None,
) -> dict[str, Any]:
    try:
        integer_options: dict[str, int | None] = {}
        for field, raw_value in (
            ("build_index", build_index), ("page_size", page_size),
            ("cursor", cursor), ("max_nodes", max_nodes),
            ("max_depth", max_depth), ("max_children_per_node", max_children_per_node),
        ):
            try:
                integer_options[field] = coerce_int(raw_value, default=None)
            except ValueError as exc:
                return {"success": False, "message": f"{field}: {exc}"}
        boolean_options: dict[str, bool | None] = {}
        for field, raw_value in (
            ("additive", additive), ("remove_scene", remove_scene),
            ("auto_repair", auto_repair), ("include_transform", include_transform),
        ):
            try:
                boolean_options[field] = coerce_bool(raw_value, default=None)
            except ValueError as exc:
                return {"success": False, "message": f"{field}: {exc}"}
        coerced_build_index = integer_options["build_index"]
        coerced_page_size = integer_options["page_size"]
        coerced_cursor = integer_options["cursor"]
        coerced_max_nodes = integer_options["max_nodes"]
        coerced_max_depth = integer_options["max_depth"]
        coerced_max_children_per_node = integer_options["max_children_per_node"]
        coerced_include_transform = boolean_options["include_transform"]

        if action == "create":
            if not name:
                return {"success": False, "message": "name is required for create."}
            if template and template.lower() not in ("empty", "default", "3d_basic", "2d_basic"):
                return {"success": False, "message": "Unknown template; use empty, default, 3d_basic, or 2d_basic."}
        if action == "load" and not name and not path and coerced_build_index is None:
            return {"success": False, "message": "load requires name, path, or build_index."}
        if (action == "load" and not name and not path
                and coerced_build_index is not None and coerced_build_index < 0):
            return {"success": False, "message": "build_index must be nonnegative."}
        if action in ("close_scene", "set_active_scene", "move_to_scene"):
            selected_name = scene_name if scene_name is not None else name
            if not selected_name and not scene_path:
                return {"success": False, "message": f"{action} requires scene_name or scene_path."}
        if action == "move_to_scene" and (target is None or target == ""):
            return {"success": False, "message": "target is required for move_to_scene."}

        params: dict[str, Any] = {"action": action}
        if name:
            params["name"] = name
        if path:
            params["path"] = path
        if coerced_build_index is not None:
            params["buildIndex"] = coerced_build_index

        # scene_view_frame params
        if scene_view_target is not None:
            params["sceneViewTarget"] = scene_view_target

        # get_hierarchy paging/safety params (optional)
        if parent is not None:
            params["parent"] = parent
        if coerced_page_size is not None:
            params["pageSize"] = coerced_page_size
        if coerced_cursor is not None:
            params["cursor"] = coerced_cursor
        if coerced_max_nodes is not None:
            params["maxNodes"] = coerced_max_nodes
        if coerced_max_depth is not None:
            params["maxDepth"] = coerced_max_depth
        if coerced_max_children_per_node is not None:
            params["maxChildrenPerNode"] = coerced_max_children_per_node
        if coerced_include_transform is not None:
            params["includeTransform"] = coerced_include_transform

        # Multi-scene editing params
        if scene_name is not None:
            params["sceneName"] = scene_name
        if scene_path is not None:
            params["scenePath"] = scene_path
        if target is not None:
            params["target"] = target
        coerced_remove_scene = boolean_options["remove_scene"]
        if coerced_remove_scene is not None:
            params["removeScene"] = coerced_remove_scene
        coerced_additive = boolean_options["additive"]
        if coerced_additive is not None:
            params["additive"] = coerced_additive
        # Scene template
        if template is not None:
            params["template"] = template

        # Scene validation
        coerced_auto_repair = boolean_options["auto_repair"]
        if coerced_auto_repair is not None:
            params["autoRepair"] = coerced_auto_repair

        # Use centralized retry helper with instance routing
        unity_instance = await get_unity_instance_from_context(ctx)
        gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=True)
        if gate is not None:
            return gate.model_dump()
        response = await send_with_unity_instance(async_send_command_with_retry, unity_instance, "manage_scene", params)

        # Preserve structured failure data; unwrap success into a friendlier shape
        if isinstance(response, dict) and response.get("success"):
            friendly = {"success": True, "message": response.get("message", "Scene operation successful."), "data": response.get("data")}
            return friendly
        return response if isinstance(response, dict) else {"success": False, "message": str(response)}

    except Exception as e:
        return {"success": False, "message": f"Python error managing scene: {str(e)}"}
