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

## `core` &nbsp; (42 tools)
Essential scene, script, asset, editor & view recording tools (always on by default)
- **[`apply_text_edits`](./core/apply_text_edits.md)** — Apply small text edits to a C# script identified by URI.
- **[`batch_execute`](./core/batch_execute.md)** — Executes multiple MCP commands in a single batch for dramatically better performance.
- **[`create_script`](./core/create_script.md)** — Create a new C# script at the given project path.
- **[`debug_request_context`](./core/debug_request_context.md)** — Return the current FastMCP request context details (client_id, session_id, and meta dump).
- **[`delete_script`](./core/delete_script.md)** — Delete a C# script by URI or Assets-relative path.
- **[`execute_custom_tool`](./core/execute_custom_tool.md)** — Execute a project-scoped custom tool registered by Unity.
- **[`execute_menu_item`](./core/execute_menu_item.md)** — Inspect menu existence/availability evidence or invoke a menu by exact path.
- **[`find_gameobjects`](./core/find_gameobjects.md)** — Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded co…
- **[`find_in_file`](./core/find_in_file.md)** — Searches a C# script with a regex pattern and returns line numbers and excerpts.
- **[`get_compile_job`](./core/get_compile_job.md)** — Read a compilation request outcome and bounded diagnostics by compile_id.
- **[`get_sha`](./core/get_sha.md)** — Get SHA256 and basic metadata for a Unity C# script without returning file contents.
- **[`manage_addressables`](./core/manage_addressables.md)** — Inspect existing optional Addressables settings (groups, explicit entries and raw profiles), run bounded preflight for duplicate addresses/missing references/profile issues, or analyze an existing2.7.6 native build-layout report for dupl…
- **[`manage_asset`](./core/manage_asset.md)** — Performs asset operations (import, create, modify, delete, etc.) in Unity.
- **[`manage_audio`](./core/manage_audio.md)** — Diagnose observed scene audio state without proving output-device audibility; inspect or explicitly set existing mixer-group routing; read/apply per-platform AudioImporter policies.
- **[`manage_build`](./core/manage_build.md)** — Manage Unity player builds — trigger builds, switch platforms, configure settings, manage build scenes and profiles, run batch builds across platforms.
- **[`manage_camera`](./core/manage_camera.md)** — Manage cameras (Unity Camera + Cinemachine).
- **[`manage_components`](./core/manage_components.md)** — Add, remove, or set properties on components attached to GameObjects.
- **[`manage_editor`](./core/manage_editor.md)** — Controls Unity editor state and settings. play optionally waits for scene_loaded or first_frame with a bounded persisted job. first_frame confirms simulation frame progress, not rendered pixels or application async initialization. get_pl…
- **[`manage_gameobject`](./core/manage_gameobject.md)** — Performs CRUD operations on GameObjects.
- **[`manage_graphics`](./core/manage_graphics.md)** — Manage rendering graphics: volumes, post-processing, light baking, rendering stats, pipeline settings, and URP renderer features.
- **[`manage_input_actions`](./core/manage_input_actions.md)** — Author optional Input System1.7+ .inputactions assets in idle Edit Mode. capabilities/read are bounded reads; read returns exact map/action/binding GUIDs and a raw source revision. create_asset requires an unoccupied Assets path and exis…
- **[`manage_localization`](./core/manage_localization.md)** — Inspect optional Unity Localization locale/table identities, shared keys, missing translations, static Smart String selectors and asset GUID references.
- **[`manage_material`](./core/manage_material.md)** — Manages Unity materials (properties, colors, shaders, render state).
- **[`manage_navigation`](./core/manage_navigation.md)** — Read and explicitly author navigation surfaces, agents, obstacles and links.
- **[`manage_netcode`](./core/manage_netcode.md)** — Inspect local Netcode for GameObjects configuration without starting a network session. capabilities reports optional package detection/version; preflight requires an explicit loaded scene_handle and checks managers, active same-scene tr…
- **[`manage_object_handles`](./core/manage_object_handles.md)** — Capture or resolve the original loaded GameObject/Component reference for this Editor session.
- **[`manage_packages`](./core/manage_packages.md)** — Manage Unity packages: query, install, remove, embed, and configure registries.
- **[`manage_physics`](./core/manage_physics.md)** — Manage physics settings, collision matrix, materials, joints, queries, and validation.
- **[`manage_prefabs`](./core/manage_prefabs.md)** — Manages Unity Prefab assets. get_hierarchy returns bounded sibling-index depth-first pages: page_size defaults to 50 (1-500), max_depth defaults to 64 (0-128, root depth 0), and cursor continues the identical revision/options.
- **[`manage_recording`](./core/manage_recording.md)** — Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. start returns job_id immediately; poll status explicitly or stop to finalize.
- **[`manage_scene`](./core/manage_scene.md)** — Performs CRUD operations on Unity scenes.
- **[`manage_scene_edits`](./core/manage_scene_edits.md)** — Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene.
- **[`manage_script`](./core/manage_script.md)** — Compatibility router for legacy script operations.
- **[`manage_script_capabilities`](./core/manage_script_capabilities.md)** — Get manage_script capabilities (supported ops, limits, and guards).
- **[`manage_terrain`](./core/manage_terrain.md)** — Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays.
- **[`manage_tools`](./core/manage_tools.md)** — Manage which tool groups are visible in this session.
- **[`read_console`](./core/read_console.md)** — Gets messages from or clears the Unity Editor console.
- **[`refresh_unity`](./core/refresh_unity.md)** — Request a Unity asset database refresh and optionally a script compilation.
- **[`script_apply_edits`](./core/script_apply_edits.md)** — Structured C# edits (methods/classes) with safer boundaries - prefer this over raw text.
- **[`script_change_set`](./core/script_change_set.md)** — Prepare, apply, inspect or recover 1–16 existing C# source replacements as one bounded change set.
- **[`set_active_instance`](./core/set_active_instance.md)** — Set the active Unity instance for this client/session.
- **[`validate_script`](./core/validate_script.md)** — Validate a C# script and report diagnostics, the effective validation engine, and context limitations.

