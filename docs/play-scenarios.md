# Saved Play Mode scenarios

`manage_play_scenario` saves repeatable checks such as **menu -> Start -> game scene -> player**.
Unity owns the job, condition waits, step results and bounded diagnostics. `run` returns a `job_id`
immediately; `status` reads once. The Python tool and CLI do not start a polling task or replay effects.

Select the intended Unity instance. If needed, activate the `testing` group with
`manage_tools(action="activate", group="testing")` after enabling the tool in Unity.

## Author in the Editor

Open **Window -> MCP for Unity -> Play Scenarios**. **New** creates a four-step menu/start/game/player
template with empty scene fields and placeholder targets. Choose real scene assets and exact object
paths, using the scene picker and **Use Selection** where applicable.

Edit **Setup**, **Main** and **Cleanup** steps, with action-specific fields, ordering, duplication and
removal. **Undo** and **Redo** keep up to 50 in-window JSON snapshots; these are draft history, not Unity
scene undo. **Save** persists the definition. Existing names are locked; **Save As** creates a separate
named copy and refuses to overwrite one. Delete and navigation protect unsaved changes.

**Preflight** checks scene assets and the currently inspectable target capabilities without loading a
scene, entering Play Mode or clicking. Runtime/future-scene targets are marked `deferred`; preflight
success does not establish that the complete game flow will pass. Inspect its stage/index and detail.

**Run policies and diagnostics** configures unexpected error handling, a completion quiet window,
cleanup timeout, optional per-iteration memory samples and failure screenshots. Run validates and saves
the draft before starting. Status refreshes every 500 ms only while the job is active. Closing the
window leaves the backend job running; reopen it to inspect or cancel the retained job ID.

**Previous runs and comparison** reads saved reports on **Refresh history** and after completion.
Choose two different reports and **Compare reports** to compare status, duration, poll counts and
metric warnings. Rows align by stage, iteration and step index; a warning identifies changed definitions.
This view does not continuously scan report files or query scene objects.

## Define and save

Save this as `menu-start.json`, replacing the scene and hierarchy paths with verified project values:

```json
{
  "name": "menu-start",
  "poll_interval_ms": 250,
  "completion_stable_ms": 250,
  "setup_steps": [
    {
      "name": "Load menu",
      "action": "load_scene",
      "scene": "Assets/Scenes/Menu.unity",
      "timeout_seconds": 30
    }
  ],
  "steps": [
    {
      "name": "Click Start",
      "action": "click_ui",
      "target": "Canvas/Start",
      "timeout_seconds": 30
    },
    {
      "name": "Wait for game scene",
      "action": "wait_scene",
      "scene": "Assets/Scenes/Game.unity",
      "stable_for_ms": 500,
      "timeout_seconds": 30
    },
    {
      "name": "Assert one active player remains ready",
      "action": "wait_object",
      "target": "Player",
      "count": 1,
      "active": true,
      "stable_for_ms": 1000,
      "timeout_seconds": 30
    }
  ],
  "cleanup_steps": [
    {
      "name": "Return to menu",
      "action": "load_scene",
      "scene": "Assets/Scenes/Menu.unity",
      "timeout_seconds": 30
    }
  ],
  "cleanup_timeout_seconds": 30,
  "log_policy": { "mode": "strict", "allowed_messages": [] },
  "metrics": {
    "enabled": true,
    "warmup_iterations": 1,
    "consecutive_increases": 2,
    "managed_growth_bytes": 1048576,
    "allocated_growth_bytes": 1048576,
    "object_growth_count": 0
  },
  "diagnostics": { "screenshot_on_failure": false }
}
```

The first executed step must be `load_scene`: `setup_steps[0]` when setup exists, otherwise `steps[0]`.
Existing definitions with only `steps` remain valid. Each iteration runs setup, main, the completion
quiet window, then cleanup. Scene loads do not reset arbitrary static state or `DontDestroyOnLoad`
objects. Express the game's own reset flow explicitly when repeat isolation matters.

```sh
unity-mcp --instance "MyProject@<hash>" --format json play-scenario save menu-start.json
unity-mcp --format json play-scenario list
unity-mcp --format json play-scenario get menu-start
```

