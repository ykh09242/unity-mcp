# Focused Unity Workflows

Choose the recipe relevant to the request. The JSON envelopes describe a logical tool and its arguments; invoke the corresponding exposed client tool rather than treating these as a separate API. Replace example names/paths/IDs with inspected targets.

Examples without routing assume an unambiguous sole Editor. Otherwise add the selected `unity_instance` to every modern tool request and `_meta.unity_instance` to resource requests; follow [connection.md](connection.md). Optional operations below require their advertised group/tool to be available first.

## Concurrent Calls And Coordination

Parallelize independent reads whose inputs and Editor target are already known. Keep dependencies ordered: inspect before choosing a target, wait for creation before using its returned ID, and verify a mutation after it settles. A read-looking name or discovery hint does not establish concurrency safety; supported connections classify the actual operation, including preflight and callback effects. Unknown/custom handlers are conservative.

The server coordinates whole invocations for one instance, with local writer fairness; compatible native packages enforce exclusion across separate client/server processes. These are different guarantees, not a global distributed FIFO. Different Editors remain independent. Unity API work stays on the main thread. Reviewed status/cancel requests remain available where the Editor can respond; a synchronous native call or an occupied legacy transaction can delay them.

Pending jobs can retain barriers through native cleanup after an initial response, public terminal result or cancellation acknowledgement. Follow job identity and returned coordination diagnostics. Additive coordination evidence can accompany a result without changing that result's success semantics. `recovery_required` needs inspection and the returned recovery guidance; it is not permission to replay uncertain work. Never retry a whole batch or mutation because a contender was blocked: earlier children may have executed. Automatic coordination preserves authorization, consent and the bound target.

## Shader Diagnostics

Inspect the known `.shadergraph` path with `manage_shader(action="inspect_graph")` for graph structure. Use `diagnostics` on an imported `.shader` or `.shadergraph` asset for bounded cached compiler messages and up to 100 runtime pass identities. The returned stage and compiler-platform names describe this Editor's enums. Runtime pass availability follows the imported shader and current rendering configuration.

```json
{"tool":"manage_shader","params":{"action":"diagnostics","path":"Assets/Shaders/Effect.shadergraph"}}
```

When the task requires compilation, choose an exact runtime subshader/pass and the intended target. This action requires idle Edit Mode and run permission. It compiles one variant and may update Unity's compiler cache. It preserves shader assets and the active build target.

```json
{"tool":"manage_shader","params":{"action":"compile_variant","path":"Assets/Shaders/Effect.shadergraph","variant":{"shader_type":"Vertex","platform":"D3D","build_target":"StandaloneWindows64","subshader_index":0,"pass_index":0,"keywords":[]}}}
```

Check `data.outcome` and the bounded messages. `success` covers the selected variant. `failed` contains compiler diagnostics. `no_program` means Unity returned empty bytecode for that selection. Platform/target availability depends on the installed Editor. Several graphics APIs compile multiple stages through `Vertex`. Expand the selection only when required by the task. A timeout requires observation before another compile request. [Unity CompileVariant API](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/ShaderData.Pass.CompileVariant.html).

## Console Fields And Compilation Follow-Up

For compact Console output, provide `format="json"` or `"detailed"` and include both `type` and `message` in `fields`. Selecting `stackTrace` also requires `include_stacktrace=true`. A projection error returns all detected field constraints and `suggested_argument_patch`. For a read, merge that patch into the original arguments while retaining `unity_instance`, filters and pagination.

Keep the original `compile_id` after `refresh_unity(compile="request")`. Follow `data.follow_up.next_call` when present. `get_compile_job` also returns a `follow_up` alongside the strict job data. Terminal failures direct inspection of compiler diagnostics, and unavailable tracking directs inspection of peer support or job identity. Evaluate compiler outcome, Editor readiness and source freshness independently. Retain confirmed results through a reconnect and continue the same job.

Use the returned reason to choose recovery. `observation_unavailable` allows a bounded same-job observation after transport recovery. `tracking_unsupported` calls for checking peer support. `invalid_evidence`, `unsupported_schema`, `job_identity_mismatch` and `observation_rejected` require inspecting the contract, peer or requested identity before another attempt. `evidence_diagnostics` contains bounded field/code pairs for rejected evidence. Preserve the original compiler outcome while gathering source evidence. When the returned `next_call` sets `observe_source=true`, call `get_compile_job` with that same `compile_id` and selected instance. After a domain reload, this observation can resolve `reload_requires_observation`. Repeated unchanged invalid evidence needs a contract fix.

