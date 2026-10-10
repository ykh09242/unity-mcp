---
name: unity-play-scenarios
description: Author, save and run repeatable Unity Play Mode scenarios using manage_play_scenario, including menu-to-game scene flows, direct uGUI clicks, bounded waits and failure reports. Use when users want reusable gameplay checks or edits to saved Play Scenarios.
---

# Unity Play Scenarios

Turn a requested game flow into a saved, repeatable scenario: load the menu scene, click Start, wait for the game scene, then check that the player appears. Complete authoring and saving within that requested scope. Run the scenario when execution is requested or implied by the task; do not ask again for authorization already given.

## Connect and discover real paths

- Select the intended Unity instance. If `manage_play_scenario` is unavailable, inspect the `testing` group with `manage_tools(action="list_groups")`. For a hidden group in a stateful MCP session, use `manage_tools(action="activate", group="testing")`. Sessionless activation cannot persist and cannot enable a tool disabled in Unity. After changing Editor tool settings, call `manage_tools(action="sync")` and refresh the available tools.
- Use available scene/asset queries and active-scene hierarchy resources to discover actual scene, UI and object paths. Do not assume the user's game contains `Menu`, `Canvas/Start` or `Player`. Do not query GameData or credential files. If the required scene cannot currently be inspected or a target is ambiguous, draft from the available facts and clarify only the missing paths.
- Targets are **exact hierarchy paths starting at an active-scene root**, not instance IDs. Choose a unique path; duplicate paths are ambiguous. Do not reuse object IDs from before a domain reload.
- `click_ui` dispatches direct uGUI events. It does not support UI Toolkit buttons, OS input, visual occlusion or raycast-hit verification. Confirm the target UI system first.

## Author and save

The first step must be `load_scene`. This example illustrates the format; replace every scene and target with verified project values before using it.

```json
{
  "name": "menu-start",
  "poll_interval_ms": 250,
  "steps": [
    {
      "name": "Load menu",
      "action": "load_scene",
      "scene": "Assets/Scenes/Menu.unity",
      "timeout_seconds": 30
    },
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
      "timeout_seconds": 30
    },
    {
      "name": "Assert player appears",
      "action": "wait_object",
      "target": "Player",
      "timeout_seconds": 30
    }
  ]
}
```

Validate JSON types and paths before saving. Names are lowercase slugs of 1-64 characters (`[a-z0-9][a-z0-9_-]{0,63}`); definitions contain at most 32 steps. Scene actions accept only `scene`, while object actions accept only `target`. Scenes must use canonical, traversal-free `Assets/... .unity` paths outside GameData. Hierarchy paths cannot contain leading/trailing slashes, empty segments, `.` or `..`, backslashes or control characters. Step timeouts are integers from 1-120 seconds; condition polling intervals are integers from 100-2000ms. Reject unknown fields and string-encoded numbers.

Save with `manage_play_scenario(action="save", scenario=<whole definition>)`; do not pass a separate `name` to `save`. Use `list` for names and `get(name=...)` to inspect saved content. Saving the same name replaces that definition, so update only the definition the user intends to change; save variants under a new name.

For Editor authoring, open **Window -> MCP for Unity -> Play Scenarios**. Fill in the scene fields and target paths of the four-step **New** template. Use scene asset pickers and **Use Selection**, then check ordering, timeouts and the polling interval. **Save** persists the current definition; **Save As** creates a named copy. Existing names are locked, and Save As refuses to overwrite another definition. **Run** validates and saves before starting. The window displays step results, errors, the report path and captured logs.

## Run, interpret results and retry safely

`manage_play_scenario(action="run", name="menu-start", repeat_count=3, timeout_seconds=300)` returns a `job_id` immediately. Runs allow 1-10 repetitions and a total timeout of at most 1800 seconds. Choose a budget within the user's task time; only one job can run at once.

Optionally provide a caller-generated 32-character lowercase hexadecimal `job_id` as a request key. After an uncertain response, check `status` for that ID first. Reuse the same ID when deliberately retrying the same request; do not start a duplicate run under a new ID because the response was lost. Keep the returned ID for subsequent `status` and `cancel` calls.

Call `manage_play_scenario(action="status", job_id=...)` every 0.5-1 seconds only while the job is active, stopping at a terminal state or the user's task budget. Unity polls conditions using the definition's interval; do not create a Python background polling task or permanent monitor. At the budget boundary, cancel your own job once if appropriate and report its current state. When the user requests a stop, call `cancel` with the same ID. Cancellation cannot abort an in-flight native scene load.

The outer `success` indicates command handling, not scenario passage. Check that the report's `status` is `succeeded`. Phases are `starting` -> `executing` -> `finished`; run statuses are `running/succeeded/failed/timed_out/cancelled`, and step statuses are `pending/running/passed/failed/timed_out/cancelled/skipped`. On failure, inspect iteration, step_index, detail, poll_count and bounded logs. Report the first failing step and report path; later pending steps may be skipped. Do not describe a non-successful run as passing.

Each repetition reloads the first scene but does not reset static state or DontDestroyOnLoad objects. Unity enters Play if needed and leaves Play unchanged on completion, failure or cancellation. A domain reload after execution begins fails the job instead of automatically replaying effects. Closing the window may leave the backend job running; use Cancel when termination is intended.

## CLI fallback

If MCP calls are unavailable but an existing CLI connection works, use `unity-mcp play-scenario`. Select the intended instance with the global `--instance` option when needed, and use `--format json` to preserve the full result. Installing or starting a new server is outside this skill's own scope.

```sh
unity-mcp --format json play-scenario save menu-start.json
unity-mcp --format json play-scenario run menu-start --repeat-count 3 --timeout-seconds 300
unity-mcp --format json play-scenario status JOB_ID
unity-mcp --format json play-scenario cancel JOB_ID
```

Replace `JOB_ID` with the returned ID. CLI `status` also reads once without waiting. Use `get` and `list` to inspect saved definitions. Summarize what was authored, saved or executed, the actual paths and repetition count, the final state and any failure evidence.
