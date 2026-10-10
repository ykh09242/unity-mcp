---
name: unity-play-scenarios
description: Author, save and run repeatable Unity Play Mode scenarios using manage_play_scenario, including menu-to-game flows, stable readiness conditions, setup/cleanup, bounded diagnostics and saved run comparisons. Use for reusable gameplay checks or edits to saved Play Scenarios.
---

# Unity Play Scenarios

Translate the user's requested game flow into a saved scenario using real project paths. Complete authoring and saving within that scope. Run when execution is requested or implied; reuse existing authorization.

## Discover the actual flow

- Select the intended Unity instance. If the tool is hidden, inspect `manage_tools(action="list_groups")` and activate `testing` in a stateful MCP session. Activation cannot enable a tool disabled in Unity; after Editor tool-setting changes, sync and refresh available tools.
- Discover scene assets and active-scene hierarchy paths through available scene/asset queries. Do not assume the game contains Menu, Canvas/Start or Player. Do not query GameData or credential files. Clarify only paths that cannot be established from available evidence.
- Object targets are exact root-relative hierarchy paths in the active scene, not instance IDs. Unique-path conditions reject ambiguity; explicit object counts deliberately support duplicate exact paths. Do not reuse IDs after reload.
- `click_ui` dispatches direct uGUI events. It does not cover UI Toolkit, OS input, occlusion or raycast hits. Confirm the UI system. A disabled Button can become ready within its timeout; a missing click component is a failure.

## Author a bounded definition

The first executed step must load a scene: use setup's first step when setup exists, otherwise main's first step. This example is illustrative; replace its scene and target with verified values:

```json
{
  "name": "menu-start",
  "poll_interval_ms": 250,
  "setup_steps": [
    {"name": "Load menu", "action": "load_scene", "scene": "Assets/Scenes/Menu.unity"}
  ],
  "steps": [
    {"name": "Click Start", "action": "click_ui", "target": "Canvas/Start"},
    {"name": "Wait for game", "action": "wait_scene", "scene": "Assets/Scenes/Game.unity"},
    {"name": "Player stays active", "action": "wait_object", "target": "Player", "count": 1, "active": true, "stable_for_ms": 500}
  ],
  "cleanup_steps": [
    {"name": "Return to menu", "action": "load_scene", "scene": "Assets/Scenes/Menu.unity"}
  ],
  "log_policy": {"mode": "strict", "allowed_messages": []},
  "completion_stable_ms": 250,
  "cleanup_timeout_seconds": 30
}
```

Use names matching `[a-z0-9][a-z0-9_-]{0,63}`. Main has 1-32 steps; setup and cleanup each have at most 16, total at most 64. Step names are 1-128 characters, timeouts integer 1-120 seconds (default 30), and polling integer 100-2000 ms (default 250). Scene paths must be canonical `Assets/... .unity` paths outside GameData; hierarchy paths reject traversal, empty segments, backslashes and control characters. Preserve JSON types: unknown fields, explicit null optional fields and string-encoded numbers fail validation. Definition size is at most 64 KiB.

Choose readiness based on what the game actually guarantees:

- `wait_object` without count requires one unique target. Explicit `count` counts all exact matches, including inactive objects; every match must satisfy `active` (default true, explicit false requires inactivity). `count: 0` means absence and excludes active/component/property options.
- At effective count one, `component` is an exact full type name. Add `property: {"path": "isInitialized", "equals": true}` only after verifying that component and serialized field. Equality accepts bool, signed 64-bit integer, finite number or string, not null/collections. Arbitrary getters and methods are never invoked. Component and property path limits are 256 UTF-16 units; string equality is at most 1024.
- `stable_for_ms` on wait_object/wait_scene requires continuous successful sampled observations and resets on false. It is 0-60000 and strictly below the step timeout; it cannot detect changes between polls.
- Prefer the strict default error policy. Exact, case-sensitive full-message allowlists can admit known intentional errors (at most 32 unique messages, each 1-1024 UTF-16 units). Use `log_only` only when the requested scenario deliberately tolerates logged errors; thrown exceptions still fail.
- The completion quiet window is 0-10000 ms, default 250, after main steps and before cleanup. It catches late logs only within that bounded interval. Prefer an initialized-property condition over extending it blindly.
- Every repetition executes setup/main/cleanup. Scene reloads do not reset arbitrary statics or DontDestroyOnLoad objects. Express the application's intended reset actions explicitly. Cleanup has a separate 1-300 second budget (default 30) and may run after failure/cancel/timeout if execution began.