## Prefab Edit Previews

Use `prefab_contents(action="modify_contents", preview_only=true)` to inspect serialized changes before saving. The default `preview_scope="prefab"` observes the whole prefab. For component-property work, `preview_scope="components"` observes every supported serialized field on the selected target's components named in `component_properties`. Each component selector must resolve uniquely. This scope accepts component-property edits only. `LoadPrefabContents` still loads the full prefab, and callbacks can affect additional objects or external state.

Read `data.preview.scope`, `complete`, `whole_prefab_complete`, `observed`, `truncated` and unsupported-field counts. In component scope, `complete=true` covers the selected components and `whole_prefab_complete=false` records the remaining prefab coverage. Snapshot limits are 2048 objects, 20000 serialized properties and 4 MiB. Reports retain at most 200 changes within 64 KiB. A `prefab_preview_limit_exceeded` response includes the limit kind, bound, observed count and `follow_up`. Eligible whole-prefab component edits can receive a bounded `next_call` selecting component scope. If that scope also exceeds the limit, choose a smaller component selection or explicitly proceed using other suitable evidence.

Saving remains an explicit edit request. Record the preview's scope and any pending verification when interpreting its changes. Image-based appearance checks use `capture_prefab`.

## Objects And Scenes

<!-- default-core:start -->
Find the target rather than replacing the whole scene:

```json
{"tool":"find_gameobjects","params":{"search_term":"Player","search_method":"by_name","include_inactive":true,"page_size":25,"cursor":0}}
```

Read the returned ID through `mcpforunity://scene/gameobject/{instance_id}` and its components. If several objects match, resolve the intended scene/path. Then modify by ID:

```json
{"tool":"gameobject_edit","params":{"action":"modify","target":"12345","search_method":"by_id","position":[0,1,0]}}
```

A creation/duplicate response supplies the new identity; use it for subsequent component/material configuration. Prefab placement:

```json
{"tool":"gameobject_create","params":{"action":"create","name":"PlayerInstance","prefab_path":"Assets/Prefabs/Player.prefab","position":[0,0,0]}}
```

If the request really is a new scene, inspect unsaved state before `scene_lifecycle(action="create")`. Save only the requested scene/destination, and verify the full path; do not routinely create, save or close unrelated scenes. Inspect loaded scenes for additive workflows:

```json
{"tool":"scene_inspect","params":{"action":"get_loaded_scenes"}}
```

Use the intended destination for an explicitly requested save:

```json
{"tool":"scene_lifecycle","params":{"action":"save","path":"Assets/Scenes/Requested.unity"}}
```

For large hierarchy/search results, use the continuation field actually returned by that action. Read small pages; stop when complete or if a cursor repeats. Do not assume `items`/`next_cursor` casing from a different resource.
<!-- default-core:end -->

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

Headless prefab editing and scene-instance editing are different. Discover the optional `prefabs` group's `prefab_inspect`, `prefab_contents` or `prefab_stage` for asset work; instantiate via core `gameobject_create`. For texture/VFX/lighting edits, validate action-specific payloads and bounded sizes before applying them rather than copying another pipeline's settings.

For scene-instance overrides, call `list_overrides` with the resolved instance target. Select the returned `overrideId` values for `revert_overrides` or `apply_overrides`; apply also requires the listed `prefab_path`. Scope follows the nearest nested instance root. Re-list after structural edits because IDs are editor-session identifiers. Do not infer apply-all from an empty selection; inspect rejected compound properties and structural dependencies before retrying.

For Addressables, inspect the installed package and the requested action's capabilities. Stable 1.x, 2.x, 3.x and 4.x families are eligible when their exact APIs, stored profile schema and Undo capabilities match. This is conditional source compatibility, not a claim that every version has passed native tests. Content Directory layout coverage is unsupported: reports whose actual native schema declares ContentDirectories require an explicit empty array, and all reports refuse nonempty or malformed group ContentDirectoryName values, including managed-reference data. Use the returned capability reason rather than upgrading the project's package to bypass a refusal.

## UI Creation Workflows

Respect the project's existing UI framework. Read `mcpforunity://project/info` when the request depends on UI/input packages. Its `packages` flags and `activeInputHandler` distinguish uGUI, TMP and old/new input support; do not install or migrate frameworks without scope.

