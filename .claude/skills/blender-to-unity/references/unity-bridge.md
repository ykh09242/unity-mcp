# Unity's Integrated Blender Bridge

Use this route when the selected Unity instance advertises `blender_bridge`. Unity contacts
the configured BlenderMCP addon socket directly; a separate Blender tool in the agent's
client is unnecessary. Blender must already be running with the addon reachable from the
Unity host. A missing socket/addon is not permission to install, launch or sync applications.

## Inspect Before Import

```json
{"tool":"blender_bridge","params":{"action":"status"}}
```

Check `data.blender_reachable`; the request can succeed while reporting that Blender is not
reachable. A configured checkout is needed for addon maintenance, not every model handoff.

```json
{"tool":"blender_bridge","params":{"action":"scene_info"}}
```

Inspect a named object where useful:

```json
{"tool":"blender_bridge","params":{"action":"object_info","object_name":"Robot"}}
```

Confirm the intended Blender objects and Unity project/scene. Resolve the route's group and
Editor toggle through the discovered Unity capabilities, without hidden-handler workarounds.

## Import And Optional Finishing

A scoped FBX placement example, deliberately retaining imported scale and disabling
unrequested controller/prefab/Bloom creation:

```json
{"tool":"blender_bridge","params":{"action":"import_model","object_names":["Robot"],"format":"fbx","name":"Robot","output_folder":"Assets/ImportedModels","animation_type":"generic","apply_modifiers":false,"place_in_scene":true,"position":[0,0,0],"target_size":0,"auto_animate":false,"save_prefab":false,"ensure_bloom":false}}
```

The tool exports the named objects and their children, stages its own export within Assets,
imports it and optionally places it. With neither names nor selection-only supplied it can
export the whole scene, so do not omit targeting accidentally. GLB needs glTFast; format
fallback or installation is a user choice. Disable modifier application for rigs/shape keys
when preserving them matters.

`target_size > 0` requests measured-bounds scaling after placement; zero retains imported
scale. Verify returned `bounds_size`/`scale_factor_applied` rather than treating the request
as proof of usable renderer bounds. This differs from the public file importer's
preference-dependent importer normalization.

`auto_animate` defaults true and can create/assign a looping controller or legacy animation
setup when clips exist. `save_prefab=true` requests a prefab next to the asset;
**save_prefab is an import parameter, not an action**. `ensure_bloom=true` can change camera/
volume configuration for emissive materials. Enable these only for requested finishing
work, not every handoff. `place_in_scene=false` performs an asset-only import.

Inspect `success`, `data.asset_path`/`asset_guid` and `data.placed`. A successful import can
still report that no GameObject root was available to instantiate. The response's
`game_object` is a name; resolve it to an unambiguous Unity ID before subsequent edits.
Read `animation`, `prefab_path` and `bloom` only when those operations were requested.

When a new prefab is part of the requested handoff, choose that finishing flag on the
initial import instead of running a second export/import:

```json
{"tool":"blender_bridge","params":{"action":"import_model","object_names":["Robot"],"format":"fbx","name":"Robot","output_folder":"Assets/ImportedModels","animation_type":"generic","apply_modifiers":false,"place_in_scene":true,"target_size":0,"auto_animate":false,"save_prefab":true,"ensure_bloom":false}}
```

The bridge saves under the imported asset's folder in `Prefabs/`, generates a unique
non-overwriting path, and connects the placed instance. Check `data.placed` and a nonempty
`data.prefab_path`; an asset-only import is not prefab creation. Verify that actual returned
path with `manage_prefabs(action="get_info", prefab_path=...)`, then inspect its root,
materials/children and scene connection. For an already completed placement, use the
conditional prefab workflow in [SKILL.md](../SKILL.md) without repeating the import.

## Verification And Scope

When available, `compare_screenshot` accepts the placed object's name and produces a
Blender/Unity comparison PNG. Inspect the returned image on its actual host; a remote path
alone is not image evidence. Check relevant materials, clips and dimensions. Playback still
needs a separately requested verification context.

`run_python` executes code in Blender. `setup_bloom` changes Unity rendering;
`check_updates` fetches Git remotes and `sync_addon` writes Blender's addon installation.
These are distinct operations with external side effects, not automatic error recovery.
Do not expand an import request into them. After a timeout, inspect existing assets/scene
before repeating an import that may already have placed an object.