The equivalent save call is `manage_play_scenario(action="save", scenario=<whole definition>)`;
do not supply a separate `name`. Definitions live in `ProjectSettings/MCPForUnity/PlayScenarios` and
can be versioned. Storage accepts at most 100 definitions, each at most 64 KiB.

## Readiness and error policies

For `wait_object`, omitted `count` requires a unique path; duplicate paths fail as ambiguous. Explicit
`count` counts all exact-path matches, including inactive ones. All matches must satisfy the requested
activity: `active: true` (the default) requires active objects; `active: false` requires inactive ones.
`count: 0` waits for absence and cannot be combined with active/component/property conditions.

At effective count one, `component` can require an exact full type name. A `property` condition checks a
serialized scalar without invoking reflected getters or methods. For example, after verifying the
actual component type and serialized field in the project:

```json
{
  "name": "Player initialization completed",
  "action": "wait_object",
  "target": "Player",
  "component": "MyGame.PlayerState",
  "property": { "path": "isInitialized", "equals": true },
  "stable_for_ms": 500,
  "timeout_seconds": 30
}
```

Supported equality values are boolean, signed 64-bit integer, finite number and string. Null, arrays
and objects are rejected. A missing/incompatible serialized property fails with an explicit detail.
`stable_for_ms` on `wait_object` or `wait_scene` requires uninterrupted successful observations for that
duration; a false observation resets the interval. It cannot observe changes between polls. Clicks and
scene-load dispatches are not retried after execution starts. A temporarily disabled uGUI Button waits
for activation within the step timeout; a missing click handler is a capability failure.

The default `strict` log policy fails on unexpected Unity Error/Assert/Exception logs throughout the
active run, including later coroutine errors and cleanup. `allowed_messages` matches the complete,
case-sensitive message exactly before log truncation. Allow only known intentional messages. `log_only`
records logs without failing for them; directly thrown action exceptions always fail under either mode.

`completion_stable_ms` keeps the job active after main steps, without additional host polling, to catch
late logs before cleanup. Its default is 250 ms; even zero retains the final evaluation's error check.
This is a bounded observation window, not proof that arbitrary asynchronous work has finished. Prefer
an explicit initialized-property condition for game readiness.

## Run, cancel and inspect

```sh
unity-mcp --format json play-scenario run menu-start --repeat-count 4 --timeout-seconds 300
unity-mcp --format json play-scenario status JOB_ID
unity-mcp --format json play-scenario cancel JOB_ID
unity-mcp --format json play-scenario reports --name menu-start
unity-mcp --format json play-scenario delete menu-start
```

Equivalent MCP calls use actions `run`, `status`, `cancel`, `reports` and `delete`; `reports` accepts an
optional `name`. A caller-generated 32-character lowercase hexadecimal `job_id` on `run` is an
idempotency key. After an uncertain response, inspect that ID before retrying; do not create duplicate
jobs under new IDs. Only one job runs at a time. Unity enters Play Mode if needed and leaves it unchanged
on completion, failure or cancellation.

The outer `success` means the command was handled. Only report `status: succeeded` means the scenario
passed. CLI run/status return a nonzero exit code for failed, timed-out or cancelled jobs;
a processed cancel request and a successful history query return zero regardless of job outcome. JSON output retains the report. While evidence/report saving is pending, status stays `running` with phase `finalizing` and the terminal `pending_status`; keep observing until the final report is available.

Failure, timeout or cancellation skips the remaining main steps and future iterations. If execution
began, configured cleanup runs best effort under its separate wall-clock budget; cancellation can
therefore return a still-running job in `cleaning`. The cleanup budget begins after failure capture resolves; further cancellation does not reset it.
The primary outcome remains intact, with cleanup errors reported separately. Exiting Play Mode or
reloading assemblies after execution starts interrupts immediately and reports cleanup unavailable;
effects are never replayed. An in-flight native asynchronous scene load cannot be cancelled.

Reports include stage/iteration/step index, status, timestamps, poll counts, observations, error state,
cleanup outcome and runner resource release. The log tail holds 50 entries, with message/stack bounds
of 1,024/2,048 characters; unexpected error counts survive tail rotation. Up to 20 terminal reports of
at most 2 MiB each are retained in `Library/MCPForUnity/PlayScenarioRuns`.

## Memory trends and failure evidence

