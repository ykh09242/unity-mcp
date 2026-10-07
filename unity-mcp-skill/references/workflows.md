# Focused Unity Workflows

Choose the recipe relevant to the request. The JSON envelopes describe a logical tool and its arguments; invoke the corresponding exposed client tool rather than treating these as a separate API. Replace example names/paths/IDs with inspected targets.

## Objects And Scenes

Find the target rather than replacing the whole scene:

```json
{"tool":"find_gameobjects","params":{"search_term":"Player","search_method":"by_name","include_inactive":true,"page_size":25,"cursor":0}}
```

Read the returned ID through `mcpforunity://scene/gameobject/{instance_id}` and its components. If several objects match, resolve the intended scene/path. Then modify by ID:

```json
{"tool":"manage_gameobject","params":{"action":"modify","target":"12345","search_method":"by_id","position":[0,1,0]}}
```

A creation/duplicate response supplies the new identity; use it for subsequent component/material configuration. Prefab placement:

```json
{"tool":"manage_gameobject","params":{"action":"create","name":"PlayerInstance","prefab_path":"Assets/Prefabs/Player.prefab","position":[0,0,0]}}
```

If the request really is a new scene, inspect unsaved state before `manage_scene(action="create")`. Save only the requested scene/destination, and verify the full path; do not routinely create, save or close unrelated scenes. Inspect `manage_scene(action="get_loaded_scenes")` for additive workflows.

For large hierarchy/search results, use the continuation field actually returned by that action. Read small pages; stop when complete or if a cursor repeats. Do not assume `items`/`next_cursor` casing from a different resource.

## Materials And Assets

Choose a shader available in `mcpforunity://project/info`'s render pipeline; this example is URP-specific:

```json
{"tool":"manage_material","params":{"action":"create","material_path":"Assets/Materials/Accent.mat","shader":"Universal Render Pipeline/Lit","properties":{"_BaseColor":[0.1,0.7,0.4,1]}}}
```

Use the discovered renderer target and intended slot:

```json
{"tool":"manage_material","params":{"action":"assign_material_to_renderer","target":"12345","search_method":"by_id","material_path":"Assets/Materials/Accent.mat","slot":0,"mode":"shared"}}
```

A shared-material change affects other users of that asset. Choose instance/property-block behavior only when appropriate to the requested scope. Preserve occupied destination assets and GUIDs; do not overwrite an unrelated asset as error recovery. Inspect shader properties before assigning textures/integer values.

Headless prefab editing and scene-instance editing are different. Inspect the prefab path/stage and use `manage_prefabs` for asset changes; instantiate via `manage_gameobject`. For texture/VFX/lighting edits, validate action-specific payloads and bounded sizes before applying them rather than copying another pipeline's settings.

For scene-instance overrides, call `list_overrides` with the resolved instance target. Select the returned `overrideId` values for `revert_overrides` or `apply_overrides`; apply also requires the listed `prefab_path`. Scope follows the nearest nested instance root. Re-list after structural edits because IDs are editor-session identifiers. Do not infer apply-all from an empty selection; inspect rejected compound properties and structural dependencies before retrying.

## UI Creation Workflows

Respect the project's existing UI framework. Read `mcpforunity://project/info` when the request depends on UI/input packages. Its `packages` flags and `activeInputHandler` distinguish uGUI, TMP and old/new input support; do not install or migrate frameworks without scope.

UI Toolkit asset creation:

```json
{"tool":"manage_ui","params":{"action":"create","path":"Assets/UI/Status.uxml","contents":"<ui:UXML xmlns:ui=\"UnityEngine.UIElements\"><ui:Label name=\"status\" text=\"Ready\" /></ui:UXML>"}}
```

Create/link the intended USS with `manage_ui` and use `attach_ui_document` only after confirming its live schema and a suitable target/PanelSettings asset. Inspect `get_visual_tree` and `render_ui` for the resulting hierarchy/image where available. Preserve empty stylesheet content intentionally; it is not a missing-content error.

