# Verify mixed uGUI and UI Toolkit pointer routing

Use a single Game View and the project's existing `EventSystem` with
`InputSystemUIInputModule`. Enable the Input System Package or Both in Active
Input Handling and bind the module's Point and Left Click actions to the
mouse. A Play Scenario can send separate `input` steps for mouse move, press
and release. Positions use Game View pixels measured from the bottom left.

Subscribe to the actual UI callbacks before sending input. Use
`RoutedPointerTrace` from the optional `MCPForUnity.Input.UGUI.Runtime`
assembly on the Unity main thread. Call `WatchToolkit` for the element and the
ancestors you want to observe. In your existing uGUI pointer handlers, pass
the event and receiving GameObject to `RecordUgui`. Use unique names within
the observed scope. The trace labels UI Toolkit callbacks with their target,
receiving element and propagation phase. For uGUI it records the current
raycast hit and receiving GameObject separately. UI Toolkit callback
coordinates are panel units measured from the top left. Convert the same
screen point with `RuntimePanelUtils.ScreenToPanel` after flipping its Y
coordinate against the current viewport height.

Read the samples before calling `Clear` for the next interaction. The trace
retains the first 128 samples across at most 16 watched UI Toolkit elements.
A truncated trace is incomplete evidence. Dispose the trace before releasing
the UI to remove its callbacks. Register the gameplay state separately with
`PlayScenarioStateRegistry` and check it with `wait_state` before and after input.
Record the application's state directly at these checkpoints.

The existing `click_ui` adapters support targeted actions. uGUI raycast mode
does a raycast and directly dispatches uGUI events. UI Toolkit direct mode
submits the resolved Button. Use raw `input` for evidence of cross-backend
routing through the real input module. The fixture covers an Input System
virtual mouse. Hardware input, an OS cursor and per-monitor DPI behavior
require separate evidence.

## Compare two observed clicks

Save one JSON object per attempt. Use the same input action, coordinate
convention, callback scope and gameplay probes for both attempts.

| Field | Evidence to save |
| --- | --- |
| `schema_version` | Integer `1` |
| `input_action` | A stable gesture label such as `mouse_left_click` |
| `viewport`, `screen_point` | `[width, height]` and intended `[x, y]` in current Game View pixels with a bottom-left origin |
| `input_action_edges` | Ordered Input Action observations with `control`, `pressed` and the device's actual `[x, y]` `position` in Game View pixels |
| `events` | Ordered callbacks with `backend` (`ugui` or `uitoolkit`), `event` (`Down`, `Up`, `Click`), `phase` (`handler`, `target`, `trickle_down`, `bubble_up`), `target`, `receiver` and integer `button` |
| `trace_truncated` | `true` if the callback capture exceeded its bound |
| `gameplay_state` | `before` and `after` objects with the same named scalar probes, read directly from application state |

```sh
unity-mcp --format json play-scenario compare-pointer-traces \
  before.json after.json --output comparison.json
```

The command divides each intended screen point by its own viewport. It aligns
the two clicks when both normalized coordinates differ by at most `0.0001` and
their action labels match. It compares raw press/release edges first, then the
first observed receiver, the last observed `Click` on a UI Toolkit target or
uGUI handler, the remaining ordered callback route and the independent game
state. Panel and callback coordinates are available in the fixture rows for
inspection. The verdict compares the normalized input and raw edge positions,
and uses callback identities and phases to compare delivery. Frame numbers and
per-run device IDs are diagnostic metadata.

Exit `0` means the compared stages match. Exit `1` reports the first divergent
stage and the observations on both sides. The summary also retains the first
recipient, terminal click handler and the before/after gameplay readings.
Exit `2` means incomplete evidence. The reader accepts up to 128 raw edges
and 128 routed callbacks per trace. A truncated trace reports which side
truncated and how many callbacks it retained. An action or coordinate mismatch
or two traces with no raw input produce `no_common_input`.
Each comparable click requires exactly one press followed by a release of
the same control. Both raw edges must match that attempt's intended point
within the normalized tolerance. An incomplete or off-target click produces
`incomplete_click` and identifies the affected side. Any difference between
the recorded raw edges is included as `observed_raw_difference`.

## Run the licensed fixture

The `mixed-ui-pointer` suite in **Unity Tests** runs through the licensed
optional integration job. It exports the four observations from the exact
passing native test and runs `compare-pointer-traces` on both scale pairs.
License-free compilation checks the C# sources.

To reproduce manually, prepare a fresh optional project with the project's
version-specific dependencies and run the test in a licensed, graphics-enabled
Editor. For example, from the repository root with Unity 6000.0.69f1:

```powershell
$editor = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
$isolated = '.unity-ci/mixed-ui-pointer-example'
python tools/unity_ci_packages.py prepare --unity-version 6000.0.69f1 --unity-data 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' --include-optional --output $isolated
$isolated = (Resolve-Path $isolated).Path
& $editor -batchmode -projectPath "$isolated/project" -runTests -testPlatform EditMode -testFilter MCPForUnityTests.EditMode.Tools.Input.MixedUiPointerRouteTests -testResults "$isolated/results.xml" -logFile "$isolated/unity.log"
python tools/export_mixed_ui_pointer_traces.py "$isolated/results.xml" "$isolated/traces"
foreach ($scale in '1x', '1.5x') {
    unity-mcp --format json play-scenario compare-pointer-traces "$isolated/traces/$scale-before.json" "$isolated/traces/$scale-after.json" --output "$isolated/traces/$scale-comparison.json"
    if ($LASTEXITCODE -ne 1) { throw "Expected a pointer routing difference at $scale" }
}
```

The test enters Play Mode and creates a transparent, pickable UI Toolkit
element over a uGUI inventory button. Both are at the same Game View position.
The exact position is computed from current native geometry at 1× and 1.5×
UI scale. The scenario sends the same raw mouse sequence to the same point
before and after changing the overlay's `pickingMode` to `Ignore`.

| Control | Independent recipient and propagation | Scenario state checkpoint |
| --- | --- | --- |
| Pickable overlay | UI Toolkit ancestor, overlay, ancestor for down, up and click | Inventory remains closed and the open-state check times out |
| Overlay ignores picking | uGUI button receives down, up and click at the same screen pixel | Inventory opens exactly once and the open-state check passes |

Both scale pairs should report `first_recipient` as the first divergence.
The blocked click first reaches the UI Toolkit ancestor during trickle-down.
The repaired click first reaches the uGUI button handler. The ordered down,
up and click traces and the independent inventory checks are asserted by the
native fixture.

The inventory state changes in the uGUI Button's `onClick` listener. The
trace comes from the actual pointer callbacks on both stacks. Each JSON row
in the Unity test output includes the scale, viewport, Input Action edges and
device IDs, input and callback coordinates, native callback route, gameplay
state and scenario observations.
The fixture isolates devices with the Input System test runtime. Unity's
player loop updates input and the real UI input module.

See Unity's [runtime UI event system documentation](https://docs.unity3d.com/6000.0/Documentation/Manual/UIE-Runtime-Event-System.html).
