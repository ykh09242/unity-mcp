---
title: Tool reference
sidebar_label: Tools
sidebar_class_name: sidebar-hidden
slug: /reference/tools
description: Auto-generated catalog of every MCP for Unity tool, grouped by domain.
---

# Tool reference

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

Every tool MCP for Unity exposes, generated directly from the Python `@mcp_for_unity_tool` registry under `Server/src/services/tools/`.

## `animation` &nbsp; (4 tools)
Animator control, AnimationClip creation & 2D sprite-sheet animation
- **[`manage_animation`](./animation/manage_animation.md)** — Manage Unity animation: Animator control and AnimationClip creation.
- **[`manage_sprite`](./animation/manage_sprite.md)** — Slice 2D sprite sheets and build AnimationClips and an AnimatorController from the frames.
- **[`manage_sprite_atlas`](./animation/manage_sprite_atlas.md)** — Inspect and explicitly author SpriteAtlas V2 Assets/*.spriteatlasv2 files.
- **[`manage_timeline`](./animation/manage_timeline.md)** — Inspect/create optional Timeline Assets/*.playable assets and explicitly add built-in animation/audio tracks and clips.

## `asset_gen` &nbsp; (6 tools)
AI asset generation – 3D model gen/import, 2D image gen & audio gen (bring-your-own-key)
- **[`blender_bridge`](./asset_gen/blender_bridge.md)** — Bridge to a running Blender that has the BlenderMCP addon connected (socket, default 127.0.0.1:9876; configured in Window > Unity MCP (ykh09242) > Generative > Blender Bridge).
- **[`generate_audio`](./asset_gen/generate_audio.md)** — Generate audio (sound effects and background music) with fal.ai models and import them as AudioClips into the Unity project.
- **[`generate_image`](./asset_gen/generate_image.md)** — Generate 2D images with AI providers (fal.ai, OpenRouter) and import them as textures/sprites into the Unity project.
- **[`generate_model`](./asset_gen/generate_model.md)** — Generate 3D models with AI providers (Tripo, Meshy, fal) and import them into the Unity project.
- **[`import_model`](./asset_gen/import_model.md)** — Import 3D models from the Sketchfab marketplace into the Unity project.
- **[`import_model_file`](./asset_gen/import_model_file.md)** — Import a local 3D model file that already exists within the Unity project's Assets folder (e.g. an FBX/OBJ/glTF exported from Blender or another DCC tool) into the Unity project.

## `assets` &nbsp; (5 tools)
Asset changes, labels, bundles and reference diagnostics
- **[`asset_bundles`](./assets/asset_bundles.md)** — Inspect asset bundles, their contents and dependencies.
- **[`asset_edit`](./assets/asset_edit.md)** — Create, import, modify, duplicate, move or delete assets and folders.
- **[`asset_labels`](./assets/asset_labels.md)** — Read or change asset labels with revision checks.
- **[`asset_reference_diagnostics`](./assets/asset_reference_diagnostics.md)** — Inspect dependencies and reverse references, or preview asset moves and deletion.
- **[`manage_reference_graph`](./assets/manage_reference_graph.md)** — Maintain an automatic asset/code/external-config reference graph for the selected Unity instance and authenticated user.

## `camera` &nbsp; (3 tools)
Camera setup, Cinemachine and view capture
- **[`capture_camera`](./camera/capture_camera.md)** — Capture Game or Scene views, including surround and orbit batches.
- **[`manage_cameras`](./camera/manage_cameras.md)** — Create or list cameras, select targets and configure lenses or priority.
- **[`manage_cinemachine`](./camera/manage_cinemachine.md)** — Configure Cinemachine brains, tracking, extensions and camera blends.

## `core` &nbsp; (42 tools)
Essential scene, script, asset, editor & view recording tools (always on by default)
- **[`apply_text_edits`](./core/apply_text_edits.md)** — Apply small text edits to a C# script identified by URI.
- **[`asset_inspect`](./core/asset_inspect.md)** — Search assets or inspect their metadata and components.
- **[`batch_execute`](./core/batch_execute.md)** — Execute multiple native MCP commands in one request.
- **[`create_script`](./core/create_script.md)** — Create a new C# script at the given project path.
- **[`debug_request_context`](./core/debug_request_context.md)** — Return the current FastMCP request context details (client_id, session_id, and meta dump).
- **[`delete_script`](./core/delete_script.md)** — Delete a C# script by URI or Assets-relative path.
- **[`execute_custom_tool`](./core/execute_custom_tool.md)** — Execute a project-scoped custom tool registered by Unity.
- **[`execute_menu_item`](./core/execute_menu_item.md)** — Inspect menu existence/availability evidence or invoke a menu by exact path.
- **[`find_gameobjects`](./core/find_gameobjects.md)** — Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded co…
- **[`find_in_file`](./core/find_in_file.md)** — Searches a C# script with a regex pattern and returns line numbers and excerpts.
- **[`gameobject_create`](./core/gameobject_create.md)** — Create a GameObject with transform, components and optional prefab output.
- **[`gameobject_edit`](./core/gameobject_edit.md)** — Modify or delete an existing GameObject and its components.
- **[`get_compile_job`](./core/get_compile_job.md)** — Read a compilation request outcome and bounded diagnostics by compile_id.
- **[`get_sha`](./core/get_sha.md)** — Get SHA256 and basic metadata for a Unity C# script without returning file contents.
- **[`manage_addressables`](./core/manage_addressables.md)** — Inspect existing optional Addressables settings (groups, explicit entries and raw profiles), run bounded preflight for duplicate addresses/missing references/profile issues, or analyze an existing native build-layout report for duplicate…
- **[`manage_audio`](./core/manage_audio.md)** — Diagnose observed scene audio state without proving output-device audibility; inspect or explicitly set existing mixer-group routing; read/apply per-platform AudioImporter policies.
- **[`manage_build`](./core/manage_build.md)** — Manage Unity player builds — trigger builds, switch platforms, configure settings, manage build scenes and profiles, run batch builds across platforms.
- **[`manage_components`](./core/manage_components.md)** — Add, remove, or set properties on components attached to GameObjects.
- **[`manage_editor`](./core/manage_editor.md)** — Controls Unity editor state and settings.
- **[`manage_input_actions`](./core/manage_input_actions.md)** — Author optional Input System1.7+ .inputactions assets in idle Edit Mode. capabilities/read are bounded reads; read returns exact map/action/binding GUIDs and a raw source revision. create_asset requires an unoccupied Assets path and exis…
- **[`manage_localization`](./core/manage_localization.md)** — Inspect optional Unity Localization locale/table identities, shared keys, missing translations, static Smart String selectors and asset GUID references.
- **[`manage_material`](./core/manage_material.md)** — Manages Unity materials (properties, colors, shaders, render state).
- **[`manage_navigation`](./core/manage_navigation.md)** — Read and explicitly author navigation surfaces, agents, obstacles and links.
- **[`manage_netcode`](./core/manage_netcode.md)** — Inspect local Netcode for GameObjects configuration without starting a network session. capabilities reports optional package detection/version; preflight requires an explicit loaded scene_handle and checks managers, active same-scene tr…
- **[`manage_object_handles`](./core/manage_object_handles.md)** — Capture or resolve the original loaded GameObject/Component reference for this Editor session.
- **[`manage_packages`](./core/manage_packages.md)** — Manage Unity packages: query, install, remove, embed, and configure registries.
- **[`manage_recording`](./core/manage_recording.md)** — Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. start returns job_id immediately; poll status explicitly or stop to finalize.
- **[`manage_scene_edits`](./core/manage_scene_edits.md)** — Preview or apply 1-1000 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene.
- **[`manage_script`](./core/manage_script.md)** — Compatibility router for legacy script operations.
- **[`manage_script_capabilities`](./core/manage_script_capabilities.md)** — Get manage_script capabilities (supported ops, limits, and guards).
- **[`manage_terrain`](./core/manage_terrain.md)** — Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays.
- **[`manage_tools`](./core/manage_tools.md)** — Manage visible groups or individual feature tools in this session.
- **[`prefab_inspect`](./core/prefab_inspect.md)** — Inspect prefab metadata or a bounded hierarchy page.
- **[`read_console`](./core/read_console.md)** — Gets messages from or clears the Unity Editor console.
- **[`refresh_unity`](./core/refresh_unity.md)** — Request a Unity asset database refresh and optionally a script compilation.
- **[`scene_inspect`](./core/scene_inspect.md)** — Inspect loaded scenes, hierarchy and build settings, or frame the Scene view.
- **[`scene_lifecycle`](./core/scene_lifecycle.md)** — Create, load, save or close scenes and organize loaded scene objects.
- **[`script_apply_edits`](./core/script_apply_edits.md)** — Edit C# source using operations scoped to the intended members or text.
- **[`script_change_set`](./core/script_change_set.md)** — Prepare, apply, inspect or recover 1–256 existing C# source replacements as one bounded change set.
- **[`set_active_instance`](./core/set_active_instance.md)** — Set the active Unity instance for this client/session.
- **[`validate_script`](./core/validate_script.md)** — Validate a C# script and report diagnostics, the effective validation engine, and context limitations.
- **[`wait_for_operation`](./core/wait_for_operation.md)** — Observe an existing build, package query/mutation/resolve, or single scenario job.

## `docs` &nbsp; (2 tools)
Unity API reflection and documentation lookup
- **[`unity_docs`](./docs/unity_docs.md)** — Fetch official Unity documentation from docs.unity3d.com.
- **[`unity_reflect`](./docs/unity_reflect.md)** — Inspect Unity's live C# API via reflection.

## `graphics` &nbsp; (8 tools)
Rendering, lighting, volumes and graphics diagnostics
- **[`manage_light_baking`](./graphics/manage_light_baking.md)** — Control light baking and configure light or reflection probes.
- **[`manage_render_environment`](./graphics/manage_render_environment.md)** — Inspect the environment or configure skybox, ambient light and sun.
- **[`manage_render_fog`](./graphics/manage_render_fog.md)** — Enable or configure scene fog.
- **[`manage_render_pipeline`](./graphics/manage_render_pipeline.md)** — Inspect the render pipeline or change quality and pipeline settings.
- **[`manage_render_reflections`](./graphics/manage_render_reflections.md)** — Configure environment reflection mode, intensity, bounces and resolution.
- **[`manage_render_volumes`](./graphics/manage_render_volumes.md)** — Create volumes and profiles, then inspect or configure post-processing effects.
- **[`manage_renderer_features`](./graphics/manage_renderer_features.md)** — Add, configure, reorder or remove URP renderer features.
- **[`manage_rendering_stats`](./graphics/manage_rendering_stats.md)** — Inspect rendering counters and memory, or select a scene debug view.

## `legacy` &nbsp; (8 tools)
Compatibility: full combined schemas for existing clients; prefer focused tools
- **[`manage_asset`](./legacy/manage_asset.md)** — Performs asset operations (import, create, modify, delete, etc.) in Unity.
- **[`manage_camera`](./legacy/manage_camera.md)** — Manage cameras (Unity Camera + Cinemachine).
- **[`manage_gameobject`](./legacy/manage_gameobject.md)** — Performs CRUD operations on GameObjects.
- **[`manage_graphics`](./legacy/manage_graphics.md)** — Manage rendering graphics: volumes, post-processing, light baking, rendering stats, pipeline settings, and URP renderer features.
- **[`manage_physics`](./legacy/manage_physics.md)** — Manage physics settings, collision matrix, materials, joints, queries, and validation.
- **[`manage_prefabs`](./legacy/manage_prefabs.md)** — Manages Unity Prefab assets. get_hierarchy returns bounded sibling-index depth-first pages: page_size defaults to 50 (1-500), max_depth defaults to 64 (0-128, root depth 0), and cursor continues the identical revision/options.
- **[`manage_scene`](./legacy/manage_scene.md)** — Performs CRUD operations on Unity scenes.
- **[`manage_ui`](./legacy/manage_ui.md)** — Manages Unity UI Toolkit elements (UXML, USS, UIDocument and Unity 6.5+ PanelRenderer components).

## `physics` &nbsp; (8 tools)
2D and 3D physics settings, queries and simulation
- **[`apply_physics_force`](./physics/apply_physics_force.md)** — Apply forces, torque or explosions to rigidbodies.
- **[`diagnose_physics`](./physics/diagnose_physics.md)** — Validate physics setup or inspect a pair of colliders and passive contacts.
- **[`manage_physics_joints`](./physics/manage_physics_joints.md)** — Add, configure or remove 2D/3D joints.
- **[`manage_physics_materials`](./physics/manage_physics_materials.md)** — Create, configure and assign physics materials.
- **[`manage_physics_settings`](./physics/manage_physics_settings.md)** — Inspect or change 2D/3D physics settings and layer collision rules.
- **[`manage_rigidbody`](./physics/manage_rigidbody.md)** — Inspect or configure a Rigidbody or Rigidbody2D.
- **[`query_physics`](./physics/query_physics.md)** — Run ray, shape and overlap queries in 2D or 3D.
- **[`simulate_physics`](./physics/simulate_physics.md)** — Advance physics by a bounded number of explicit simulation steps.

## `prefabs` &nbsp; (4 tools)
Prefab creation, contents, stages and overrides
- **[`prefab_contents`](./prefabs/prefab_contents.md)** — Edit prefab contents, inspect changes without saving with modify_contents preview_only, or preview and apply a guarded transform change.
- **[`prefab_create`](./prefabs/prefab_create.md)** — Create a prefab asset from an existing GameObject.
- **[`prefab_overrides`](./prefabs/prefab_overrides.md)** — List, apply or revert explicitly selected prefab overrides.
- **[`prefab_stage`](./prefabs/prefab_stage.md)** — Open, save or close the Prefab Stage.

## `probuilder` &nbsp; (1 tool)
ProBuilder 3D modeling – requires com.unity.probuilder package
- **[`manage_probuilder`](./probuilder/manage_probuilder.md)** — Manage ProBuilder meshes for in-editor 3D modeling.

## `profiling` &nbsp; (1 tool)
Unity Profiler session control, counters, memory snapshots & Frame Debugger
- **[`manage_profiler`](./profiling/manage_profiler.md)** — Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger.

## `scene` &nbsp; (5 tools)
GameObject positioning, placement and scene diagnostics
- **[`gameobject_duplicate`](./scene/gameobject_duplicate.md)** — Duplicate a GameObject with optional name, parent and position changes.
- **[`gameobject_placement`](./scene/gameobject_placement.md)** — Preview or apply alignment and distribution of selected GameObjects.
- **[`gameobject_positioning`](./scene/gameobject_positioning.md)** — Move or orient a GameObject selected by target (or name). move_relative requires reference_object plus direction or offset; look_at requires look_at_target.
- **[`scene_reference_diagnostics`](./scene/scene_reference_diagnostics.md)** — Inspect scene references within explicit work and result budgets.
- **[`scene_validate`](./scene/scene_validate.md)** — Validate the scene, with optional explicit repair.

## `scripting_ext` &nbsp; (3 tools)
ScriptableObject management
- **[`execute_code`](./scripting_ext/execute_code.md)** — Execute arbitrary C# code inside the Unity Editor.
- **[`inspect_serialized_object`](./scripting_ext/inspect_serialized_object.md)** — Inspect one exact Component or ScriptableObject instance ID through SerializedObject.
- **[`manage_scriptable_object`](./scripting_ext/manage_scriptable_object.md)** — Creates and modifies ScriptableObject assets using Unity SerializedObject property paths.

## `testing` &nbsp; (10 tools)
Test runner, async test jobs & bounded Play Mode input simulation
- **[`cancel_test_job`](./testing/cancel_test_job.md)** — Request cancellation of an owned native test job.
- **[`export_test_job`](./testing/export_test_job.md)** — Export completed Unity test evidence as JSON or JUnit.
- **[`get_test_job`](./testing/get_test_job.md)** — Polls an async Unity test job by job_id.
- **[`get_tests`](./testing/get_tests.md)** — Lists Unity tests without running them.
- **[`manage_input`](./testing/manage_input.md)** — Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycas…
- **[`manage_play_scenario`](./testing/manage_play_scenario.md)** — Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs.
- **[`manage_scenario_recording`](./testing/manage_scenario_recording.md)** — Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode.
- **[`manage_simulation_profiles`](./testing/manage_simulation_profiles.md)** — Capture named Time/Physics3D/Physics2D/Audio/Input configuration profiles, inspect per-domain applicability, diff/validate against current settings, explicitly apply, and conditionally restore confirmed changes. capabilities lists exact…
- **[`run_tests`](./testing/run_tests.md)** — Starts a Unity test run asynchronously and returns a job_id immediately.
- **[`view_test_results`](./testing/view_test_results.md)** — Read one completed Unity test-result page (up to 100 failed/skipped details).

## `ui` &nbsp; (7 tools)
UI Toolkit (UXML, USS, UIDocument, optional PanelRenderer) and Canvas-based uGUI editing and diagnostics
- **[`capture_ui_snapshot`](./ui/capture_ui_snapshot.md)** — Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit.
- **[`manage_ugui`](./ui/manage_ugui.md)** — Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings.
- **[`ui_capture`](./ui/ui_capture.md)** — Start, poll or cancel a bounded UI Toolkit render capture.
- **[`ui_document`](./ui/ui_document.md)** — Attach UI documents and configure Panel Settings.
- **[`ui_element`](./ui/ui_element.md)** — Change a visual element's text, classes, style or state.
- **[`ui_inspect`](./ui/ui_inspect.md)** — Inspect the visual tree or diagnose UI Toolkit layout and text.
- **[`ui_source`](./ui/ui_source.md)** — Create, read or edit UXML/USS assets, link stylesheets or preview source patches.

## `vfx` &nbsp; (3 tools)
Visual effects – VFX Graph, shaders, procedural textures
- **[`manage_shader`](./vfx/manage_shader.md)** — Manages shader scripts in Unity (create, read, update, delete).
- **[`manage_texture`](./vfx/manage_texture.md)** — Procedural texture generation for Unity.
- **[`manage_vfx`](./vfx/manage_vfx.md)** — Manage Unity VFX components (ParticleSystem, VisualEffect, LineRenderer, TrailRenderer).

