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
`PlayScenarioStateRegistry` and check it with `wait_state` before and after
input. Raycast hits, pointer callbacks and gameplay state each establish a
different part of the result.

The existing `click_ui` adapters support targeted actions. uGUI raycast mode
does a raycast and directly dispatches uGUI events. UI Toolkit direct mode
submits the resolved Button. Use raw `input` for evidence of cross-backend
routing through the real input module. The fixture covers an Input System
virtual mouse. Hardware input, an OS cursor and per-monitor DPI behavior
require separate evidence.

## Run the licensed fixture

The `mixed-ui-pointer` suite in **Unity Tests** runs through the existing
licensed optional integration job. It requires the exact test case to execute
and pass. License-free compilation checks the C# sources. Read the native
test result for the four observed rows.

To reproduce manually, prepare a fresh optional project with the project's
version-specific dependencies and run the test in a licensed, graphics-enabled
Editor. For example, from the repository root with Unity 6000.0.69f1:

```powershell
$editor = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
$isolated = '.unity-ci/mixed-ui-pointer-example'
python tools/unity_ci_packages.py prepare --unity-version 6000.0.69f1 --unity-data 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' --include-optional --output $isolated
$isolated = (Resolve-Path $isolated).Path
& $editor -batchmode -projectPath "$isolated/project" -runTests -testPlatform EditMode -testFilter MCPForUnityTests.EditMode.Tools.Input.MixedUiPointerRouteTests -testResults "$isolated/results.xml" -logFile "$isolated/unity.log"
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

The inventory state changes in the uGUI Button's `onClick` listener. The
trace comes from the actual pointer callbacks on both stacks. Each JSON row
in the Unity test output includes the scale, viewport, Input Action edges and
device IDs, input and callback coordinates, native callback route, gameplay
state and scenario observations.
The fixture isolates devices with the Input System test runtime. Unity's
player loop updates input and the real UI input module.

See Unity's [runtime UI event system documentation](https://docs.unity3d.com/6000.0/Documentation/Manual/UIE-Runtime-Event-System.html).
