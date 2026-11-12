# Tool Selection And Payloads

This is a focused routing reference, not an exhaustive frozen schema. Discover the client's advertised tools; read the selected tool's live schema before applying an example. Logical names below do not specify client prefixes.

## Routing

| Request | Relevant logical tools | Important distinction |
|---|---|---|
| Discover a task's tools | `manage_tools` with `action="catalog"` | Interpret the original request, browse relevant groups/tools and compare action purposes and effects. Describe the selected tool and operation before execution |
| Scene state/save/load | `scene_inspect`, `scene_lifecycle` | Core; preserve unsaved/additive scenes; replacement is not a neutral setup action |
| Find/create/edit objects | `find_gameobjects`, `gameobject_create`, `gameobject_edit` | Core; search returns IDs; reuse an ID with `search_method="by_id"` |
| Duplicate/position/place objects | `gameobject_duplicate`, `gameobject_positioning`, `gameobject_placement` | Optional scene group; inspect action-specific references and preview contracts |
| Components | `manage_components` | Required type/target; explicit null differs from omitted value |
| Scripts | `create_script`, `get_sha`, `apply_text_edits`, `script_apply_edits`, `validate_script`, `delete_script` | Read [scripts.md](scripts.md) for preview, concurrency and native-file checks |
| Inspect assets | `asset_inspect` | Core; find paths and metadata before changes |
| Asset changes/bundles | `asset_edit`, `asset_labels`, `asset_bundles`, `asset_reference_diagnostics` | Optional assets group; bounded AssetDatabase assignments and reference pages |
| Prefabs | `prefab_inspect`, `prefab_create`, `prefab_contents`, `prefab_stage`, `prefab_overrides` | Core inspection and optional prefabs group; inspect asset contents and select explicit scene-instance override IDs |
| Materials/shaders/textures | `manage_material`, `manage_shader`, `manage_texture` | `inspect_graph` reads supported `.shadergraph` structure; match the installed render pipeline and shader-declared property types for edits |
| Cameras/capture | `manage_cameras`, `manage_cinemachine`, `capture_camera` | Optional camera group; inspect Cinemachine and capture support |
| Prefab preview | `capture_prefab` | Use the inspected prefab asset path and review the returned image |
| Shader compiler evidence | `manage_shader` | Use `diagnostics` for cached messages and `compile_variant` for one selected stage, platform, target, pass and keyword set |
| Graphics/lighting | `manage_render_pipeline`, `manage_render_volumes`, `manage_light_baking`, `manage_components` | Optional graphics group except core components; inspect URP/HDRP capabilities |
| Animation/physics/VFX | `manage_animation`, `query_physics`, `manage_rigidbody`, `manage_physics_joints`, `manage_vfx` | Optional animation, physics or vfx group; query identity/state before native mutations |
| 2D sprite animation | `manage_sprite` | Inspect sheet dimensions and slices before reslicing; see the staged workflow below |
| UI | `ui_source`, `ui_document`, `ui_inspect`, `ui_element`, `ui_capture`, `manage_ugui` | Optional ui group; uGUI can also use core GameObject/component tools |
| Tests/input | `run_tests`, `get_test_job`, `manage_input` | Input simulation belongs to `testing`; direct uGUI event dispatch does not test raycast occlusion |
| Tests in the current Play state | `manage_input`, `manage_components`, `execute_code` | Prefer a suitable dedicated tool; proactively use exposed, enabled and consented code execution for game-specific runtime work while preserving the session. See [current-state workflow](workflows.md#tests-in-the-current-play-state) |
| Video capture | `manage_recording` | Query capabilities; start returns a job ID, status/stop require that ID; silent MP4 on supported interactive Windows/macOS Editors |
| Build/package jobs | `manage_build`, `manage_packages` | Poll returned identity; consent and optional installation scope still apply |
| ProBuilder | `manage_probuilder` | Installed package required; [topology guide](probuilder-guide.md) |
| Audio | `manage_audio` | Play/stop an existing AudioSource in Play mode; no audible-success guarantee |
| Editor/code | `manage_editor`, `execute_menu_item`, `execute_code` | Game View sizing and bounded Play readiness are explicit actions; retain returned job IDs/restore tokens |
| API inspection/docs | `unity_reflect`, `unity_docs` | Live APIs and installed assets are contextual; docs may describe another version |
| Bulk commands | `batch_execute` | Nontransactional native handler dispatch, not a tool visibility bypass |
| Asset generation/import | `generate_image`, `generate_model`, `generate_audio`, `import_model`, `import_model_file`, `blender_bridge` | Confirm provider/model capabilities and consent; poll returned jobs and preserve source/destination assets |
| Profiling/data assets | `manage_profiler`, `manage_scriptable_object` | Inspect optional dependencies and serialized fields; collect only requested data |

Only `core` is enabled by default. Optional rows require their advertised group/tool selection when absent: `testing` for tests/input, `animation` for sprites, `docs` for API inspection, `scripting_ext` for data assets, `probuilder`, `profiling`, `asset_gen` or the selected tool's listed group. See [connection.md](connection.md) for selection support, Editor availability and the optional `legacy` combined schemas. A table entry does not establish that a tool is already callable.

## Sprite Sheets

The `animation` group can expose `manage_sprite` when both Editor and server support it. Follow the returned discovery scope and selection support described in [connection.md](connection.md).

Start with `get_info` and inspect dimensions, import settings, slice pagination and any returned image. Image inclusion is conditional and bounded; metadata alone does not prove the grid is correct. `slice_sheet` replaces existing slices and may invalidate references when frames disappear. Choose `cols`/`rows` or frame dimensions, and `filter_mode` deliberately.

Use `setup_clips` then `setup_controller` for staged control, or `full_setup` only for the requested combined workflow. Inspect skipped entries and each stage's results: earlier stages can remain applied after a later failure. `overwrite` controls existing clips/controllers, not permission to discard arbitrary assets. Clip names influence controller defaults, locomotion and trigger states; verify the resulting controller. Scene assignment is optional, not a prerequisite for creating assets.

## Common Shapes

For assigned bundle metadata, discover optional `assets` tool `asset_bundles` with actions `list_asset_bundles`,
`get_bundle_assets`, and `get_bundle_dependencies`. These inspect AssetDatabase
assignments; they do not load or extract external bundle files. Use the exact returned
bundle name and paginate results.

For Shader Graph structure, use `manage_shader` with `action="inspect_graph"` and a
full project-owned `.shadergraph` path under `Assets/`. Supported modern MultiJSON
graphs return bounded node/edge/property/keyword pages. This does not edit, compile,
or validate the graph against the installed Shader Graph package. Unsupported legacy
serialization returns an error rather than being rewritten.

Use JSON objects/arrays, numeric values and booleans rather than stringifying them unnecessarily. Some tools accept legacy aliases/coercions, but those are not universal contracts. Colors are clearest as normalized `[r,g,b,a]` arrays. Omission preserves an optional value where supported; explicit `null` clears only references/actions that allow it.

Component references can use an asset path, asset GUID object or instance-ID object. Inspect the component's exposed property names rather than assuming a name from another Unity version.

```json
{"tool":"manage_components","params":{"action":"set_property","target":12345,"search_method":"by_id","component_type":"Rigidbody","properties":{"mass":5.0,"useGravity":false}}}
```

For multiple components of one type, use the discovered zero-based `component_index`. Boolean indices are invalid. Multiple property updates can be best-effort; an error does not imply earlier properties were rolled back.

## Batches

Batching reduces round trips for independent commands with known inputs; it has no guaranteed speedup factor or transactional rollback. Use small bounded batches and inspect `data.settings.batch_execute_max_commands` in Editor state for the selected instance's configured limit. The current fallback is 100 commands and the hard ceiling is 1000; these are limits, not recommended batch sizes.

```json
{"tool":"batch_execute","params":{"commands":[{"tool":"manage_gameobject","params":{"action":"create","name":"Block_A","primitive_type":"Cube","position":[0,0,0]}},{"tool":"manage_gameobject","params":{"action":"create","name":"Block_B","primitive_type":"Cube","position":[2,0,0]}}],"fail_fast":true,"parallel":false}}
```

Native batch dispatch accepts supported key normalization, but does not run every Python wrapper's aliases/validation. Use documented native-compatible parameters and avoid nested batches. Do not batch a later operation that needs an earlier returned ID unless that identity is already known. Inspect each result and skipped command before deciding whether a retry is safe.

The nested `manage_gameobject` identifiers above are native batch handler IDs, not default MCP tool bindings. Direct calls use core `gameobject_create`; enabling a batch does not expose a hidden combined MCP tool.

## Capture And Playback

Discover the optional `camera` group's `capture_camera` before capturing visual changes:

<!-- optional-group:camera:start -->
```json
{"tool":"capture_camera","params":{"action":"screenshot","capture_source":"scene_view","view_target":"Player","include_image":true,"max_resolution":512}}
```
<!-- optional-group:end -->

Image inclusion and file-output behavior depend on the chosen capture route. Inspect the returned image; a path/metadata acknowledgement does not prove the scene looks correct. Resolve an ID when names are ambiguous. Select actions and parameters from the exposed tool's schema; focused and legacy capture tools may differ.

Prefer these capture tools to ad hoc `execute_code` screenshots. If custom rendering is required, follow [temporary rendering cleanup](workflows.md#temporary-rendering-resources); never destroy a texture while it is still assigned to a camera.

Use `manage_editor(action="get_game_view_size")` to inspect the open Game View. Explicit `set_game_view_size` accepts fixed width/height, an aspect ratio, or a discovered preset. Retain the returned `restore_token` for `restore_game_view_size`; screenshots do not resize the view. `manage_recording` captures a visible view over time and scales frames to its output dimensions; it does not set the Game View size.

For recording, call `capabilities` before `start`, retain `data.job_id`, then call `status` or `stop` with that ID. Game View recording requires unpaused Play Mode and actual rendered frames, not keyboard focus; the tool does not activate or restore the Editor window. Inspect actual frame counts/skipped slots and final status. Output stays within `Captures/Recordings`; interrupted jobs discard partial video. Linux, batch mode, audio and inline video data are unsupported.

Audio example, only after the user has requested playback in Play mode and an existing source is resolved:

```json
{"tool":"manage_audio","params":{"action":"play","target":"12345","search_method":"by_id","clip":"Assets/Audio/Alert.wav"}}
```

The clip is optional for play. Stop uses the existing source and does not take a replacement clip. Invalid clips and disabled/inactive play targets fail; inactive sources can still be stopped. Successful play means Unity accepted the native operation, not that a person or recording heard sound.
