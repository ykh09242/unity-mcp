# Saved Play Mode scenarios

`manage_play_scenario` saves repeatable checks such as **menu → Start → game scene → player**.
Unity owns the running job, condition waits, step results and bounded failure logs. `run` returns
immediately with a `job_id`; `status` reads a report once. The Python tool and CLI do not create a
polling task or replay a side effect after reload.

Enable the Unity tool and activate the `testing` group with
`manage_tools(action="activate", group="testing")` when that group is hidden for your MCP session.
Select the intended Unity instance before running a scenario.

## Use the Editor window

Open **Window → MCP for Unity → Play Scenarios**. Choose **New** to create the four-step
menu/start/game/player template. Its scene fields are initially empty and its object targets are
placeholders: select your menu/game scene assets and set exact targets before saving or running.
Use the scene asset picker or canonical path field, and **Use Selection** to fill an object path.

The window lets you add/delete/reorder steps, edit action-specific scene/target fields and timeouts,
and configure the condition poll interval. Choose **Save** to persist the definition. Existing
scenario names are locked; **Save As** writes a separate named copy and refuses to overwrite one.
**Delete** asks for confirmation. Navigation with unsaved edits offers Save/Discard/Cancel;
closing the window protects unsaved edits as well.

Set repeat count and total timeout, then choose **Run**. The window validates and saves the current
definition before starting the Unity job. **Cancel** requests cooperative cancellation. While the job
runs, status refreshes every 500 milliseconds; the same window shows step results, errors, report
path and captured logs. Its job ID survives window reload. Closing the window does not cancel the
backend job, which may continue running; reopen it to inspect the job or cancel it.

## Define and save

Save this definition as `menu-start.json`, using scene and object paths from your project:

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

The MCP call is `manage_play_scenario(action="save", scenario=<whole definition above>)`.
The definition includes its name; do not supply a separate `name` to `save`.

```sh
unity-mcp --instance "MyProject@<hash>" --format json play-scenario save menu-start.json
unity-mcp --format json play-scenario list
unity-mcp --format json play-scenario get menu-start
```

Definitions live under `ProjectSettings/MCPForUnity/PlayScenarios` and can be versioned with the
project. Storage accepts at most 100 definitions, each at most 64 KiB.

## Run and inspect

```sh
unity-mcp --format json play-scenario run menu-start --repeat-count 3 --timeout-seconds 300
unity-mcp --format json play-scenario status <job_id>
unity-mcp --format json play-scenario cancel <job_id>
unity-mcp --format json play-scenario delete menu-start
```

Equivalent MCP calls:

```text
manage_play_scenario(action="run", name="menu-start", repeat_count=3, timeout_seconds=300)
manage_play_scenario(action="status", job_id="<32 lowercase hexadecimal characters>")
manage_play_scenario(action="cancel", job_id="<same job_id>")
```

An optional `job_id` on `run` is a caller-generated 32-character lowercase hexadecimal request
key. Retain the returned ID and use explicit status calls while the job is running. Only one job
runs at a time. Every repeat begins by loading the first scene. Unity enters Play Mode if needed;
completion, failure and cancellation leave Play Mode unchanged. Repeats do not reset static state
or objects in `DontDestroyOnLoad`.

Reports contain job status, per-step iteration/index/status, timestamps, detail and poll count.
A failed condition stops execution; pending steps are skipped. Failure logs include up to 50
entries, with messages capped at 1,024 characters and stacks at 2,048 characters. The latest report
is retained in memory and up to 20 reports are stored under `Library/MCPForUnity/PlayScenarioRuns`.
Use `--format json` to retain the structured diagnostic report even when the CLI exits with a
failure status.

## Input and runtime boundaries

- Names use `[a-z0-9][a-z0-9_-]{0,63}`. Definitions have 1–32 steps and start with `load_scene`.
- Each step requires a 1–128-character name and a timeout of 1–120 seconds (default 30).
  The condition poll interval is 100–2,000 milliseconds (default 250).
- `load_scene` and `wait_scene` accept only canonical `Assets/... .unity` scene paths, outside
  `GameData`. They do not accept object targets.
- `click_ui` and `wait_object` accept exact root-relative hierarchy paths in the active scene.
  Paths have no leading/trailing slash, empty segment, `.` or `..`, and are bounded to 4,096
  characters and 128 segments. Instance IDs and fuzzy name selectors are unsupported.
- `click_ui` sends direct uGUI events. It does not operate UI Toolkit, inject OS input or verify
  raycast occlusion. Choose a unique target hierarchy; duplicate paths are ambiguous.
- Run repeat count is 1–10 (default 1), and total timeout is 1–1,800 seconds (default 300).
  Unknown fields, numeric coercions and action-incompatible arguments are rejected.
- The initial transition to Play Mode can survive startup domain reload. A domain reload after
  execution begins fails the job rather than replaying steps. Cancellation is cooperative;
  an in-flight native asynchronous scene load cannot be cancelled.

These are bounded functional checks in the selected Editor. They do not provide a fully isolated
fresh game process, a screenshot comparison or proof that the UI is visible to a human player.

## Repeat the native E2E fixture

The maintained fixture is `MCPForUnityTests.PlayScenarios.Integration.PlayScenarioNativeFlowTests`
in `TestProjects/UnityMCPTests`. Use a dedicated test-project checkout with its compatible Unity
Editor and existing package dependencies resolved. Start in Edit Mode with a clean, saved scene
and no active scenario job. The fixture skips unsafe starting states rather than changing an
existing run or discarding unsaved work.

In **Window -> General -> Test Runner**, select **EditMode**, find `PlayScenarioNativeFlowTests`,
and choose **Run Selected**. Its three tests create their own menu/button/game/player scenes and
exercise real Play Mode transitions, two successful repetitions, cancellation and restart, and a
missing-player timeout with captured logs. The fixture restores its prior scene setup and Editor
settings and removes its owned temporary assets and reports.

For a batch run, invoke the matching Unity Editor executable with these arguments, replacing the
paths with absolute paths on the machine:

```text
-batchmode -projectPath <test-project-path> -runTests -testPlatform EditMode
-testFilter PlayScenarioNativeFlowTests -testResults <results.xml> -logFile <editor.log>
```

Combine these arguments into one invocation. The Editor must not already have that project open.
Keep graphics enabled when including `PlayScenarioWindowTests`, which opens an actual Editor
window and checks field/button callbacks. Do not add `-quit`; the test runner exits on completion.
Retain both the NUnit XML and Editor log so a skipped test or setup failure is not mistaken for a
successful scenario.

The native verification for this feature used an isolated Unity 6000.0.69f1 project with Test
Framework 1.6.0 and uGUI 2.0.0. It did not launch the repository's original test project or change
its package versions. The scenes are synthetic; use verified paths in saved scenarios to exercise
a real game. Direct uGUI dispatch and Editor callback tests do not verify screen pixels or OS input.