Choose `component_type="auto"` to preserve an existing UIDocument and prefer available PanelRenderer for new attachments. Use `"ui_document"` or `"panel_renderer"` to select explicitly, and keep the selection consistent for tree inspection, live modification, detach and render. PanelRenderer requires the supported Unity 6.5+ API; missing capability is a reported error, not permission to upgrade Unity. A root may be uninitialized; retry after the component is enabled and the UI loads.

For uGUI, create the requested Canvas hierarchy using GameObject/component tools. Use `RectTransform` anchors/pivots appropriate to the parent and choose the EventSystem input module that matches the project's configuration. TMP requires the available TMP component/assets. Do not add a duplicate EventSystem or overwrite a working canvas as setup. Verify text fit, interaction state and the requested viewport visually.

## Animation And Physics

Use the existing Animator/controller/clip identity. Imported clips do not imply an assigned controller or actual playback. Inspect parameters/types before writes, and choose fixed-time versus normalized-time transitions intentionally. Preserve user playback state unless a requested verification needs it changed.

For physics, choose 2D or 3D explicitly in the action's schema. Inspect collision layers/masks and body/joint references before changing settings or queuing forces. Trigger callbacks require the appropriate collider/rigidbody setup, enabled simulation and compatible filtering; adding a body alone is not proof of correct interaction. Edit-mode simulation is a mutation, not a harmless query.

## Tests And Long-Running Jobs

When Play readiness matters, use `manage_editor(action="play", wait_until="scene_loaded")` or `"first_frame"` and retain its returned job ID. Poll `get_play_mode_job` with a bounded deadline. `first_frame` observes simulation progress; it does not establish rendered pixels or completion of application-specific async loading. `cancel_play_mode_job` cancels monitoring without stopping Play Mode.

For requested interaction checks, enable/discover `testing` and query `manage_input(action="status")`. `ui_click` uses an integer instance ID or exact hierarchy path and directly dispatches uGUI pointer events. Verify the resulting application state; this path does not test raycast ordering. Raw key/mouse/touch input requires Input System 1.7+ with Input System/Both handling and advances on game input updates. Holds are limited to 1–600 updates with a hard 30-second lease. Device-paired PlayerInput actions may need virtual-device pairing; legacy UnityEngine.Input is unsupported. Pausing automatically releases MCP-owned virtual devices; `release_all` also permits explicit cleanup.

Start a specific requested test run, then retain its returned `data.job_id`:

```json
{"tool":"run_tests","params":{"mode":"EditMode","test_names":["MyTests.TestSomething"],"include_failed_tests":true}}
```

Poll that identity rather than starting another run:

```json
{"tool":"get_test_job","params":{"job_id":"returned-job-id","wait_timeout":30,"include_failed_tests":true}}
```

Terminal statuses are `succeeded`, `failed` and `cancelled`; a polling timeout can simply mean the job is still running. Inspect counts/failed details even when the poll request itself has `success=true`. Do not use `clear_stuck` against another active run as an automatic retry. Preserve job identity across reload and report what actually ran.

Build/package/asset-generation jobs likewise require their own returned ID/status contract. Poll the advertised action; do not infer a generic `get_job` tool or assume a queued response means an asset/build succeeded.

## Verification And Recovery

For script changes, follow [scripts.md](scripts.md). For visual changes, capture the relevant view with `manage_camera` or UI rendering rather than running every screenshot mode.

```json
{"tool":"read_console","params":{"action":"get","types":["error","warning"],"count":10,"include_stacktrace":true}}
```

MCP internal logs are excluded from console reads by default. Set `include_mcp_logs=true` when diagnosing the bridge; the filter applies before count/pagination. It does not delete console entries.

For explicitly requested build-output cleanup, preview with `manage_build(action="clean_output", output_path="Builds/SelectedOutput")`. The default `dry_run=true` reports the selected output. Deletion requires `dry_run=false` and stays inside an explicit descendant of `Builds/`; links, project source/assets and the root directory are rejected. Existing `execute_code` restrictions still apply.

Report concrete state/image/test evidence and remaining limits. Console silence does not prove behavior or audible playback. If compilation/reload blocks a read, follow readiness advice with a bounded wait. For malformed parameters, inspect the live schema and correct the request. For an uncertain mutation outcome, inspect existing state before retrying; batches may leave earlier successful work applied.