UI Toolkit asset creation:

Discover/activate the optional `ui` group before these examples. `ui_source`, `ui_document`, `ui_inspect`, `ui_element` and `ui_capture` split source, document, inspection, element and rendering operations.

<!-- optional-group:ui:start -->
```json
{"tool":"ui_source","params":{"action":"create","path":"Assets/UI/Status.uxml","contents":"<ui:UXML xmlns:ui=\"UnityEngine.UIElements\"><ui:Label name=\"status\" text=\"Ready\" /></ui:UXML>"}}
```
<!-- optional-group:end -->

Create/link the intended USS with `ui_source` and use `ui_document(action="attach_ui_document")` only after confirming its live schema and a suitable target/PanelSettings asset. Inspect `ui_inspect(action="get_visual_tree")` and `ui_capture(action="render_ui")` for the resulting hierarchy/image where available. Preserve empty stylesheet content intentionally; it is not a missing-content error.

Choose `component_type="auto"` to preserve an existing UIDocument and prefer available PanelRenderer for new attachments. Use `"ui_document"` or `"panel_renderer"` to select explicitly, and keep the selection consistent for tree inspection, live modification, detach and render. PanelRenderer requires the supported Unity 6.5+ API; missing capability is a reported error, not permission to upgrade Unity. A root may be uninitialized; retry after the component is enabled and the UI loads.

For uGUI, create the requested Canvas hierarchy using GameObject/component tools. Use `RectTransform` anchors/pivots appropriate to the parent and choose the EventSystem input module that matches the project's configuration. TMP requires the available TMP component/assets. Do not add a duplicate EventSystem or overwrite a working canvas as setup. Verify text fit, interaction state and the requested viewport visually.

## Animation And Physics

Use the existing Animator/controller/clip identity. Imported clips do not imply an assigned controller or actual playback. Inspect parameters/types before writes, and choose fixed-time versus normalized-time transitions intentionally. Preserve user playback state unless a requested verification needs it changed.

For physics, choose 2D or 3D explicitly in the action's schema. Inspect collision layers/masks and body/joint references before changing settings or queuing forces. Trigger callbacks require the appropriate collider/rigidbody setup, enabled simulation and compatible filtering; adding a body alone is not proof of correct interaction. Edit-mode simulation is a mutation, not a harmless query.

## Tests In The Current Play State

Use this workflow when the requested test depends on the game as it is currently playing: the current battle, open UI, spawned objects or other live state. Read `mcpforunity://editor/state` for the selected instance and inspect the relevant target before acting. Preserve the current Play session, loaded scenes, pause state and unrelated runtime state. If the Editor is not playing or a transition/reload has lost the required state, report that precondition instead of silently starting a new session.

Choose the narrowest suitable operation:

1. Use available dedicated tools for supported component changes, UI interactions and input. Discover project-specific tools when relevant.
2. For runtime inspection, test setup, state changes or game-specific method calls that those tools cannot express, proactively select `execute_code` when exposed, enabled in Unity and execution consent is granted. An already-authorized test does not need another request naming this tool. Visibility or group activation alone does not establish native permission. If unavailable or consent-blocked, explain the missing capability or request the missing consent; never alter preferences or use another handler to bypass the restriction.
3. Inspect the actual runtime target, component and API first, using available object queries, project source or API inspection. Resolve fully qualified types, overloads, static/instance access and parameter types. Use the selected instance and resolved target identity. Keep code short, bounded and limited to the requested runtime work, with `safety_checks=true`. Check that Play Mode and the expected target/state still hold at execution time; stop on a missing, replaced or ambiguous target rather than mutating a namesake. Return concise observations and before/after values.
4. Verify the outcome with fresh state reads and relevant UI/Console evidence. For changes that finish over later frames, return promptly and use bounded follow-up observations. Do not block the Editor with sleeps, busy-waits or unbounded loops. If the call times out or reconnects with an uncertain outcome, inspect the existing state before repeating a mutation or using `replay`.

`execute_code` compiles the snippet in memory. Do not create temporary scripts, refresh/recompile the project, restart Play or reload scenes as a workaround for this workflow. Saved `manage_play_scenario` definitions require an initial scene load, and test-runner workflows can change lifecycle state; use those only when their setup/reset matches the request. In-memory code can still have lasting effects: persistent assets, save data and external-service writes need their own authorized scope. Restore only test-owned temporary changes when appropriate and the target is still valid; do not reset unrelated progress or undo the requested final state.