## `docs` &nbsp; (2 tools)
Unity API reflection and documentation lookup
- **[`unity_docs`](./docs/unity_docs.md)** — Fetch official Unity documentation from docs.unity3d.com.
- **[`unity_reflect`](./docs/unity_reflect.md)** — Inspect Unity's live C# API via reflection.

## `probuilder` &nbsp; (1 tool)
ProBuilder 3D modeling – requires com.unity.probuilder package
- **[`manage_probuilder`](./probuilder/manage_probuilder.md)** — Manage ProBuilder meshes for in-editor 3D modeling.

## `profiling` &nbsp; (1 tool)
Unity Profiler session control, counters, memory snapshots & Frame Debugger
- **[`manage_profiler`](./profiling/manage_profiler.md)** — Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger.

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

## `ui` &nbsp; (3 tools)
UI Toolkit (UXML, USS, UIDocument, optional PanelRenderer) and Canvas-based uGUI editing and diagnostics
- **[`capture_ui_snapshot`](./ui/capture_ui_snapshot.md)** — Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit.
- **[`manage_ugui`](./ui/manage_ugui.md)** — Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings.
- **[`manage_ui`](./ui/manage_ui.md)** — Manages Unity UI Toolkit elements (UXML, USS, UIDocument and Unity 6.5+ PanelRenderer components).

## `vfx` &nbsp; (3 tools)
Visual effects – VFX Graph, shaders, procedural textures
- **[`manage_shader`](./vfx/manage_shader.md)** — Manages shader scripts in Unity (create, read, update, delete).
- **[`manage_texture`](./vfx/manage_texture.md)** — Procedural texture generation for Unity.
- **[`manage_vfx`](./vfx/manage_vfx.md)** — Manage Unity VFX components (ParticleSystem, VisualEffect, LineRenderer, TrailRenderer).

