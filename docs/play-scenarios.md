# Saved Play Mode scenarios

`manage_play_scenario` saves repeatable checks such as **menu -> Start -> game scene -> player**.
Unity owns the job, condition waits, step results and bounded diagnostics. `run` returns a `job_id`
immediately; `status` reads once. Individual commands never start background polling or replay effects.
The explicit `suite-run` CLI command waits for its own bounded suite and exports CI reports.

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
For Editor/Player parity, use explicit read-only `wait_state` providers instead of serialized-property
inspection; see [state provider authoring](play-scenario-player-assurance.md#read-game-state-explicitly).
`stable_for_ms` on `wait_object`, `wait_scene` or `wait_state` requires uninterrupted successful observations for that
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
passed. CLI run/status return a nonzero exit code for failed, timed-out or cancelled jobs, or report persistence errors;
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

## Stable targets and raycast clicks

Add a **Play Scenario Target** component to an intended scene object or prefab and assign a unique
`TargetId`. Use `target_id` instead of `target` in that step; the Editor target selector and **Use
Selection** can read an existing marker without modifying the scene. IDs use
`[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` and survive hierarchy renames or reparenting. Resolution is scoped to
the active scene, includes inactive objects and rejects duplicate IDs. An ID condition accepts only
omitted count, zero or one. The existing hierarchy-path selector remains supported.

```json
{
  "name": "Start through the visible UI",
  "action": "click_ui",
  "target_id": "menu.start",
  "click_mode": "raycast",
  "timeout_seconds": 15
}
```

`click_mode` is `direct` by default. `raycast` checks the target's screen-space center against the
configured EventSystem raycasters, waits while the target is blocked or temporarily unavailable,
and dispatches pointer events only when the hit resolves to the intended handler. Once dispatch
starts, the click is never retried. This is simulated EventSystem input, not OS mouse injection or a
standalone Player launch by itself. It needs a suitable uGUI Canvas, GraphicRaycaster and EventSystem; preflight
cannot establish that future runtime geometry will be clickable. Pointer dispatch is synchronous;
a Graphic created during a callback may not enter native raycast results until the next frame.
Use follow-up readiness steps when the click creates new UI.

The implementation follows the [uGUI EventSystem API](https://docs.unity3d.com/Packages/com.unity.ugui@1.0/api/UnityEngine.EventSystems.EventSystem.html)
and [Unity's input-module source](https://github.com/Unity-Technologies/uGUI/blob/main/com.unity.ugui/Runtime/UGUI/EventSystem/InputModules/StandaloneInputModule.cs).

## Assert registered resource release

Enable `resources` to fail when newly registered resources remain after an iteration's cleanup:

```json
{
  "enabled": true,
  "max_scriptable_objects": 0,
  "max_subscriptions": 0,
  "max_handles": 0
}
```

Integrate the opt-in runtime tracker at the game's actual ownership boundaries:

```csharp
using System;
using MCPForUnity.Runtime;

// Register the runtime clone, never its persistent source asset.
PlayScenarioResourceTracker.RegisterScriptableObject(runtimeSettings);

// Retain these small tokens with the owners, not the subscribed delegates or handles.
IDisposable subscriptionRegistration = PlayScenarioResourceTracker.RegisterSubscription();
IDisposable handleRegistration = PlayScenarioResourceTracker.RegisterHandle();

// During real cleanup, first unsubscribe or release the owned resource, then dispose its token.
subscriptionRegistration.Dispose();
handleRegistration.Dispose();
// Destroy(runtimeSettings) when its real owner releases the runtime clone.
```

Registration does not release the game's resources. Token disposal records the application's
release claim; it cannot independently prove that a delegate was removed or a third-party handle
was released. Unregistered resources are outside this assertion. Persistent SO assets are rejected;
pre-existing registrations are the baseline, so intentional long-lived resources can be registered
before the iteration. Thresholds allow a bounded number of new retained resources where intended.

The runner compares registration identities, not net counts: releasing an old resource cannot hide
a new retained one. It samples before the first executed step and after cleanup, including unsuccessful
iterations when execution is still available. `resource_checks` records the result; missing observation
or exceeded limits is an explicit failure. A prior failure remains primary. No global object scan,
per-poll memory sampling or forced GC is added. There are at most 4,096 active registrations and ten
iteration checks. SO liveness uses transient native-ID resolution in the Editor; it does not rely
only on whether a managed wrapper was collected. Register SOs and capture them on Unity's main thread.
Reclaiming dead SO entries when token registration reaches capacity also needs the main thread;
a background registration that cannot reclaim capacity fails explicitly.
Tracking is Editor-only; Player registration calls do no work and return shared no-op tokens.
A registration overflow makes coverage uncertain until the next Play entry; resolve the overflow and
start a fresh Play session before relying on subsequent resource assertions.

## Failure classification and reproduction

Reports retain their existing human-readable `error` and per-step `detail`. The additional `failure`
record identifies a semantic `code`, stage, iteration, step, target and bounded expected/actual values;
`cleanup_failures` preserves secondary problems. Consumers should branch on the code, not parse prose.
`reproduction` stores the normalized definition hash, Unity version and package version. An optional
`source_revision` on run/suite-run records the caller's source label (at most 128 characters); it is
not an independently verified Git revision. Pass the CI checkout SHA explicitly.

Use the stored definition and environment information to reproduce a failure. Compare equivalent
hashes and environments before treating timing differences as a regression. No automatic retries
replace the original result, and evidence export errors must not be reported as a successful CI run.

## Compare saved query counts offline

Use `compare-queries` to turn two saved runs into an explicit query-count budget check for CI.
It reads both input files once and never connects to Unity, starts a Player, replays a scenario,
changes the reports, or follows their embedded artifact paths. Select an individual Editor report
from `Library/MCPForUnity/PlayScenarioRuns`, or a standalone Player `run.json`.

```sh
unity-mcp --format json play-scenario compare-queries baseline.json candidate.json \
  --max-target-searches-increase 0 \
  --max-hierarchy-visits-increase 25
```

Both limits are required non-negative integer counts (at most 9,223,372,036,854,775,807).
The example permits no additional target searches and at most 25 additional hierarchy visits.
Each decision uses `candidate - baseline <= limit`, including equality and negative deltas;
zero is a real count, while a missing/null counter is invalid. No percentages or elapsed-time
thresholds are inferred.

| Exit | Result |
| --- | --- |
| 0 | `within_budget`: both reports are complete and compatible, and neither increase exceeds its limit. |
| 1 | `budget_exceeded`: comparable evidence exceeds at least one supplied limit. |
| 2 | `not_comparable`: invalid/unsupported input, incomplete or failed evidence, or incompatible reports. |

JSON results have `schema_version: 1`, a `status`, baseline/candidate identity and reproduction
labels, and `queries.target_searches` / `queries.hierarchy_visits`. Each counter contains
`baseline`, `candidate`, signed `delta`, `max_increase` and `within_budget`. A `not_comparable`
result contains a bounded `error` and no query decision. Argument syntax errors retain the
CLI's existing stderr diagnostic and exit 2. Text and table output show both budgets and values.

Reports must have the same normalized scenario definition, recorded definition hash, exact Unity
version, execution environment and repeat count. Editor-to-Editor and Player-to-Player comparisons
are supported. Package version and caller-provided source revision may differ; both are displayed.
The stored hash and source label establish recorded compatibility, not authenticity of execution.

Admission requires a finished successful run, released runner resources, an explicit version-1
iteration ledger, and every planned setup/main/cleanup step completed in order for every repetition.
Run, cleanup, export and unexpected-log failures are rejected, including contradictory success
labels. Enabled resource assertions must be complete, successful and within their declared budgets.
A successful report cannot exceed its own enabled native query budget. Player reports additionally
require explicit Player schema 1 or 2, completed finalization, zero exit code and no progress error.
Legacy reports without a ledger, active/partially finalized reports, suite/session summaries,
MCP response envelopes and unknown report versions cannot establish a passing comparison.

Inputs must be regular UTF-8 JSON files no larger than 2 MiB and no deeper than 32 levels. Duplicate
keys, non-finite numbers, links/reparse paths, directories, UNC/device namespaces and protected
data paths are rejected. Namespace checks precede filesystem access; ancestors are checked
from the root before their descendants. Every passed step must record at least one evaluation;
enabled resource measurements must enclose all executed steps in their iteration, including cleanup.
Counters must be non-negative signed 64-bit integers; booleans, floats and numeric strings are
not accepted. URLs, stdin and report directories are not input formats for this command.

Query counts can change with polling, scene state or machine load. Collect equivalent runs and
choose tolerances from repeated evidence before adopting a required CI gate. A lower count does
not prove faster execution; a budget violation alone does not establish its cause. This command
adds no native runner, baseline retention policy or automatic CI job.

## Save and run scenario suites

Add up to 16 unique tags to a scenario, for example `"tags": ["smoke", "resource-lifetime"]`.
Tags use the same lower-case grammar as scenario names. In the Play Scenarios window, use the suite
editor to save an ordered list of scenario names, tag selectors and a stop/continue policy. The
Editor and CLI use the same native suite runner; neither starts multiple Play jobs in parallel.

Save this illustrative definition as `smoke-suite.json` after saving its referenced scenarios:

```json
{
  "schema_version": 1,
  "name": "smoke-suite",
  "scenarios": ["menu-start"],
  "tags": ["smoke"],
  "failure_policy": "stop"
}
```

A suite selects the union of explicit names (in authored order) and matching tags (OR matching, then
ordinal name order), deduplicates it, and freezes the resolved definitions before execution. At most
16 scenarios can be selected; an empty selection fails before Play starts. `stop` skips remaining
scenarios after the first unsuccessful child; `continue` proceeds but preserves the suite failure.
Definitions are not re-read between children. Suite execution reserves the runner until the active
child's cleanup and report finalization finish.

```sh
unity-mcp --instance "MyProject@<hash>" --format json play-scenario suite-save smoke-suite.json
unity-mcp --instance "MyProject@<hash>" --format json play-scenario suite-run smoke-suite \
  --repeat-count 2 --timeout-seconds 300 --source-revision COMMIT_SHA \
  --output-dir reports/play-scenarios
unity-mcp --instance "MyProject@<hash>" --format json play-scenario suite-status SUITE_ID
unity-mcp --instance "MyProject@<hash>" --format json play-scenario suite-cancel SUITE_ID
unity-mcp --format json play-scenario suite-reports --name smoke-suite
```

MCP uses actions `suite_save`, `suite_get`, `suite_list`, `suite_delete`, `suite_run`, `suite_status`,
`suite_cancel` and `suite_reports`. Save accepts `suite`, status/cancel accept `suite_id`, and run uses
`name`, optional `suite_id`, `repeat_count`, `timeout_seconds` and `source_revision`. A suite ID is a
32-character lowercase hexadecimal idempotency key. Reuse it after an uncertain dispatch.

Native suite_run returns immediately; CLI `suite-run` explicitly waits and requires `--instance` to
keep requests pinned to the same Editor. `--timeout-seconds` is the whole suite's execution budget,
not a fresh budget for every child. Cancellation stops the queue, cancels the active child once, and
waits for its bounded cleanup/finalization. Initial Play entry can restore the queue after reload;
executing effects are never replayed. The CLI poll interval defaults to 0.5 seconds and its cleanup
wait defaults to 360 seconds. If observation cannot finish, the exported receipt reports that limit
instead of inventing a successful terminal result.

The output directory contains `suite.json` and `junit.xml`, including skipped cases, original failures,
structured errors, environment information and evidence paths. Exit zero requires a fully successful
suite and successful persistence/export. XML content is escaped and sanitized. Screenshot links refer
to the actual Unity-host file; a remote server cannot turn them into local image evidence. Preserve
the PNGs while they remain in the bounded native report history. Batch/headless screenshots remain
explicitly unavailable.

For an existing CI job with a connected, licensed Editor and authored scenarios, run the CLI command
above and upload the explicit output directory even on failure. For example:

```yaml
- name: Run saved Play scenarios
  env:
    UNITY_INSTANCE: ${{ vars.UNITY_SCENARIO_INSTANCE }}
  run: |
    unity-mcp --instance "$UNITY_INSTANCE" --format json play-scenario suite-run smoke-suite \
      --timeout-seconds 300 --source-revision "$GITHUB_SHA" --output-dir reports/play-scenarios
- name: Retain Play scenario evidence
  if: always()
  uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
  with:
    name: play-scenarios
    path: |
      reports/play-scenarios/
      TestProjects/UnityMCPTests/Library/MCPForUnity/PlayScenarioRuns/*.png
```

Replace the PNG path with the actual same-host Unity project when applicable. This recipe does not
provision an Editor, activate a license or start a server. Existing license-gated native test workflows
remain separate; a skipped licensed job is not a passed game scenario.

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

The default commands run in the selected Editor. Direct uGUI events do not verify OS input, UI Toolkit,
raycast occlusion or human-visible pixels; repeated runs share one process. Use the explicit Player
build/run workflow below to verify a standalone executable.

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
-testFilter "PlayScenario;Ugui" -testResults <results.xml> -logFile <editor.log>
```

Combine these arguments into one invocation. The project must not already be open. Keep graphics
enabled for actual Editor window tests; do not add `-quit` because the test runner exits on completion.
Retain NUnit XML and Editor logs, checking failures and skips separately. Batch execution verifies the
explicit screenshot-unavailable result, not successful composited screenshot capture.

The isolated verification uses Unity 6000.0.69f1, Test Framework 1.6.0 and uGUI 2.0.0 without changing
the repository test project's dependencies. Synthetic scenes do not prove the user's game flow;
author saved definitions using verified paths to exercise that game.

## Reset, query diagnostics and standalone execution

See [scenario execution and CI evidence](play-scenario-execution.md) for explicit `reset_state`
participants, query budgets, bounded timelines, resource owner/call-site attribution, the **Build Player**
workflow, `play-scenario player-run`, and required native E2E mode. These features preserve the existing
bounded polling, once-only effect dispatch, best-effort cleanup and truthful report requirements.