Separate test setup from the path being verified. For example, use code to prepare a known health value, then use the appropriate UI/input tool to exercise the requested healing interaction and observe the result. Calling the healing method directly proves only that method path, not a button, raycast or physical-input path. A successful execution response alone does not establish that asynchronous gameplay or visual behavior completed.

Track ownership and lifetime for temporary resources and state changes. Arrange restoration and cleanup even on failure, using `try/finally` or the appropriate lifecycle callback. Restore borrowed state after use; release only resources created for this operation, once all consumers finish. Preserve the requested final state and unrelated objects. For operations spanning frames, cleanup belongs after completion or cancellation, not when the initiating call returns.

For an unfamiliar API, use `unity_reflect(action="search", query=...)` to resolve a type, `get_type` with `class_name` for its member names, and `get_member` with `class_name` and `member_name` for signature details. Search supports `scope="project"` and an optional `assembly` filter. Project source can establish the same contract when available. Reuse verified signatures during the task. A compiler symbol/member error requires re-inspecting the relevant contract before changing the call. Persistently missing members can indicate stale loaded assemblies, which need the compilation/source checks above.

## Reporting Verification Evidence

- Record the target, initial lifecycle state, setup and executed path. State whether the check used the existing game state or test-owned fixtures. List substituted services and settings changed during setup, such as muted audio, along with the affected coverage.
- Attribute framework test counts and failures to the final native runner result and retain its job identity. Give custom `execute_code` assertions their own probe count and observed values.
- Name the exercised interaction route, such as direct method invocation, uGUI pointer dispatch, raw input or a complete user flow. Record visual and audio observations when those outcomes matter to the requested check.
- Verify cleanup after asynchronous consumers finish. Observe restored state and remaining test-owned objects, then report any pending cleanup or unavailable evidence.
- Match the completion claim to the observed scope. State remaining user-flow checks and unavailable evidence in the final report.

## Temporary Rendering Resources

Prefer `capture_camera` (optional camera group) capture or the UI rendering tool for supported screenshots. A warning such as `Releasing render texture that is set as Camera.targetTexture!` with `MCPDynamicCode.Execute` identifies cleanup in the submitted code; a successful tool response does not make that cleanup valid. Inspect available execution history and Console evidence without assuming the original snippet is retained.

When custom rendering is required, inspect the actual camera and render pipeline first. Save the camera's existing target and `RenderTexture.active`, then restore both in `finally` **before** releasing the texture. This synchronous ownership pattern is a cleanup template, not a complete capture implementation:

```csharp
// camera is the already-resolved, valid target; preserve the existing Play state.
var previousTarget = camera.targetTexture;
var previousActive = RenderTexture.active;
var temporary = RenderTexture.GetTemporary(256, 256, 24);
try
{
    camera.targetTexture = temporary;
    // Perform the bounded render/readback supported by this project's pipeline.
    // Do not yield, expose this temporary to other owners, or return the texture.
}
finally
{
    if (camera != null)
        camera.targetTexture = previousTarget;
    RenderTexture.active = previousActive;
    RenderTexture.ReleaseTemporary(temporary);
}
```

Pair `GetTemporary` with `ReleaseTemporary`; do not call `Destroy` or `DestroyImmediate` on a pooled texture. For an explicitly created, test-owned texture, detach/restore every binding changed by the test before releasing and disposing it using the appropriate Play/Edit-mode API. Never release the borrowed previous target, broadly clear other cameras, or destroy live game objects with `DestroyImmediate`. Async readback requires retaining ownership until completion; the synchronous template must not release a still-in-use texture.

Unity 6 references: [Camera.targetTexture](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Camera-targetTexture.html), [RenderTexture.active](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/RenderTexture-active.html), [ReleaseTemporary](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/RenderTexture.ReleaseTemporary.html).

## Tests And Long-Running Jobs

When Play readiness matters, use `manage_editor(action="play", wait_until="scene_loaded")` or `"first_frame"` and retain its returned job ID. Poll `get_play_mode_job` with a bounded deadline. `first_frame` observes simulation progress; it does not establish rendered pixels or completion of application-specific async loading. `cancel_play_mode_job` cancels monitoring without stopping Play Mode.