Metrics are opt-in and sampled once after cleanup of each successful iteration. Warmup samples remain
visible but do not contribute to trends. With the defaults, use at least four repetitions: one warmup
plus three comparable samples for two adjacent increases. Every adjacent increase must exceed the
configured threshold; missing/failed samples interrupt a streak. Warnings never change test success
or establish a leak by themselves. No forced garbage collection is performed.

Samples contain managed memory, Unity allocated memory, eligible loaded scene GameObject count, and
the runner's owned subscription/handle counts. They do not count every Unity object, process RSS or
all native allocations. Object inspection is capped at 100,000 entries; unavailable/incomplete samples
carry an error instead of a misleading zero. At most ten samples and bounded warnings are retained.

A failed run captures the active scene, target and last observation before cleanup changes the scene.
With `screenshot_on_failure: true`, an interactive, unpaused graphics Editor can capture the composited
Game view at end of frame. Capture waits at most two seconds, caps source dimensions and writes a
maximum 1,280-pixel-edge PNG sidecar of at most 4 MiB. Textures and callbacks are released afterward.
Batch/headless/unavailable capture records `screenshot_error` without replacing the primary failure.
Retention deletes the report's owned PNG with its JSON. This is diagnostic evidence, not pixel matching.

## Validation limits

- Names use `[a-z0-9][a-z0-9_-]{0,63}`. Main steps: 1-32; setup and cleanup: 0-16 each; total: at most 64.
- Step names: 1-128 characters; step timeout: integer 1-120 seconds, default 30; poll interval:
  integer 100-2,000 ms, default 250. `stable_for_ms`: 0-60,000, strictly below the step timeout.
- Completion quiet window: 0-10,000 ms; cleanup timeout: 1-300 seconds, default 30.
- Scene paths must be canonical `Assets/... .unity` paths outside GameData. Object paths are exact
  active-scene root-relative hierarchy paths, up to 4,096 characters and 128 segments. No traversal,
  empty segments, instance IDs or fuzzy selectors.
- Object count: 0-10,000; component/property path: at most 256 characters; scalar string: at most 1,024.
  Allowed error messages: at most 32 unique nonempty strings of at most 1,024 characters.
- Repeat count: 1-10; main run budget: 1-1,800 seconds. Metrics warmup: 0-9; consecutive increases: 2-9;
  byte thresholds: 0-2,147,483,647; object-growth threshold: 0-10,000.
- Unknown fields, explicit null optional fields, numeric coercion and action-incompatible arguments
  are rejected. New bounded component/property/error-message strings use UTF-16 length limits.

These checks run in the selected Editor. Direct uGUI events do not verify OS input, UI Toolkit,
raycast occlusion or human-visible pixels; repeated runs are not fresh isolated game processes.

## Repeat the native E2E fixture

The maintained `PlayScenarioNativeFlowTests` fixture in `TestProjects/UnityMCPTests` creates owned
menu/button/game/player scenes and performs actual Play Mode transitions. Its additional hardening
cases exercise delayed errors and policies, stable object conditions, setup/cleanup, cancellation,
timeout, metrics, report history and preflight. Use a dedicated test checkout with compatible Unity
and already resolved dependencies. Start in Edit Mode with saved scenes and no active scenario job.
The fixture skips unsafe starting states and restores its owned scene/settings/assets/reports.

In **Window -> General -> Test Runner**, select **EditMode**, find `PlayScenarioNativeFlowTests`, and
choose **Run Selected**. For batch execution use absolute paths:

```text
-batchmode -projectPath <test-project-path> -runTests -testPlatform EditMode
-testFilter "PlayScenario;UguiInputSimulationTests" -testResults <results.xml> -logFile <editor.log>
```

Combine these arguments into one invocation. The project must not already be open. Keep graphics
enabled for actual Editor window tests; do not add `-quit` because the test runner exits on completion.
Retain NUnit XML and Editor logs, checking failures and skips separately. Batch execution verifies the
explicit screenshot-unavailable result, not successful composited screenshot capture.

The isolated verification uses Unity 6000.0.69f1, Test Framework 1.6.0 and uGUI 2.0.0 without changing
the repository test project's dependencies. Synthetic scenes do not prove the user's game flow;
author saved definitions using verified paths to exercise that game.
