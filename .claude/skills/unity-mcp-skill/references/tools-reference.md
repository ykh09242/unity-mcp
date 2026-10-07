# Tool Selection And Payloads

This is a focused routing reference, not an exhaustive frozen schema. Discover the client's advertised tools; read the selected tool's live schema before applying an example. Logical names below do not specify client prefixes.

## Routing

| Request | Relevant logical tools | Important distinction |
|---|---|---|
| Scene state/save/load | `manage_scene` | Preserve unsaved/additive scenes; replacement is not a neutral setup action |
| Find/edit objects | `find_gameobjects`, `manage_gameobject` | Search returns IDs; reuse an ID with `search_method="by_id"` |
| Components | `manage_components` | Required type/target; explicit null differs from omitted value |
| Scripts | `create_script`, `get_sha`, `apply_text_edits`, `script_apply_edits`, `validate_script`, `delete_script` | Read [scripts.md](scripts.md) for preview, concurrency and native-file checks |
| Assets/prefabs | `manage_asset`, `manage_prefabs` | Instantiate a prefab using `manage_gameobject`, not headless prefab editing |
| Materials/shaders/textures | `manage_material`, `manage_shader`, `manage_texture` | Match the installed render pipeline and shader-declared property types |
| Cameras/graphics/lighting | `manage_camera`, `manage_graphics`, `manage_components` | Inspect lighting components and optional Cinemachine/URP/HDRP capabilities; do not assume availability |
| Animation/physics/VFX | `manage_animation`, `manage_physics`, `manage_vfx` | Query identity/state before native mutations; inspect action-specific parameters |
| 2D sprite animation | `manage_sprite` | Inspect sheet dimensions and slices before reslicing; see the staged workflow below |
| UI | `manage_ui`, `manage_ugui`, `manage_gameobject`, `manage_components` | UI Toolkit and uGUI use different asset/component workflows |
| Tests | `run_tests`, `get_test_job` | Async job acknowledgement is not a passing test result |
| Build/package jobs | `manage_build`, `manage_packages` | Poll returned identity; consent and optional installation scope still apply |
| ProBuilder | `manage_probuilder` | Installed package required; [topology guide](probuilder-guide.md) |
| Audio | `manage_audio` | Play/stop an existing AudioSource in Play mode; no audible-success guarantee |
| Editor/code | `manage_editor`, `execute_menu_item`, `execute_code` | Exact action/schema plus explicit consent where enforced |
| API inspection/docs | `unity_reflect`, `unity_docs` | Live APIs and installed assets are contextual; docs may describe another version |
| Bulk commands | `batch_execute` | Nontransactional native handler dispatch, not a tool visibility bypass |
| Asset generation/import | `generate_image`, `generate_model`, `generate_audio`, `import_model`, `import_model_file`, `blender_bridge` | Confirm provider/model capabilities and consent; poll returned jobs and preserve source/destination assets |
| Profiling/data assets | `manage_profiler`, `manage_scriptable_object` | Inspect optional dependencies and serialized fields; collect only requested data |

## Sprite Sheets

The `animation` group can expose `manage_sprite` when both Editor and server support it. Follow [connection.md](connection.md) for visibility; sessionless HTTP cannot persist group activation.

Start with `get_info` and inspect dimensions, import settings, slice pagination and any returned image. Image inclusion is conditional and bounded; metadata alone does not prove the grid is correct. `slice_sheet` replaces existing slices and may invalidate references when frames disappear. Choose `cols`/`rows` or frame dimensions, and `filter_mode` deliberately.

Use `setup_clips` then `setup_controller` for staged control, or `full_setup` only for the requested combined workflow. Inspect skipped entries and each stage's results: earlier stages can remain applied after a later failure. `overwrite` controls existing clips/controllers, not permission to discard arbitrary assets. Clip names influence controller defaults, locomotion and trigger states; verify the resulting controller. Scene assignment is optional, not a prerequisite for creating assets.

## Common Shapes

Use JSON objects/arrays, numeric values and booleans rather than stringifying them unnecessarily. Some tools accept legacy aliases/coercions, but those are not universal contracts. Colors are clearest as normalized `[r,g,b,a]` arrays. Omission preserves an optional value where supported; explicit `null` clears only references/actions that allow it.

Component references can use an asset path, asset GUID object or instance-ID object. Inspect the component's exposed property names rather than assuming a name from another Unity version.

```json
{"tool":"manage_components","params":{"action":"set_property","target":12345,"search_method":"by_id","component_type":"Rigidbody","properties":{"mass":5.0,"useGravity":false}}}
```

For multiple components of one type, use the discovered zero-based `component_index`. Boolean indices are invalid. Multiple property updates can be best-effort; an error does not imply earlier properties were rolled back.

## Batches

Batching reduces round trips for repetitive independent commands; it has no guaranteed speedup factor or transactional rollback. The default configured limit is 25 commands and the hard ceiling is 100. Read `data.settings.batch_execute_max_commands` from Editor state if the limit matters.

```json
{"tool":"batch_execute","params":{"commands":[{"tool":"manage_gameobject","params":{"action":"create","name":"Block_A","primitive_type":"Cube","position":[0,0,0]}},{"tool":"manage_gameobject","params":{"action":"create","name":"Block_B","primitive_type":"Cube","position":[2,0,0]}}],"fail_fast":true,"parallel":false}}
```

Native batch dispatch accepts supported key normalization, but does not run every Python wrapper's aliases/validation. Use documented native-compatible parameters and avoid nested batches. Do not batch a later operation that needs an earlier returned ID unless that identity is already known. Inspect each result and skipped command before deciding whether a retry is safe.

## Capture And Playback

Capture visual changes with the actual camera/capture capability:

```json
{"tool":"manage_camera","params":{"action":"screenshot","capture_source":"scene_view","view_target":"Player","include_image":true,"max_resolution":512}}
```

Image inclusion and file-output behavior depend on the chosen capture route. Inspect the returned image; a path/metadata acknowledgement does not prove the scene looks correct. Resolve an ID when names are ambiguous. Do not invent `screenshot_multiview` actions or pass camera capture parameters to the current `manage_scene` schema.

Audio example, only after the user has requested playback in Play mode and an existing source is resolved:

```json
{"tool":"manage_audio","params":{"action":"play","target":"12345","search_method":"by_id","clip":"Assets/Audio/Alert.wav"}}
```

The clip is optional for play. Stop uses the existing source and does not take a replacement clip. Invalid clips and disabled/inactive play targets fail; inactive sources can still be stopped. Successful play means Unity accepted the native operation, not that a person or recording heard sound.