For requested interaction checks, enable/discover `testing` and query `manage_input(action="status")`. `ui_click` uses an integer instance ID or exact hierarchy path and directly dispatches uGUI pointer events in Old, New or Both handling. Verify the resulting application state; this path does not test raycast ordering or change `UnityEngine.Input` state.

Raw key/mouse/touch simulation requires Input System 1.7+ with New/Both handling and game code that consumes Input System devices or actions. Changing handling alone does not redirect `UnityEngine.Input` reads. Simulation advances on game input updates; inspect status for current hold bounds and lifetime limits. Device-paired PlayerInput actions may need virtual-device pairing. Pausing automatically releases MCP-owned virtual devices; `release_all` also permits explicit cleanup.

Old handling supports read-only legacy input observation during an explicit scenario recording session, subject to Unity's existing focus/background policy. Inspect `input_observation`, its backend and `raw_replay_available`. A `recorded-legacy-input` draft cannot inject its raw steps into `UnityEngine.Input`; retain target-based UI clicks and remove overlapping raw pointer steps when reviewing a UI-only flow. New/Both retain the Input System observer. Do not change Active Input Handling to make a diagnostic pass.

Inspect `legacy_raw_input_unavailable_reason` for alternatives in every handling mode. Use `ui_click` or scenario `click_ui` for uGUI targets. A project-defined input provider/action adapter requires explicit game-code integration; MCP does not supply a legacy raw adapter or rewrite input consumers. The public `EditorGUIUtility.QueueGameViewInputEvent` route is not an advertised raw backend: an isolated unfocused Windows Unity 6000.0.69f1 graphics batch probe delivered IMGUI events while raw polling stayed neutral; hidden non-batch probes produced no runtime samples, leaving normal Editor background delivery unverified. IMGUI delivery alone is insufficient evidence. Do not substitute focus changes, OS input, private reflection APIs or an automatic input-settings migration.

Instrumented integration can support legacy-style input: [AltTester documents](https://www.alttester.com/docs/sdk/latest/pages/advanced-usage.html#alttester-input) its custom `Input` class, assembly references and changed physical-input routing. Treat that as an explicit consumer-integration example, not an MCP dependency or a transparent change to untouched `UnityEngine.Input` reads.

Start a specific requested test run, then retain its returned `data.job_id`:

```json
{"tool":"run_tests","params":{"mode":"EditMode","test_names":["MyTests.TestSomething"],"include_failed_tests":true}}
```

Poll that identity rather than starting another run:

```json
{"tool":"get_test_job","params":{"job_id":"returned-job-id","wait_timeout":30,"include_failed_tests":true}}
```

Terminal statuses are `succeeded`, `failed` and `cancelled`; a polling timeout can simply mean the job is still running. Inspect counts/failed details even when the poll request itself has `success=true`. A result callback can precede native resource release: inspect `data.native_control.released` before starting another run. Missing release evidence is unconfirmed; requery the same job within the observation budget. Do not use `clear_stuck` against another active run as an automatic retry. Preserve job identity across reload and report what actually ran.

Build/package/asset-generation jobs likewise require their own returned ID/status contract. Poll the advertised action or supported `wait_for_operation` kind with that identity; do not infer a generic `get_job` tool or assume a queued response means an asset/build succeeded. A successful observation may contain a failed native outcome; inspect that outcome and its error.

## Verification And Recovery

For script changes, follow [scripts.md](scripts.md). For visual changes, capture the relevant view with `capture_camera` (optional camera group) or `ui_capture` (optional ui group) rather than running every screenshot mode.

```json
{"tool":"read_console","params":{"action":"get","types":["error","warning"],"count":10,"include_stacktrace":true}}
```

MCP internal logs are excluded from console reads by default. Set `include_mcp_logs=true` when diagnosing the bridge; the filter applies before count/pagination. It does not delete console entries.

For explicitly requested build-output cleanup, preview with `manage_build(action="clean_output", output_path="Builds/SelectedOutput")`. The default `dry_run=true` reports the selected output. Deletion requires `dry_run=false` and stays inside an explicit descendant of `Builds/`; links, project source/assets and the root directory are rejected. Existing `execute_code` restrictions still apply.

Report concrete state/image/test evidence and remaining limits. Console silence does not prove behavior or audible playback. If compilation/reload blocks a read, follow readiness advice with a bounded wait. For malformed parameters, inspect the live schema and correct the request. For an uncertain mutation outcome, inspect existing state before retrying; batches may leave earlier successful work applied.
