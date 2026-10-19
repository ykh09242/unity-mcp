"""Server-owned remote operation policy; annotations and catalogs never grant authority."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias
from urllib.parse import urlsplit

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class Permission(StrEnum):
    READ = "read"
    TEST = "test"
    EDIT = "edit"
    RUN = "run"


@dataclass(frozen=True, slots=True)
class OperationGrants:
    """Validated immutable authority, including absent-claim migration provenance."""

    permissions: frozenset[Permission]
    scoped: bool


LEGACY_GRANTS: Final = OperationGrants(frozenset(Permission), scoped=False)
NO_GRANTS: Final = OperationGrants(frozenset(), scoped=True)
CUSTOM_GRANTS: Final = frozenset({Permission.EDIT, Permission.RUN})
_PERMISSION_NAMES: Final = frozenset(permission.value for permission in Permission)


class PermissionClaimError(ValueError):
    """Invalid server-owned metadata; no raw claim is included in the exception."""

    def __init__(self) -> None:
        super().__init__("Invalid remote operation permissions")


class OperationPermissionError(PermissionError):
    """An explicitly scoped authority cannot perform this operation."""

    def __init__(self) -> None:
        super().__init__("Remote operation permission denied")


def parse_permissions(metadata: JsonValue, *, strict: bool) -> OperationGrants:
    """Parse at most four unique exact names; malformed present claims fail closed."""
    match metadata:
        case None:
            return NO_GRANTS if strict else LEGACY_GRANTS
        case dict() as values:
            if "unity_mcp_permissions" not in values:
                return NO_GRANTS if strict else LEGACY_GRANTS
            claim = values["unity_mcp_permissions"]
        case _:
            raise PermissionClaimError()
    if not isinstance(claim, list) or len(claim) > 4:
        raise PermissionClaimError()
    parsed: set[Permission] = set()
    for value in claim:
        if not isinstance(value, str) or value not in _PERMISSION_NAMES:
            raise PermissionClaimError()
        permission = Permission(value)
        if permission in parsed:
            raise PermissionClaimError()
        parsed.add(permission)
    return OperationGrants(frozenset(parsed), scoped=True)


def _names(value: str) -> frozenset[str]:
    return frozenset(value.split())


# Explicit action inventory, not a name-prefix or plugin annotation heuristic.
# A mixed read/write action uses the conservative combined requirement below.
_ACTIONS: Final = {
    "manage_asset": {
        Permission.READ: _names(
            "search search_metadata_v1 get_info get_components list_asset_bundles get_bundle_assets get_bundle_dependencies get_labels get_dependencies get_reverse_references preview_move preview_delete"
        ),
        Permission.EDIT: _names(
            "import create modify delete duplicate move rename create_folder set_labels add_labels remove_labels"
        ),
    },
    "manage_scene": {
        Permission.READ: _names(
            "get_hierarchy get_active get_build_settings get_loaded_scenes validate validate_references"
        ),
        Permission.EDIT: _names(
            "create load save scene_view_frame close_scene set_active_scene move_to_scene modify_build_settings"
        ),
    },
    "manage_editor": {
        Permission.READ: _names("telemetry_status get_play_mode_job get_game_view_size"),
        Permission.RUN: _names("telemetry_ping play pause stop cancel_play_mode_job"),
        Permission.EDIT: _names(
            "set_active_tool add_tag remove_tag add_layer remove_layer deploy_package restore_package undo redo set_game_view_size restore_game_view_size"
        ),
    },
    "manage_script": {
        Permission.READ: _names(
            "read get_sha validate validate_edit preview_edit preview_text_edits preview_script_buffer get_capabilities prepare_change_set get_change_set"
        ),
    },
    "manage_shader": {
        Permission.READ: _names("read inspect_graph"),
        Permission.EDIT: _names("create update delete"),
    },
    "manage_gameobject": {
        Permission.EDIT: _names("create modify delete duplicate move_relative look_at")
    },
    "manage_components": {Permission.EDIT: _names("add remove set_property")},
    "manage_scriptable_object": {Permission.EDIT: _names("create modify")},
    "manage_texture": {
        Permission.EDIT: _names(
            "create modify delete create_sprite apply_pattern apply_gradient apply_noise set_import_settings"
        )
    },
    "manage_material": {
        Permission.READ: _names("ping get_material_info get_render_state"),
        Permission.EDIT: _names(
            "create set_material_shader_property set_material_color assign_material_to_renderer set_renderer_color set_render_state"
        ),
    },
    "manage_prefabs": {
        Permission.READ: _names("get_info get_hierarchy list_overrides preview_modify_contents"),
        Permission.EDIT: _names(
            "create_from_gameobject modify_contents open_prefab_stage save_prefab_stage close_prefab_stage revert_overrides apply_overrides apply_preview"
        ),
    },
    "manage_ugui": {
        Permission.READ: _names(
            "ping get_hierarchy diagnose diagnose_projection diagnose_text diagnose_raycast diagnose_device_profiles"
        ),
        Permission.EDIT: _names(
            "create create_cli create_control set_rect set_layout set_text set_canvas set_image"
        ),
    },
    "manage_ui": {
        Permission.READ: _names("ping read get_visual_tree diagnose list"),
        Permission.EDIT: _names(
            "create update delete attach_ui_document detach_ui_document create_panel_settings update_panel_settings link_stylesheet modify_visual_element"
        ),
        Permission.RUN: _names("render_ui"),
    },
    "manage_object_handles": {Permission.READ: _names("capture resolve")},
    "manage_input": {
        Permission.READ: _names("status"),
        Permission.RUN: _names("ui_click key mouse touch release_all"),
    },
    "manage_audio": {
        Permission.READ: _names("diagnose get_routing read_import_settings"),
        Permission.EDIT: _names("set_routing apply_import_settings"),
        Permission.RUN: _names("play stop"),
    },
    "manage_navigation": {
        Permission.READ: _names(
            "capabilities list read bake_status sample_position calculate_path"
        ),
        Permission.EDIT: _names("add configure bake cancel_bake"),
    },
    "script_change_set": {
        Permission.READ: _names("prepare status"),
    },
    "manage_play_scenario": {
        Permission.READ: _names(
            "get list status reports suite_get suite_list suite_status suite_reports"
        ),
        Permission.EDIT: _names("save delete suite_save suite_delete"),
        Permission.RUN: _names("run cancel suite_run suite_cancel"),
    },
    "manage_scenario_recording": {
        Permission.READ: _names("status"),
        Permission.RUN: _names("start stop cancel checkpoint"),
    },
    "manage_recording": {
        Permission.READ: _names("capabilities status"),
        Permission.RUN: _names("start stop"),
    },
    "manage_sprite_atlas": {
        Permission.READ: _names("capabilities read"),
        Permission.EDIT: _names("create configure add_packables remove_packables pack"),
    },
    "manage_terrain": {
        Permission.READ: _names("get_info get_region get_trees get_layer"),
        Permission.EDIT: _names("set_region set_trees set_layer"),
    },
    "manage_sprite": {
        Permission.READ: _names("get_info get_geometry"),
        Permission.EDIT: _names("slice_sheet setup_clips setup_controller full_setup set_geometry"),
    },
    "manage_scene_edits": {
        Permission.READ: _names("preview preview_structure"),
        Permission.EDIT: _names("apply apply_structure"),
    },
    "execute_code": {
        Permission.READ: _names("get_history"),
        Permission.RUN: _names("execute replay clear_history"),
    },
    "execute_menu_item": {
        Permission.READ: _names("inspect exists"),
        Permission.RUN: _names("execute"),
    },
    "read_console": {Permission.READ: _names("get"), Permission.EDIT: _names("clear")},
    "manage_tools": {
        Permission.READ: _names("list_groups"),
        Permission.EDIT: _names("activate deactivate sync reset"),
    },
    "unity_reflect": {Permission.READ: _names("get_type get_member search")},
    "unity_docs": {Permission.READ: _names("get_doc get_manual get_package_doc lookup")},
    "manage_camera": {
        Permission.READ: _names("ping list_cameras get_brain_status"),
        Permission.RUN: _names("screenshot screenshot_multiview force_camera release_override"),
        Permission.EDIT: _names(
            "create_camera set_target set_lens set_priority ensure_brain set_body set_aim set_noise add_extension remove_extension set_blend"
        ),
    },
    "manage_packages": {
        Permission.READ: _names(
            "status list_packages search_packages get_package_info list_registries ping"
        ),
        Permission.EDIT: _names(
            "add_package remove_package add_registry remove_registry embed_package resolve_packages"
        ),
    },
    "manage_physics": {
        Permission.READ: _names(
            "ping get_settings get_collision_matrix raycast raycast_all linecast shapecast overlap get_rigidbody validate diagnose_pair"
        ),
        Permission.EDIT: _names(
            "set_settings set_collision_matrix create_physics_material configure_physics_material assign_physics_material add_joint configure_joint remove_joint configure_rigidbody"
        ),
        Permission.RUN: _names("apply_force simulate_step"),
    },
    "manage_build": {
        Permission.READ: _names("plan status report_history report compare_reports"),
        Permission.RUN: _names("build_from_plan build batch cancel clean_output"),
        Permission.EDIT: _names("platform settings scenes profiles"),
    },
    "manage_profiler": {
        Permission.READ: _names(
            "ping profiler_status get_frame_timing get_counters get_object_memory memory_list_snapshots memory_compare_snapshots frame_debugger_get_events window_status window_result"
        ),
        Permission.RUN: _names(
            "profiler_start profiler_stop profiler_set_areas memory_take_snapshot frame_debugger_enable frame_debugger_disable window_start window_cancel"
        ),
    },
    "blender_bridge": {
        Permission.READ: _names("status scene_info object_info"),
        Permission.RUN: _names("screenshot compare_screenshot run_python check_updates"),
        Permission.EDIT: _names("import_model setup_bloom sync_addon"),
    },
    "generate_audio": {
        Permission.READ: _names("status list_providers"),
        Permission.RUN: _names("cancel list_models refresh_models"),
    },
    "generate_image": {
        Permission.READ: _names("status list_providers"),
        Permission.RUN: _names("cancel list_models refresh_models"),
    },
    "generate_model": {
        Permission.READ: _names("status list_providers"),
        Permission.RUN: _names("cancel list_models refresh_models"),
    },
    "import_model": {
        Permission.READ: _names("search preview status list_providers"),
        Permission.EDIT: _names("import"),
        Permission.RUN: _names("cancel"),
    },
    "manage_animation": {
        Permission.READ: _names(
            "animator_get_info animator_get_parameter controller_get_info clip_get_info clip_get_curves clip_get_curve controller_get_graph"
        ),
        Permission.RUN: _names(
            "animator_play animator_crossfade animator_set_parameter animator_set_speed animator_set_enabled"
        ),
        Permission.EDIT: _names(
            "controller_create controller_add_state controller_add_transition controller_add_parameter controller_assign controller_add_layer controller_remove_layer controller_set_layer_weight controller_create_blend_tree_1d controller_create_blend_tree_2d controller_add_blend_tree_child clip_create clip_add_curve clip_set_curve clip_set_vector_curve clip_create_preset clip_assign clip_add_event clip_remove_event clip_replace_curve clip_delete_curve controller_modify_state controller_delete_state controller_modify_transition controller_delete_transition"
        ),
    },
    "manage_graphics": {
        Permission.READ: _names(
            "ping volume_get_info volume_list_effects bake_status bake_get_settings stats_get stats_list_counters stats_get_memory pipeline_get_info pipeline_get_settings feature_list skybox_get"
        ),
        Permission.RUN: _names("bake_start bake_cancel bake_reflection_probe"),
        Permission.EDIT: _names(
            "volume_create volume_add_effect volume_set_effect volume_remove_effect volume_set_properties volume_create_profile bake_clear bake_set_settings bake_create_light_probe_group bake_create_reflection_probe bake_set_probe_positions stats_set_scene_debug pipeline_set_quality pipeline_set_settings feature_add feature_remove feature_configure feature_toggle feature_reorder skybox_set_material skybox_set_properties skybox_set_ambient skybox_set_fog skybox_set_reflection skybox_set_sun"
        ),
    },
    "manage_probuilder": {
        Permission.READ: _names("ping get_mesh_info validate_mesh get_topology"),
        Permission.EDIT: _names(
            "create_shape create_poly_shape extrude_faces extrude_edges bevel_edges subdivide delete_faces bridge_edges connect_elements detach_faces flip_normals merge_faces combine_meshes merge_objects duplicate_and_flip create_polygon merge_vertices weld_vertices split_vertices move_vertices insert_vertex append_vertices_to_edge select_faces set_face_material set_face_color set_face_uvs convert_to_probuilder set_smoothing auto_smooth center_pivot freeze_transform set_pivot repair_mesh"
        ),
    },
    "manage_vfx": {
        Permission.READ: _names(
            "ping particle_get_info vfx_list_templates vfx_list_assets vfx_get_info vfx_get_exposed_properties line_get_info trail_get_info"
        ),
        Permission.RUN: _names(
            "particle_play particle_stop particle_pause particle_restart particle_clear vfx_send_event vfx_play vfx_stop vfx_pause vfx_reinit vfx_set_playback_speed vfx_set_seed trail_emit"
        ),
        Permission.EDIT: _names(
            "particle_create particle_set_main particle_set_emission particle_set_shape particle_set_color_over_lifetime particle_set_size_over_lifetime particle_set_velocity_over_lifetime particle_set_noise particle_set_renderer particle_enable_module particle_add_burst particle_clear_bursts vfx_create_asset vfx_assign_asset vfx_set_float vfx_set_int vfx_set_bool vfx_set_vector2 vfx_set_vector3 vfx_set_vector4 vfx_set_color vfx_set_gradient vfx_set_texture vfx_set_mesh vfx_set_curve line_set_positions line_add_position line_set_position line_set_width line_set_color line_set_material line_set_properties line_clear line_create_line line_create_circle line_create_arc line_create_bezier trail_set_time trail_set_width trail_set_color trail_set_material trail_set_properties trail_clear"
        ),
    },
}

_READ_COMMANDS: Final = _names(
    "ping get_editor_state get_test_job view_test_results get_compile_job get_tests get_tests_for_mode export_test_job find_gameobjects find_in_file get_sha manage_script_capabilities validate_script capture_ui_snapshot debug_request_context set_active_instance inspect_serialized_object inspect_serialized_object_v1 get_project_info get_selection get_active_tool get_windows get_menu_items get_tags get_layers get_prefab_stage get_gameobject get_gameobject_components get_gameobject_component get_gameobject_component_selected get_gameobject_by_handle_v2 get_rendering_stats get_cameras get_volumes get_renderer_features get_tool_states inspect_menu_item tail_console"
)
_TEST_COMMANDS: Final = _names("run_tests cancel_test_job")
_EDIT_COMMANDS: Final = _names(
    "create_script delete_script apply_text_edits script_apply_edits import_model_file"
)
_PUBLIC_SCRIPT_WRAPPERS: Final = _names(
    "create_script delete_script apply_text_edits script_apply_edits"
)
_COMPILING_SCRIPT_ACTIONS: Final = _names(
    "create delete edit apply_text_edits apply_prepared_script apply_change_set recover_change_set"
)
# BatchExecute dispatches canonical native tools, not Python convenience wrappers.
_CANONICAL_ALIASES: Final = {
    "manage_components_selected": "manage_components",
    "manage_object_handles_v2": "manage_object_handles",
    "execute_menu_item_observed": "execute_menu_item",
}
_NATIVE_COMMANDS: Final = _names(
    "batch_execute batch_validate blender_bridge cancel_test_job capture_ui_snapshot execute_code execute_menu_item execute_menu_item_observed export_test_job find_gameobjects generate_audio generate_image generate_model get_active_tool get_cameras get_compile_job get_editor_state get_gameobject get_gameobject_by_handle_v2 get_gameobject_component get_gameobject_component_selected get_gameobject_components get_layers get_menu_items get_prefab_stage get_project_info get_renderer_features get_rendering_stats get_selection get_tags get_test_job get_tests get_tests_for_mode get_tool_states get_volumes get_windows import_model import_model_file inspect_menu_item inspect_serialized_object_v1 manage_animation manage_asset manage_audio manage_build manage_camera manage_components manage_components_selected manage_editor manage_gameobject manage_graphics manage_input manage_material manage_navigation manage_object_handles_v2 manage_packages manage_physics manage_play_scenario manage_prefabs manage_probuilder manage_profiler manage_recording manage_scenario_recording manage_scene manage_scene_edits manage_script manage_scriptable_object manage_shader manage_sprite manage_sprite_atlas manage_terrain manage_texture manage_ugui manage_ui manage_vfx ping read_console refresh_unity run_tests tail_console unity_reflect"
)
_DEFAULT_ACTIONS: Final = {"read_console": "get", "execute_menu_item": "execute"}
_RESOURCE_PATHS: Final = _names(
    "capabilities doctor tool-diagnostics custom-tools editor/active-tool editor/state scene/cameras scene/gameobject-api project/layers menu-items editor/prefab-stage prefab-api project/info pipeline/renderer-features rendering/stats editor/selection project/tags tests tool-groups instances scene/volumes editor/windows"
)
_RESOURCE_PATTERNS: Final = (
    re.compile(r"tool-diagnostics/[^/?#]+"),
    re.compile(r"tests/(?:EditMode|PlayMode|editmode|playmode)"),
    re.compile(r"scene/gameobject/-?\d+(?:/children|/components|/component/[^/?#]+)?"),
    re.compile(r"scene/gameobject-by-handle/[^/?#]+"),
    re.compile(r"prefab/[^/?#]+(?:/hierarchy)?"),
)


def required_permissions(
    name: str, params: Mapping[str, JsonValue], *, custom: bool = False, native: bool = False
) -> frozenset[Permission]:
    """Classify a known operation, rejecting unknown and nested scoped batch members."""
    if custom or name == "execute_custom_tool":
        return CUSTOM_GRANTS
    # The Python tool routes inspect/exists to a different native command.
    # Native execute handlers ignore a supplied action: do not trust that alias input.
    if native and name in {"execute_menu_item", "execute_menu_item_observed"}:
        return frozenset({Permission.RUN})
    if name in {"batch_execute", "batch_validate"}:
        commands = params.get("commands")
        if not isinstance(commands, list) or not 1 <= len(commands) <= 100:
            raise OperationPermissionError()
        required: set[Permission] = set()
        for member in commands:
            if not isinstance(member, dict):
                raise OperationPermissionError()
            tool, arguments = member.get("tool"), member.get("params", {})
            if (
                not isinstance(tool, str)
                or tool.casefold() in {"batch_execute", "batch_validate", "execute_custom_tool"}
                or tool not in _NATIVE_COMMANDS
            ):
                raise OperationPermissionError()
            if not isinstance(arguments, dict) or "unity_instance" in arguments:
                raise OperationPermissionError()
            required.update(required_permissions(tool, arguments, native=True))
        return frozenset(required)
    if name == "refresh_unity":
        if not native and params.get("wait_for_ready", True) is not False:
            return CUSTOM_GRANTS | {Permission.READ}
        return CUSTOM_GRANTS
    name = _CANONICAL_ALIASES.get(name, name)
    if name in _PUBLIC_SCRIPT_WRAPPERS:
        # These wrappers inspect/verify/await readiness, and their write paths
        # explicitly compile. Keep proven read-only preview routes unchanged.
        if name in {"apply_text_edits", "script_apply_edits"}:
            from services.tools.utils import coerce_bool

            options = params.get("options")
            if options is not None and not isinstance(options, dict):
                raise OperationPermissionError()
            flags = options or {}
            try:
                preview = coerce_bool(flags.get("preview"), default=False)
                debug_preview = (
                    coerce_bool(flags.get("debug_preview"), default=False)
                    if name == "apply_text_edits"
                    else False
                )
            except ValueError as error:
                raise OperationPermissionError() from error
            if preview or debug_preview:
                return frozenset({Permission.READ, Permission.EDIT})
        return CUSTOM_GRANTS | {Permission.READ}
    if name in _READ_COMMANDS:
        return frozenset({Permission.READ})
    if name in _TEST_COMMANDS:
        return frozenset({Permission.TEST})
    if name in _EDIT_COMMANDS:
        return frozenset({Permission.EDIT})
    actions = _ACTIONS.get(name)
    action = params.get("action")
    if action is None:
        action = _DEFAULT_ACTIONS.get(name)
    if actions is None or not isinstance(action, str):
        raise OperationPermissionError()
    action = action.strip().lower()
    if name == "manage_ui" and action == "patch_source":
        # The SDK and native handler both require a JSON boolean. Only an
        # omitted flag defaults to preview; explicit null is malformed.
        preview = params.get("preview", True)
        if type(preview) is not bool:
            raise OperationPermissionError()
        return frozenset({Permission.READ if preview else Permission.EDIT})
    if name == "manage_gameobject" and action == "placement":
        # Match public and native placement exactly, including native batch
        # members: null/missing previews, and canonical false explicitly applies.
        preview = params.get("preview")
        if (
            preview is not None
            and type(preview) is not bool
            and (not isinstance(preview, str) or preview.lower() not in {"true", "false"})
        ):
            raise OperationPermissionError()
        applies = preview is False or isinstance(preview, str) and preview.lower() == "false"
        return frozenset({Permission.EDIT if applies else Permission.READ})
    if name == "manage_ugui" and action == "diagnose_raycast":
        # The default native query refuses applicable custom callbacks. Opting
        # in can run arbitrary user code, including writes and event dispatch.
        allow_custom_filters = params.get("allow_custom_filters", False)
        if type(allow_custom_filters) is not bool:
            raise OperationPermissionError()
        if allow_custom_filters:
            return CUSTOM_GRANTS | {Permission.READ}
        return frozenset({Permission.READ})
    if name == "script_change_set" and action in {"apply", "recover"}:
        # Final-source changes can compile on automatic/deferred import even
        # when this wrapper does not explicitly request a refresh.
        return CUSTOM_GRANTS
    if name == "manage_script" and action in _COMPILING_SCRIPT_ACTIONS:
        # Native mutation paths compile/import-compile, including deferred
        # refresh. Public create/delete also unconditionally await readiness.
        if not native and action in {"create", "delete"}:
            return CUSTOM_GRANTS | {Permission.READ}
        return CUSTOM_GRANTS
    if (
        name == "manage_scene"
        and action == "validate"
        and any(
            params.get(key) is not None and params.get(key) is not False
            for key in ("auto_repair", "autoRepair")
        )
    ):
        # Native coercion also accepts string/numeric flags. Any supplied value
        # other than explicit false/null is conservatively treated as repair.
        return frozenset({Permission.READ, Permission.EDIT})
    # Generation starts provider work and imports its result into the project.
    # A run grant alone cannot authorize those asset writes.
    if name in {"generate_audio", "generate_image", "generate_model"} and action == "generate":
        return CUSTOM_GRANTS
    # These operations can persist captures under Assets or modify imported
    # content after executing Blender work. Admit the complete operation first.
    if name == "blender_bridge" and action in {"screenshot", "compare_screenshot", "import_model"}:
        return CUSTOM_GRANTS
    if name == "manage_scene" and action == "screenshot":
        return CUSTOM_GRANTS
    if name == "manage_camera" and action in {"screenshot", "screenshot_multiview"}:
        return CUSTOM_GRANTS
    if name == "manage_ui" and action == "render_ui":
        return CUSTOM_GRANTS
    for permission, names in actions.items():
        if action in names:
            return frozenset({permission})
    raise OperationPermissionError()


def authorize_operation(
    authority: OperationGrants,
    name: str,
    params: Mapping[str, JsonValue],
    *,
    custom: bool = False,
    native: bool = False,
) -> None:
    """Legacy missing claims preserve behavior; explicit authorities use the closed policy."""
    if authority.scoped:
        if native and not custom and name not in _NATIVE_COMMANDS:
            raise OperationPermissionError()
        if (
            not required_permissions(name, params, custom=custom, native=native)
            <= authority.permissions
        ):
            raise OperationPermissionError()


def authorize_resource(authority: OperationGrants, uri: str | None = None) -> None:
    """Resource reads/listings/subscriptions require read and an exact supported route."""
    # This static Apps route is exact even for legacy unscoped credentials.
    if isinstance(uri, str) and uri.startswith("ui:"):
        if uri != "ui://unity-mcp/test-results-v1":
            raise OperationPermissionError()
        if authority.scoped and Permission.READ not in authority.permissions:
            raise OperationPermissionError()
        return
    if not authority.scoped:
        return
    if Permission.READ not in authority.permissions:
        raise OperationPermissionError()
    if uri is None:
        return
    if not isinstance(uri, str) or len(uri) > 4096:
        raise OperationPermissionError()
    try:
        parts = urlsplit(uri)
    except ValueError as error:
        raise OperationPermissionError() from error
    route = parts.netloc + parts.path
    if (
        parts.scheme != "mcpforunity"
        or parts.fragment
        or route not in _RESOURCE_PATHS
        and not any(pattern.fullmatch(route) for pattern in _RESOURCE_PATTERNS)
    ):
        raise OperationPermissionError()


async def authorize_remote_operation(
    name: str, params: Mapping[str, JsonValue], *, custom: bool = False, ctx=None
) -> None:
    """Admit requester and any already selected authenticated plugin before optional work."""
    from core.config import config
    from services.credential_lease import CredentialLeaseError, require_credential_lease

    if not config.http_remote_hosted:
        return
    lease = await require_credential_lease()
    if name == "get_execution_receipt":
        # This server-only control operation authorizes the stored policy floor
        # in its handler, before and after I/O. It is not an unconditional read.
        return
    authorize_operation(lease.permissions, name, params, custom=custom)
    if ctx is not None and callable(getattr(ctx, "get_state", None)):
        from transport.plugin_hub import PluginHub

        session_id = await ctx.get_state("unity_session_id")
        websocket = PluginHub._connections.get(session_id) if isinstance(session_id, str) else None
        if websocket is not None:
            plugin = PluginHub._socket_lease(websocket)
            if plugin is None or plugin.user_id != lease.user_id:
                raise CredentialLeaseError()
            await plugin.check()
            authorize_operation(plugin.permissions, name, params, custom=custom)


async def remote_operation_allowed(name: str, params: Mapping[str, JsonValue], *, ctx=None) -> bool:
    """Only optional work may use this safe-fallback query; authentication errors propagate."""
    try:
        await authorize_remote_operation(name, params, ctx=ctx)
    except OperationPermissionError:
        return False
    return True


async def authorize_remote_resource(uri: str | None = None) -> None:
    """Fence the requester before selecting a project or invoking a resource handler."""
    from core.config import config
    from services.credential_lease import require_credential_lease

    if config.http_remote_hosted:
        lease = await require_credential_lease()
        authorize_resource(lease.permissions, uri)
