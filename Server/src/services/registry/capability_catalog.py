"""Feature boundaries shared by server adapters and generated Unity entrypoints."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityEffects:
    """Discovery hints only; never consulted for admission or automatic retries."""

    read_only: bool = False
    destructive: bool = True
    idempotent: bool = False
    open_world: bool = True
    description_suffix: str = ""


# Unknown/new capabilities remain conservative until every action is reviewed.
MUTATING_EFFECTS = CapabilityEffects()
READ_ONLY_EFFECTS = CapabilityEffects(
    read_only=True,
    destructive=False,
    open_world=False,
    description_suffix="Read-only inspection; results can change with Unity state.",
)
CONTACT_OBSERVER_EFFECTS = CapabilityEffects(
    open_world=False,
    description_suffix="May start or stop passive contact observation without advancing simulation.",
)


@dataclass(frozen=True)
class Capability:
    name: str
    source: str
    group: str
    title: str
    actions: tuple[str, ...]
    parameters: tuple[str, ...]
    effects: CapabilityEffects = MUTATING_EFFECTS

    @property
    def native_actions(self) -> tuple[str, ...]:
        if self.name == "asset_inspect":
            return (*self.actions, "search_metadata_v1")
        return self.actions

    @property
    def description(self) -> str:
        return " ".join(
            filter(None, (CAPABILITY_DESCRIPTIONS[self.name], self.effects.description_suffix))
        )


def _feature(name, source, group, title, actions, parameters, *, effects=MUTATING_EFFECTS):
    return Capability(
        name, source, group, title, tuple(actions.split()), tuple(parameters.split()), effects
    )


CAPABILITIES: tuple[Capability, ...] = (
    _feature(
        "manage_physics_settings",
        "manage_physics",
        "physics",
        "Physics Settings & Collision Layers",
        "ping get_settings set_settings get_collision_matrix set_collision_matrix",
        "dimension settings layer_a layer_b collide",
    ),
    _feature(
        "manage_physics_materials",
        "manage_physics",
        "physics",
        "Physics Materials",
        "create_physics_material configure_physics_material assign_physics_material",
        "dimension name path dynamic_friction static_friction bounciness friction friction_combine bounce_combine properties material_path target collider_type search_method component_index",
    ),
    _feature(
        "manage_physics_joints",
        "manage_physics",
        "physics",
        "Physics Joints",
        "add_joint configure_joint remove_joint",
        "dimension target search_method joint_type connected_body motor limits spring drive properties component_index",
    ),
    _feature(
        "query_physics",
        "manage_physics",
        "physics",
        "Physics Queries",
        "raycast raycast_all linecast shapecast overlap",
        "dimension origin direction max_distance layer_mask query_trigger_interaction shape position size start end point1 point2 height capsule_direction angle",
        effects=READ_ONLY_EFFECTS,
    ),
    _feature(
        "apply_physics_force",
        "manage_physics",
        "physics",
        "Physics Forces",
        "apply_force",
        "dimension target search_method force force_mode force_type torque position explosion_position explosion_radius explosion_force upwards_modifier",
    ),
    _feature(
        "manage_rigidbody",
        "manage_physics",
        "physics",
        "Rigidbodies",
        "get_rigidbody configure_rigidbody",
        "dimension target search_method properties",
    ),
    _feature(
        "diagnose_physics",
        "manage_physics",
        "physics",
        "Physics Diagnostics",
        "validate diagnose_pair",
        "dimension target search_method page_size cursor collider_type component_index other_target other_collider_type other_component_index other_search_method contact_limit observe_contacts",
        effects=CONTACT_OBSERVER_EFFECTS,
    ),
    _feature(
        "simulate_physics",
        "manage_physics",
        "physics",
        "Physics Simulation",
        "simulate_step",
        "dimension target search_method steps step_size",
    ),
    _feature(
        "manage_render_volumes",
        "manage_graphics",
        "graphics",
        "Volumes & Post-processing",
        "volume_create volume_add_effect volume_set_effect volume_remove_effect volume_get_info volume_set_properties volume_list_effects volume_create_profile",
        "target effect parameters properties name is_global weight priority profile_path effects path",
    ),
    _feature(
        "manage_light_baking",
        "manage_graphics",
        "graphics",
        "Light Baking & Probes",
        "bake_start bake_cancel bake_status bake_clear bake_reflection_probe bake_get_settings bake_set_settings bake_create_light_probe_group bake_create_reflection_probe bake_set_probe_positions",
        "async_bake target settings name position grid_size spacing size resolution mode hdr box_projection positions",
    ),
    _feature(
        "manage_rendering_stats",
        "manage_graphics",
        "graphics",
        "Rendering Statistics",
        "stats_get stats_list_counters stats_set_scene_debug stats_get_memory",
        "category mode",
    ),
    _feature(
        "manage_render_pipeline",
        "manage_graphics",
        "graphics",
        "Render Pipeline & Quality",
        "ping pipeline_get_info pipeline_set_quality pipeline_get_settings pipeline_set_settings",
        "level settings",
    ),
    _feature(
        "manage_renderer_features",
        "manage_graphics",
        "graphics",
        "Renderer Features",
        "feature_list feature_add feature_remove feature_configure feature_toggle feature_reorder",
        "feature_type name material properties settings index active order",
    ),
    _feature(
        "manage_render_environment",
        "manage_graphics",
        "graphics",
        "Skybox & Ambient Lighting",
        "skybox_get skybox_set_material skybox_set_properties skybox_set_ambient skybox_set_sun",
        "material path properties parameters ambient_mode mode color intensity equator_color ground_color target name",
    ),
    _feature(
        "manage_render_fog",
        "manage_graphics",
        "graphics",
        "Scene Fog",
        "skybox_set_fog",
        "fog_enabled fog_mode mode fog_color color fog_density fog_start fog_end",
    ),
    _feature(
        "manage_render_reflections",
        "manage_graphics",
        "graphics",
        "Environment Reflections",
        "skybox_set_reflection",
        "reflection_mode mode intensity bounces resolution",
    ),
    _feature(
        "manage_cameras",
        "manage_camera",
        "camera",
        "Cameras & Lenses",
        "create_camera list_cameras set_target set_priority set_lens",
        "target search_method properties",
    ),
    _feature(
        "manage_cinemachine",
        "manage_camera",
        "camera",
        "Cinemachine",
        "ping ensure_brain get_brain_status set_body set_aim set_noise add_extension remove_extension set_blend force_camera release_override",
        "target search_method properties",
    ),
    _feature(
        "capture_camera",
        "manage_camera",
        "camera",
        "Camera Capture",
        "screenshot screenshot_multiview",
        "screenshot_file_name screenshot_super_size camera include_image max_resolution capture_source batch view_target view_position view_rotation orbit_angles orbit_elevations orbit_distance orbit_fov output_folder",
    ),
    _feature(
        "gameobject_create",
        "manage_gameobject",
        "core",
        "Create GameObjects",
        "create",
        "name tag layer parent position rotation scale space world_position_stays sibling_index set_active components_to_add component_properties primitive_type save_as_prefab prefab_path prefab_folder",
    ),
    _feature(
        "gameobject_edit",
        "manage_gameobject",
        "core",
        "Edit GameObjects",
        "modify delete",
        "target search_method name tag layer parent position rotation scale space world_position_stays detach sibling_index set_active is_static components_to_add components_to_remove component_properties new_name",
    ),
    _feature(
        "gameobject_duplicate",
        "manage_gameobject",
        "scene",
        "Duplicate GameObjects",
        "duplicate",
        "target search_method name new_name parent position offset",
    ),
    _feature(
        "gameobject_positioning",
        "manage_gameobject",
        "scene",
        "Relative Position & Look At",
        "move_relative look_at",
        "target search_method name reference_object direction distance offset world_space look_at_target look_at_up",
    ),
    _feature(
        "gameobject_placement",
        "manage_gameobject",
        "scene",
        "Align & Distribute GameObjects",
        "placement",
        "targets placement_mode axis space preview preview_token",
    ),
    _feature(
        "prefab_inspect",
        "manage_prefabs",
        "core",
        "Inspect Prefabs",
        "get_info get_hierarchy",
        "prefab_path page_size cursor max_depth",
        effects=READ_ONLY_EFFECTS,
    ),
    _feature(
        "prefab_create",
        "manage_prefabs",
        "prefabs",
        "Create Prefabs",
        "create_from_gameobject",
        "target name prefab_path allow_overwrite search_inactive unlink_if_instance",
    ),
    _feature(
        "prefab_contents",
        "manage_prefabs",
        "prefabs",
        "Prefab Contents & Previews",
        "modify_contents preview_modify_contents apply_preview",
        "prefab_path target position rotation scale name tag layer set_active parent components_to_add components_to_remove create_child delete_child component_properties preview_id",
    ),
    _feature(
        "prefab_stage",
        "manage_prefabs",
        "prefabs",
        "Prefab Stage",
        "open_prefab_stage save_prefab_stage close_prefab_stage",
        "prefab_path",
    ),
    _feature(
        "prefab_overrides",
        "manage_prefabs",
        "prefabs",
        "Prefab Overrides",
        "list_overrides revert_overrides apply_overrides",
        "target prefab_path override_ids object_id property_filter offset page_size",
    ),
    _feature(
        "scene_lifecycle",
        "manage_scene",
        "core",
        "Open, Save & Organize Scenes",
        "create load save close_scene set_active_scene move_to_scene",
        "name path build_index scene_name scene_path target remove_scene additive template",
    ),
    _feature(
        "scene_inspect",
        "manage_scene",
        "core",
        "Inspect Scenes & Hierarchies",
        "get_hierarchy get_active get_build_settings get_loaded_scenes scene_view_frame",
        "scene_view_target parent page_size cursor max_nodes max_depth max_children_per_node include_transform",
    ),
    _feature(
        "scene_validate", "manage_scene", "scene", "Validate Scenes", "validate", "auto_repair"
    ),
    _feature(
        "scene_reference_diagnostics",
        "manage_scene",
        "scene",
        "Scene Reference Diagnostics",
        "validate_references",
        "path reference_scope max_objects max_work page_size page_number reference_revision reference_snapshot reference_cursor",
        effects=READ_ONLY_EFFECTS,
    ),
    _feature(
        "asset_inspect",
        "manage_asset",
        "core",
        "Find & Inspect Assets",
        "search get_info get_components",
        "path asset_type generate_preview search_pattern filter_type filter_date_after page_size page_number metadata_only search_snapshot",
    ),
    _feature(
        "asset_edit",
        "manage_asset",
        "assets",
        "Create, Change & Move Assets",
        "import create modify delete duplicate move rename create_folder",
        "path asset_type properties destination scan_root max_assets max_work recursive page_size page_number reference_revision",
    ),
    _feature(
        "asset_reference_diagnostics",
        "manage_asset",
        "assets",
        "Asset References & Change Previews",
        "get_dependencies get_reverse_references preview_move preview_delete",
        "path destination scan_root max_assets max_work recursive page_size page_number reference_revision",
        effects=READ_ONLY_EFFECTS,
    ),
    _feature(
        "asset_labels",
        "manage_asset",
        "assets",
        "Asset Labels",
        "get_labels set_labels add_labels remove_labels",
        "path labels label_targets label_revision",
    ),
    _feature(
        "asset_bundles",
        "manage_asset",
        "assets",
        "Asset Bundle Inspection",
        "list_asset_bundles get_bundle_assets get_bundle_dependencies",
        "bundle_name recursive page_size page_number",
        effects=READ_ONLY_EFFECTS,
    ),
    _feature(
        "ui_source",
        "manage_ui",
        "ui",
        "UI Toolkit / UXML & USS Source",
        "create read update delete link_stylesheet list patch_source",
        "path contents stylesheet filter_type page_size page_number source_selector source_changes preview expected_source_hash",
    ),
    _feature(
        "ui_document",
        "manage_ui",
        "ui",
        "UI Toolkit / Documents & Panels",
        "attach_ui_document detach_ui_document create_panel_settings update_panel_settings",
        "target component_type source_asset panel_settings sort_order path scale_mode reference_resolution settings",
    ),
    _feature(
        "ui_inspect",
        "manage_ui",
        "ui",
        "UI Toolkit / Inspect & Diagnose",
        "ping get_visual_tree diagnose",
        "target component_type max_depth max_nodes max_evaluations max_characters max_pairs max_findings",
        effects=READ_ONLY_EFFECTS,
    ),
    _feature(
        "ui_element",
        "manage_ui",
        "ui",
        "UI Toolkit / Visual Elements",
        "modify_visual_element",
        "target component_type element_name element_path element_id strict_name text add_classes remove_classes toggle_classes style enabled visible tooltip strict_style",
    ),
    _feature(
        "ui_capture",
        "manage_ui",
        "ui",
        "UI Toolkit / Render Capture",
        "render_ui",
        "target path component_type capture_id cancel_capture warmup_updates width height include_image max_resolution screenshot_file_name output_folder",
    ),
)

LEGACY_TOOLS = frozenset(capability.source for capability in CAPABILITIES)

NATIVE_HANDLERS = {
    "manage_physics": ("MCPForUnity.Editor.Tools.Physics.ManagePhysics", False),
    "manage_graphics": ("MCPForUnity.Editor.Tools.Graphics.ManageGraphics", True),
    "manage_camera": ("MCPForUnity.Editor.Tools.Cameras.ManageCamera", False),
    "manage_gameobject": ("MCPForUnity.Editor.Tools.GameObjects.ManageGameObject", False),
    "manage_prefabs": ("MCPForUnity.Editor.Tools.Prefabs.ManagePrefabs", True),
    "manage_scene": ("MCPForUnity.Editor.Tools.ManageScene", False),
    "manage_asset": ("MCPForUnity.Editor.Tools.ManageAsset", False),
    "manage_ui": ("MCPForUnity.Editor.Tools.ManageUI", False),
}

CAPABILITY_DESCRIPTIONS = {
    "manage_physics_settings": "Inspect or change 2D/3D physics settings and layer collision rules.",
    "manage_physics_materials": "Create, configure and assign physics materials.",
    "manage_physics_joints": "Add, configure or remove 2D/3D joints.",
    "query_physics": "Run ray, shape and overlap queries in 2D or 3D.",
    "apply_physics_force": "Apply forces, torque or explosions to rigidbodies.",
    "manage_rigidbody": "Inspect or configure a Rigidbody or Rigidbody2D.",
    "diagnose_physics": "Validate physics setup or inspect a pair of colliders and passive contacts.",
    "simulate_physics": "Advance physics by a bounded number of explicit simulation steps.",
    "manage_render_volumes": "Create volumes and profiles, then inspect or configure post-processing effects.",
    "manage_light_baking": "Control light baking and configure light or reflection probes.",
    "manage_rendering_stats": "Inspect rendering counters and memory, or select a scene debug view.",
    "manage_render_pipeline": "Inspect the render pipeline or change quality and pipeline settings.",
    "manage_renderer_features": "Add, configure, reorder or remove URP renderer features.",
    "manage_render_environment": "Inspect the environment or configure skybox, ambient light and sun.",
    "manage_render_fog": "Enable or configure scene fog.",
    "manage_render_reflections": "Configure environment reflection mode, intensity, bounces and resolution.",
    "manage_cameras": "Create or list cameras, select targets and configure lenses or priority.",
    "manage_cinemachine": "Configure Cinemachine brains, tracking, extensions and camera blends.",
    "capture_camera": "Capture Game or Scene views, including surround and orbit batches.",
    "gameobject_create": "Create a GameObject with transform, components and optional prefab output.",
    "gameobject_edit": "Modify or delete an existing GameObject and its components.",
    "gameobject_duplicate": "Duplicate a GameObject with optional name, parent and position changes.",
    "gameobject_positioning": "Move a GameObject relative to a reference or orient it toward a target.",
    "gameobject_placement": "Preview or apply alignment and distribution of selected GameObjects.",
    "prefab_inspect": "Inspect prefab metadata or a bounded hierarchy page.",
    "prefab_create": "Create a prefab asset from an existing GameObject.",
    "prefab_contents": "Edit prefab contents or preview and apply a guarded transform change.",
    "prefab_stage": "Open, save or close the Prefab Stage.",
    "prefab_overrides": "List, apply or revert explicitly selected prefab overrides.",
    "scene_lifecycle": "Create, load, save or close scenes and organize loaded scene objects.",
    "scene_inspect": "Inspect loaded scenes, hierarchy and build settings, or frame the Scene view.",
    "scene_validate": "Validate the scene, with optional explicit repair.",
    "scene_reference_diagnostics": "Inspect scene references within explicit work and result budgets.",
    "asset_inspect": "Search assets or inspect their metadata and components.",
    "asset_edit": "Create, import, modify, duplicate, move or delete assets and folders.",
    "asset_reference_diagnostics": "Inspect dependencies and reverse references, or preview asset moves and deletion.",
    "asset_labels": "Read or change asset labels with revision checks.",
    "asset_bundles": "Inspect asset bundles, their contents and dependencies.",
    "ui_source": "Create, read or edit UXML/USS assets, link stylesheets or preview source patches.",
    "ui_document": "Attach UI documents and configure Panel Settings.",
    "ui_inspect": "Inspect the visual tree or diagnose UI Toolkit layout and text.",
    "ui_element": "Change a visual element's text, classes, style or state.",
    "ui_capture": "Start, poll or cancel a bounded UI Toolkit render capture.",
}
