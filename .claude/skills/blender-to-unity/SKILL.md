---
name: blender-to-unity
description: Export an existing Blender model into a selected Unity project through Unity's Blender bridge or separate connected Blender/Unity tools. Use for model handoff, not model generation or automatic package installation.
---

# Blender To Unity Handoff

Blender writes an interchange file; Unity reads it from its own filesystem. Discover the tools actually exposed by both clients rather than assuming `mcp__blender__*` bindings. This skill does not create a model merely because the Blender scene is empty.

## Choose The Available Route

- If Unity advertises `blender_bridge`, it can talk directly to a running BlenderMCP addon
  socket configured in Unity. A separate client-side BlenderMCP server is not required.
  Read [unity-bridge.md](references/unity-bridge.md) for inspection/import/placement and
  optional finishing controls. Do not require a second MCP connection or ask the user to
  manually export a file when this existing route meets the request.
- Otherwise, use the separate export/import workflow below when both the Blender export
  capability and Unity's `import_model_file` are actually available.

## Resolve The Handoff

- Identify the requested Blender objects/selection and the intended Unity project/scene. Use available scene/object inspection tools and Unity's instance inventory when needed.
- Read `mcpforunity://project/info` for the selected Unity instance. Use `data.projectRoot`/`assetsPath`, not legacy root fields in Editor state.
- Establish that Blender can write to that Unity host's filesystem. A remote path is not evidence of a shared disk. If hosts differ, use an explicitly authorized transfer into the target project's `Assets/` folder or report the missing transfer capability.
- Discover `import_model_file`. If hidden and `manage_tools` is exposed, inspect groups and activate `asset_gen` in a stateful session when appropriate. Never use `batch_execute` to bypass tool visibility or consent.

Choose GLB when glTFast is installed and PBR/animation fidelity matters; choose FBX for the built-in importer or requested humanoid pipeline. Neither is a promise of lossless material/rig transfer. Read [bridge-fidelity.md](references/bridge-fidelity.md) for historical observations and conditional fixes; read [export-examples.md](references/export-examples.md) only when authoring an export.

## Export And Import

This section is the separate-client route. The integrated bridge owns its export/staging;
its internal temporary file does not make arbitrary external sources acceptable to the
public `import_model_file` tool.

Export to a unique, non-overwriting path **inside the selected Unity project's Assets folder**, for example `Assets/ModelHandoff/Robot.fbx`. `import_model_file` accepts Assets-relative or physically-contained absolute sources; arbitrary temp paths, traversal and linked paths are rejected. If an existing export is outside Assets, obtain permission for a contained copy/transfer instead of weakening the boundary.

The import request uses public snake_case parameters:

```json
{"tool":"import_model_file","params":{"source_path":"Assets/ModelHandoff/Robot.fbx","name":"Robot","output_folder":"Assets/ImportedModels","animation_type":"generic"}}
```

For rigged/animated FBX, choose `generic`/`humanoid` intentionally; omitted/`none` can yield no imported clips. GLB uses glTFast's animation import. `target_size` depends on the project's auto-normalization preference; do not claim it guarantees world-space dimensions.

A multi-file OBJ/glTF export needs its sidecars in a supported inert ZIP layout because a bare source copy does not carry them. Verify required files and textures before importing. Do not zip unrelated directories or include executable files. If glTFast is absent, offer FBX or a separately authorized installation; do not install it as an implicit fallback.

Inspect `success`, then returned `data.asset_path` and `data.asset_guid`. A failed import is not permission to delete previous assets or repeat exports blindly.

## Place And Verify

Instantiate the returned asset path using `manage_gameobject(action="create", prefab_path=...)` with the user's intended position/name. Retain its returned ID for later edits; do not replace the current scene or add cameras/lights automatically.

Inspect renderer bounds and actual materials/clips. If the user requested a target size, measure the placed model's largest world dimension and multiply its current local scale by `target_size / measured_dimension`, provided the measured dimension is finite and nonzero. Recheck after scaling; do not apply a universal 100x correction.

Apply material/emission or Animator/controller fixes only when the observed result and requested scope require them. Inspect shader properties and asset ownership first. Imported clips do not prove playback; entering Play mode or enabling Bloom changes scene behavior and is not a mandatory handoff step. An `execute_code` fallback requires explicit Unity consent and scoped authorization, not merely a failed inspection tool.

## When A Prefab Is Requested

An imported model's `asset_path`/GUID is not a newly created `.prefab` deliverable. For the
integrated bridge, request `save_prefab=true` on the original import and verify its returned
`data.prefab_path` as described in [unity-bridge.md](references/unity-bridge.md); do not
repeat a completed import just to save a prefab.

For an already placed model, inspect the intended root in the active scene (or open prefab
stage) and ensure its name is unambiguous: this creation action finds by name, not instance
ID or hierarchy path. If it is a connected model/prefab instance, the requested new prefab
requires unlinking that root. Use `unlink_if_instance=true` only for that inspected,
authorized root; otherwise omit it or leave it false.

```json
{"tool":"manage_prefabs","params":{"action":"create_from_gameobject","target":"Robot","prefab_path":"Assets/Prefabs/Robot.prefab","allow_overwrite":false,"unlink_if_instance":true}}
```

Check `success` and `data.prefabPath`: with overwrite disabled, an occupied destination
gets a unique path rather than replacing the existing asset. Verify the **actual returned
path**, substituting it for the example below, and inspect the prefab hierarchy/material
references and the scene instance's connection. Report any different filename.

```json
{"tool":"manage_prefabs","params":{"action":"get_info","prefab_path":"Assets/Prefabs/Robot.prefab"}}
```

Capture the relevant view with an available camera/UI capture tool, inspect the image, and report asset identity, scene instance and any fidelity/verification limits. [manual-verify.md](manual-verify.md) is an optional acceptance checklist for an authorized live run, not a requirement to launch applications.
