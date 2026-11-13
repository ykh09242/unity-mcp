from typing import Annotated, Literal, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry.capability_adapter import capability_command
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_int, coerce_bool
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from services.tools.preflight import preflight
from services.tools.pagination import page_integer
from services.tools.reference_diagnostics import reference_page, reference_path


@mcp_for_unity_tool(
    group="legacy",
    description=(
        "Performs CRUD operations on Unity scenes. "
        "Read-only validate_references pages broken serialized PPtrs in active_scene/loaded_scenes "
        "or an explicit prefab/scriptable_object asset; page_size<=100, max_objects<=250000, "
        "max_work<=4000000. Ordinary nulls are excluded; unavailable GUID/fileID evidence is explicit. "
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
    action: Annotated[
        Literal[
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
            "validate_references",
        ],
        "Perform CRUD operations on Unity scenes and control the Scene View camera.",
    ],
    name: Annotated[str, "Scene name. For Save As, the destination name."] | None = None,
    path: Annotated[
        str,
        "Scene path under Assets/ or Packages/. For Save As, the destination under Assets/. A bare path is relative to Assets/.",
    ]
    | None = None,
    build_index: Annotated[int | str, "Unity build index (quote as string, e.g., '0')."]
    | None = None,
    allow_overwrite: Annotated[bool | str, "Allow Save As to replace an existing scene."]
    | None = None,
    # --- scene_view_frame params ---
    scene_view_target: Annotated[
        str | int, "GameObject reference for scene_view_frame (name, path, or instance ID)."
    ]
    | None = None,
    # --- get_hierarchy paging/safety ---
    parent: Annotated[
        str | int,
        "Optional parent GameObject reference (name/path/instanceID) to list direct children.",
    ]
    | None = None,
    page_size: Annotated[int | str, "Page size for get_hierarchy paging."] | None = None,
    cursor: Annotated[int | str, "Opaque cursor for paging (offset)."] | None = None,
    max_nodes: Annotated[int | str, "Hard cap on returned nodes per request (safety)."]
    | None = None,
    max_depth: Annotated[
        int | str, "Accepted for forward-compatibility; current paging returns a single level."
    ]
    | None = None,
    max_children_per_node: Annotated[int | str, "Child paging hint (safety)."] | None = None,
    include_transform: Annotated[bool | str, "If true, include local transform in node summaries."]
    | None = None,
    # --- Multi-scene editing params ---
    scene_name: Annotated[str, "Loaded scene name. For save, selects the source scene."]
    | None = None,
    scene_path: Annotated[
        str,
        "Exact loaded scene path (e.g. 'Assets/Scenes/Level2.unity'). For save, selects the source scene.",
    ]
    | None = None,
    target: Annotated[
        str | int, "GameObject reference (name, path, or instanceID) for move_to_scene."
    ]
    | None = None,
    remove_scene: Annotated[
        bool | str, "For close_scene: true to fully remove, false to just unload."
    ]
    | None = None,
    additive: Annotated[
        bool | str, "For load: true to open scene additively (keeps current scene)."
    ]
    | None = None,
    # --- Scene template ---
    template: Annotated[
        str,
        "For create: scene template ('empty', 'default', '3d_basic', '2d_basic'). Omit for empty scene.",
    ]
    | None = None,
    # --- Scene validation ---
    auto_repair: Annotated[
        bool | str,
        "For validate: remove missing scripts after a complete bounded scan. Not undoable; cancellation before commit only; an admitted commit finishes its plan.",
    ]
    | None = None,
    max_milliseconds: Annotated[
        int | str,
        "validate: cooperative time budget 1-1000ms, default 25; native calls cannot be interrupted.",
    ]
    | None = None,
    continuation: Annotated[
        str,
        "validate: opaque read-only continuation, valid for five minutes in the same unchanged Editor scene.",
    ]
    | None = None,
    reference_scope: Annotated[
        Literal["active_scene", "loaded_scenes", "prefab", "scriptable_object"],
        "validate_references only; loaded scopes never open scenes.",
    ]
    | None = None,
    max_objects: Annotated[
        int | str,
        "validate: 1-10000 objects per slice, default 1000; validate_references: 1-250000, default 1024.",
    ]
    | None = None,
    max_work: Annotated[
        int | str,
        "validate_references: maximum 1-4000000 traversal/property steps, default 100000.",
    ]
    | None = None,
    page_number: Annotated[int | str, "validate_references: 1-based page, maximum page_size 100."]
    | None = None,
    reference_revision: Annotated[
        str, "validate_references: refuse if the repeated observation has drifted."
    ]
    | None = None,
    reference_snapshot: Annotated[
        bool, "validate_references: capture stable paged evidence without rescanning later pages."
    ]
    | None = None,
    reference_cursor: Annotated[
        str,
        "validate_references: returned snapshot cursor; captured evidence, not a live revision recheck.",
    ]
    | None = None,
    verify: Annotated[
        bool | None,
        "load/save only: observe the exact affected scene with one100-object/10ms validation slice",
    ] = None,
    scene_handle: Annotated[
        int | None,
        "validate/save: exact scene_handle from get_loaded_scenes. For save, all source selectors must match one loaded scene; omit them to save the active scene.",
    ] = None,
) -> dict[str, Any]:
    if verify is not None and (type(verify) is not bool or action not in {"load", "save"}):
        return {"success": False, "message": "verify requires load/save and a boolean value."}
    if scene_handle is not None and (
        type(scene_handle) is not int or action not in {"validate", "save"}
    ):
        return {
            "success": False,
            "message": "scene_handle requires validate/save and an integer identity.",
        }
    if action == "validate_references":
        try:
            if any(
                value is not None
                for value in (
                    name,
                    build_index,
                    allow_overwrite,
                    scene_view_target,
                    parent,
                    cursor,
                    max_nodes,
                    max_depth,
                    max_children_per_node,
                    include_transform,
                    scene_name,
                    scene_path,
                    target,
                    remove_scene,
                    additive,
                    template,
                    auto_repair,
                    max_milliseconds,
                    continuation,
                )
            ):
                raise ValueError(
                    "validate_references accepts only diagnostic scope/path/page/budget options; no repairs."
                )
            scope = reference_scope if reference_scope is not None else "active_scene"
            if scope not in {"active_scene", "loaded_scenes", "prefab", "scriptable_object"}:
                raise ValueError("Unknown reference_scope.")
            params = reference_page(
                page_size,
                page_number,
                max_work,
                reference_revision,
                scene=True,
                reference_snapshot=reference_snapshot,
                reference_cursor=reference_cursor,
            )
            params.update(
                action=action,
                referenceScope=scope,
                maxObjects=page_integer(max_objects, 1024, 1, 250000, "max_objects"),
            )
            if scope in {"prefab", "scriptable_object"}:
                params["path"] = reference_path(path, "path")
            elif path is not None:
                raise ValueError("Loaded scene diagnostic scopes do not accept an asset path.")
        except (ValueError, TypeError) as exc:
            return {
                "success": False,
                "code": "invalid_reference_diagnostics",
                "message": str(exc)[:512],
            }
        instance = await get_unity_instance_from_context(ctx)
        gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=False)
        if gate is not None:
            return gate.model_dump()
        response = await send_with_unity_instance(
            async_send_command_with_retry,
            instance,
            capability_command("manage_scene"),
            params,
        )
        return (
            response if isinstance(response, dict) else {"success": False, "message": str(response)}
        )
    if any(
        value is not None
        for value in (
            reference_scope,
            max_work,
            page_number,
            reference_revision,
            reference_snapshot,
            reference_cursor,
        )
    ):
        return {
            "success": False,
            "code": "invalid_reference_diagnostics",
            "message": "Reference options require validate_references.",
        }
    validation_options: dict[str, int | str] = {}
    try:
        if action == "validate":
            validation_options["maxObjects"] = page_integer(
                max_objects, 1000, 1, 10000, "max_objects"
            )
            validation_options["maxMilliseconds"] = page_integer(
                max_milliseconds, 25, 1, 1000, "max_milliseconds"
            )
            if continuation is not None:
                if (
                    not isinstance(continuation, str)
                    or len(continuation) != 32
                    or any(character not in "0123456789abcdef" for character in continuation)
                ):
                    raise ValueError(
                        "continuation must be an opaque returned token; restart validation."
                    )
                if coerce_bool(auto_repair, default=False):
                    raise ValueError("auto_repair requires a new complete scan; omit continuation.")
                validation_options["continuation"] = continuation
        elif any(value is not None for value in (max_objects, max_milliseconds, continuation)):
            raise ValueError("Scene validation budgets and continuation require validate.")
    except (ValueError, TypeError) as exc:
        return {"success": False, "code": "invalid_scene_validation", "message": str(exc)[:512]}
    try:
        integer_options: dict[str, int | None] = {}
        for field, raw_value in (
            ("build_index", build_index),
            ("page_size", page_size),
            ("cursor", cursor),
            ("max_nodes", max_nodes),
            ("max_depth", max_depth),
            ("max_children_per_node", max_children_per_node),
        ):
            try:
                integer_options[field] = coerce_int(raw_value, default=None)
            except ValueError as exc:
                return {"success": False, "message": f"{field}: {exc}"}
        boolean_options: dict[str, bool | None] = {}
        for field, raw_value in (
            ("allow_overwrite", allow_overwrite),
            ("additive", additive),
            ("remove_scene", remove_scene),
            ("auto_repair", auto_repair),
            ("include_transform", include_transform),
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
                return {
                    "success": False,
                    "message": "Unknown template; use empty, default, 3d_basic, or 2d_basic.",
                }
        if action == "save" and path is not None and not path.strip():
            return {"success": False, "message": "A supplied save path must be nonempty."}
        if action == "load" and not name and not path and coerced_build_index is None:
            return {"success": False, "message": "load requires name, path, or build_index."}
        if (
            action == "load"
            and not name
            and not path
            and coerced_build_index is not None
            and coerced_build_index < 0
        ):
            return {"success": False, "message": "build_index must be nonnegative."}
        if action in ("close_scene", "set_active_scene", "move_to_scene"):
            selected_name = scene_name if scene_name is not None else name
            if not selected_name and not scene_path:
                return {"success": False, "message": f"{action} requires scene_name or scene_path."}
        if action == "move_to_scene" and (target is None or target == ""):
            return {"success": False, "message": "target is required for move_to_scene."}

        params: dict[str, Any] = {"action": action, **validation_options}
        if verify is not None:
            params["verify"] = verify
        if scene_handle is not None:
            params["scene_handle"] = scene_handle
        if name:
            params["name"] = name
        if path:
            params["path"] = path
        if coerced_build_index is not None:
            params["buildIndex"] = coerced_build_index
        if boolean_options["allow_overwrite"] is not None:
            params["allowOverwrite"] = boolean_options["allow_overwrite"]

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
        response = await send_with_unity_instance(
            async_send_command_with_retry,
            unity_instance,
            capability_command("manage_scene"),
            params,
        )

        # Preserve structured failure data; unwrap success into a friendlier shape
        if isinstance(response, dict) and response.get("success"):
            friendly = {
                "success": True,
                "message": response.get("message", "Scene operation successful."),
                "data": response.get("data"),
            }
            return friendly
        return (
            response if isinstance(response, dict) else {"success": False, "message": str(response)}
        )

    except Exception as e:
        return {"success": False, "message": f"Python error managing scene: {str(e)}"}