Save through `manage_play_scenario(action="save", scenario=<whole definition>)`, without a separate name. List/get before modifying an existing definition; save variants under new names. Same-name save replaces content.

For visual authoring open **Window -> MCP for Unity -> Play Scenarios**. Fill the New template, use scene pickers/Use Selection, and edit Setup/Main/Cleanup plus Run policies and diagnostics. Draft Undo/Redo is bounded to 50 snapshots; Duplicate makes an independent step. Preflight is read-only and defers runtime/future-scene targets; it does not replace execution. Save As protects existing names. Run validates and saves first. Refresh history and Compare reports inspect up to 20 saved results without repeated scene queries.

## Run and interpret evidence

`manage_play_scenario(action="run", name="menu-start", repeat_count=4, timeout_seconds=300)` starts immediately. Repetitions are 1-10; the main wall-clock budget is at most 1800 seconds. One job runs at a time. Choose a budget within the user's task.

Optionally provide a 32-character lowercase hexadecimal job_id as an idempotency key. After an uncertain response, inspect that ID first; retry the same request with the same ID instead of creating a duplicate job. Keep the returned ID for status/cancel.

Call status every 0.5-1 seconds only while the job is active, stopping at terminal state or the task budget. Unity performs its own bounded condition polling; do not create a permanent/background Python monitor. At the budget boundary, cancel your own job once when appropriate and report its current state. Cancellation can remain running in `cleaning`; allow the bounded cleanup budget to finish if the task permits. Repeated cancellation does not extend it. Native scene loads cannot be aborted.

The outer success means command handling; only `status: succeeded` means the scenario passed. Phases include starting, executing, settling, cleaning, finalizing and finished. During finalizing, status remains running and pending_status identifies the eventual outcome; continue observing until evidence/report saving completes. Terminal statuses are succeeded/failed/timed_out/cancelled. On failure, inspect stage, iteration, step_index, detail, poll_count, primary error, cleanup_error and failure_diagnostics. Pending main/future steps can be skipped. Report the first failure and evidence path; preserve the primary outcome if cleanup also fails.

Strict unexpected-error counts survive log-tail rotation and include late coroutine logs. Reports retain at most 50 log entries and bounded messages/stacks. Failure diagnostics capture scene, target and observation before cleanup. `diagnostics: {"screenshot_on_failure": true}` requests a bounded end-of-frame composited screenshot only in an interactive unpaused graphics Editor. Batch/headless capture reports unavailable; do not invent or claim screenshot evidence when screenshot_error is set.

For trend investigations, enable `metrics: {"enabled": true, "warmup_iterations": 1, "consecutive_increases": 2}` and choose at least four repetitions for that default window. Sampling occurs once after successful iteration cleanup, not every poll; no forced GC. Metrics cover managed memory, Unity allocated memory, eligible loaded scene GameObjects and runner-owned subscription/handle counts. Missing samples break a streak. Thresholds are configurable; warnings do not prove a leak or fail a run. Explain these limits instead of treating allocation growth as a confirmed leak.

Unity enters Play if needed and leaves it unchanged at completion. Reload during execution interrupts without replay; Play exit/reload makes cleanup unavailable. Closing the window does not cancel the backend job. Reports and owned PNG sidecars are bounded and retained under Library/MCPForUnity/PlayScenarioRuns; definitions are versionable under ProjectSettings/MCPForUnity/PlayScenarios.

Use `manage_play_scenario(action="reports", name="menu-start")` for saved history; omit name to list recent terminal runs across scenarios. Compare equivalent definitions and the same stage/iteration/index before interpreting timing or metric changes.

## CLI fallback

Use an existing CLI connection when MCP is unavailable; installing/starting a new server is outside this skill's own scope. Select the instance with the global --instance option where needed and keep --format json for complete evidence:

```sh
unity-mcp --format json play-scenario save menu-start.json
unity-mcp --format json play-scenario run menu-start --repeat-count 4 --timeout-seconds 300
unity-mcp --format json play-scenario status JOB_ID
unity-mcp --format json play-scenario cancel JOB_ID
unity-mcp --format json play-scenario reports --name menu-start
```

Status reads once. Run/status return nonzero for failed/timed-out/cancelled jobs; a processed cancel request and successful reports query return zero regardless of job outcome. Summarize what was saved/executed, verified paths, repetitions, final outcome and actual evidence. Synthetic fixture success does not establish that the user's game or visible pixels were tested.
