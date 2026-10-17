---
title: "core tools"
sidebar_label: "core"
description: "MCP for Unity tools in the core group."
---

# `core` tools

Essential scene, script, asset, editor & view recording tools (always on by default)

- **[`apply_text_edits`](./apply_text_edits.md)** — Apply small text edits to a C# script identified by URI.
- **[`batch_execute`](./batch_execute.md)** — Executes multiple MCP commands in a single batch for dramatically better performance.
- **[`create_script`](./create_script.md)** — Create a new C# script at the given project path.
- **[`debug_request_context`](./debug_request_context.md)** — Return the current FastMCP request context details (client_id, session_id, and meta dump).
- **[`delete_script`](./delete_script.md)** — Delete a C# script by URI or Assets-relative path.
- **[`execute_custom_tool`](./execute_custom_tool.md)** — Execute a project-scoped custom tool registered by Unity.
- **[`execute_menu_item`](./execute_menu_item.md)** — Inspect menu existence/availability evidence or invoke a menu by exact path.
- **[`find_gameobjects`](./find_gameobjects.md)** — Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded co…
- **[`find_in_file`](./find_in_file.md)** — Searches a C# script with a regex pattern and returns line numbers and excerpts.
- **[`get_compile_job`](./get_compile_job.md)** — Read a compilation request outcome and bounded diagnostics by compile_id.
- **[`get_sha`](./get_sha.md)** — Get SHA256 and basic metadata for a Unity C# script without returning file contents.
- **[`manage_asset`](./manage_asset.md)** — Performs asset operations (import, create, modify, delete, etc.) in Unity.
- **[`manage_audio`](./manage_audio.md)** — Play or stop an existing scene AudioSource in Play mode.
- **[`manage_build`](./manage_build.md)** — Manage Unity player builds — trigger builds, switch platforms, configure settings, manage build scenes and profiles, run batch builds across platforms.
- **[`manage_camera`](./manage_camera.md)** — Manage cameras (Unity Camera + Cinemachine).
- **[`manage_components`](./manage_components.md)** — Add, remove, or set properties on components attached to GameObjects.
- **[`manage_editor`](./manage_editor.md)** — Controls Unity editor state and settings. play optionally waits for scene_loaded or first_frame with a bounded persisted job. first_frame confirms simulation frame progress, not rendered pixels or application async initialization. get_pl…
- **[`manage_gameobject`](./manage_gameobject.md)** — Performs CRUD operations on GameObjects.
- **[`manage_graphics`](./manage_graphics.md)** — Manage rendering graphics: volumes, post-processing, light baking, rendering stats, pipeline settings, and URP renderer features.
- **[`manage_material`](./manage_material.md)** — Manages Unity materials (set properties, colors, shaders, etc).
- **[`manage_object_handles`](./manage_object_handles.md)** — Capture or resolve the original loaded GameObject/Component reference for this Editor session.
- **[`manage_packages`](./manage_packages.md)** — Manage Unity packages: query, install, remove, embed, and configure registries.
- **[`manage_physics`](./manage_physics.md)** — Manage physics settings, collision matrix, materials, joints, queries, and validation.
- **[`manage_prefabs`](./manage_prefabs.md)** — Manages Unity Prefab assets. get_hierarchy returns bounded sibling-index depth-first pages: page_size defaults to 50 (1-500), max_depth defaults to 64 (0-128, root depth 0), and cursor continues the identical revision/options.
- **[`manage_recording`](./manage_recording.md)** — Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. start returns job_id immediately; poll status explicitly or stop to finalize.
- **[`manage_scene`](./manage_scene.md)** — Performs CRUD operations on Unity scenes.
- **[`manage_scene_edits`](./manage_scene_edits.md)** — Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, or Rigidbody serialized property edits in a stable Edit-mode scene.
- **[`manage_script`](./manage_script.md)** — Compatibility router for legacy script operations.
- **[`manage_script_capabilities`](./manage_script_capabilities.md)** — Get manage_script capabilities (supported ops, limits, and guards).
- **[`manage_tools`](./manage_tools.md)** — Manage which tool groups are visible in this session.
- **[`read_console`](./read_console.md)** — Gets messages from or clears the Unity Editor console.
- **[`refresh_unity`](./refresh_unity.md)** — Request a Unity asset database refresh and optionally a script compilation.
- **[`script_apply_edits`](./script_apply_edits.md)** — Structured C# edits (methods/classes) with safer boundaries - prefer this over raw text.
- **[`set_active_instance`](./set_active_instance.md)** — Set the active Unity instance for this client/session.
- **[`validate_script`](./validate_script.md)** — Validate a C# script and return diagnostics.
