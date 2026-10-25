---
title: "core tools"
sidebar_label: "core"
description: "MCP for Unity tools in the core group."
---

# `core` tools

Essential scene, script, asset, editor & view recording tools (always on by default)

- **[`apply_text_edits`](./apply_text_edits.md)** — Apply small text edits to a C# script identified by URI.
- **[`asset_inspect`](./asset_inspect.md)** — Search assets or inspect their metadata and components.
- **[`batch_execute`](./batch_execute.md)** — Executes multiple MCP commands in a single batch for dramatically better performance.
- **[`create_script`](./create_script.md)** — Create a new C# script at the given project path.
- **[`debug_request_context`](./debug_request_context.md)** — Return the current FastMCP request context details (client_id, session_id, and meta dump).
- **[`delete_script`](./delete_script.md)** — Delete a C# script by URI or Assets-relative path.
- **[`execute_custom_tool`](./execute_custom_tool.md)** — Execute a project-scoped custom tool registered by Unity.
- **[`execute_menu_item`](./execute_menu_item.md)** — Inspect menu existence/availability evidence or invoke a menu by exact path.
- **[`find_gameobjects`](./find_gameobjects.md)** — Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded co…
- **[`find_in_file`](./find_in_file.md)** — Searches a C# script with a regex pattern and returns line numbers and excerpts.
- **[`gameobject_create`](./gameobject_create.md)** — Create a GameObject with transform, components and optional prefab output.
- **[`gameobject_edit`](./gameobject_edit.md)** — Modify or delete an existing GameObject and its components.
- **[`get_compile_job`](./get_compile_job.md)** — Read a compilation request outcome and bounded diagnostics by compile_id.
- **[`get_sha`](./get_sha.md)** — Get SHA256 and basic metadata for a Unity C# script without returning file contents.
- **[`manage_addressables`](./manage_addressables.md)** — Inspect existing optional Addressables settings (groups, explicit entries and raw profiles), run bounded preflight for duplicate addresses/missing references/profile issues, or analyze an existing native build-layout report for duplicate…
- **[`manage_audio`](./manage_audio.md)** — Diagnose observed scene audio state without proving output-device audibility; inspect or explicitly set existing mixer-group routing; read/apply per-platform AudioImporter policies.
- **[`manage_build`](./manage_build.md)** — Manage Unity player builds — trigger builds, switch platforms, configure settings, manage build scenes and profiles, run batch builds across platforms.
- **[`manage_components`](./manage_components.md)** — Add, remove, or set properties on components attached to GameObjects.
- **[`manage_editor`](./manage_editor.md)** — Controls Unity editor state and settings.
- **[`manage_input_actions`](./manage_input_actions.md)** — Author optional Input System1.7+ .inputactions assets in idle Edit Mode. capabilities/read are bounded reads; read returns exact map/action/binding GUIDs and a raw source revision. create_asset requires an unoccupied Assets path and exis…
- **[`manage_localization`](./manage_localization.md)** — Inspect optional Unity Localization locale/table identities, shared keys, missing translations, static Smart String selectors and asset GUID references.
- **[`manage_material`](./manage_material.md)** — Manages Unity materials (properties, colors, shaders, render state).
- **[`manage_navigation`](./manage_navigation.md)** — Read and explicitly author navigation surfaces, agents, obstacles and links.
- **[`manage_netcode`](./manage_netcode.md)** — Inspect local Netcode for GameObjects configuration without starting a network session. capabilities reports optional package detection/version; preflight requires an explicit loaded scene_handle and checks managers, active same-scene tr…
- **[`manage_object_handles`](./manage_object_handles.md)** — Capture or resolve the original loaded GameObject/Component reference for this Editor session.
- **[`manage_packages`](./manage_packages.md)** — Manage Unity packages: query, install, remove, embed, and configure registries.
- **[`manage_recording`](./manage_recording.md)** — Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. start returns job_id immediately; poll status explicitly or stop to finalize.
- **[`manage_scene_edits`](./manage_scene_edits.md)** — Preview or apply 1-1000 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene.
- **[`manage_script`](./manage_script.md)** — Compatibility router for legacy script operations.
- **[`manage_script_capabilities`](./manage_script_capabilities.md)** — Get manage_script capabilities (supported ops, limits, and guards).
- **[`manage_terrain`](./manage_terrain.md)** — Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays.
- **[`manage_tools`](./manage_tools.md)** — Manage visible groups or individual feature tools in this session.
- **[`prefab_inspect`](./prefab_inspect.md)** — Inspect prefab metadata or a bounded hierarchy page.
- **[`read_console`](./read_console.md)** — Gets messages from or clears the Unity Editor console.
- **[`refresh_unity`](./refresh_unity.md)** — Request a Unity asset database refresh and optionally a script compilation.
- **[`scene_inspect`](./scene_inspect.md)** — Inspect loaded scenes, hierarchy and build settings, or frame the Scene view.
- **[`scene_lifecycle`](./scene_lifecycle.md)** — Create, load, save or close scenes and organize loaded scene objects.
- **[`script_apply_edits`](./script_apply_edits.md)** — Structured C# edits (methods/classes) with safer boundaries - prefer this over raw text.
- **[`script_change_set`](./script_change_set.md)** — Prepare, apply, inspect or recover 1–256 existing C# source replacements as one bounded change set.
- **[`set_active_instance`](./set_active_instance.md)** — Set the active Unity instance for this client/session.
- **[`validate_script`](./validate_script.md)** — Validate a C# script and report diagnostics, the effective validation engine, and context limitations.
- **[`wait_for_operation`](./wait_for_operation.md)** — Observe an existing build, package query/mutation/resolve, or single scenario job.
