# Verify pointer routing and localized uGUI layout

Use these fixtures to reproduce a transparent overlay blocking a button and to
inspect localized text at two rendered viewport sizes. The examples use a
Windows installation of Unity 6000.0.69f1 with uGUI 2.0.0. The project and all
results live inside a new directory under `.unity-ci`. Run from the repository
root with the server's Python environment and an installed, licensed Editor.
Supply a proportional font containing the fixture's Latin and CJK glyphs, such
as the locally installed Malgun Gothic shown below.

```powershell
$editor = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
$fixture = '.unity-ci/verified-pointer-example'
Server/.venv/Scripts/python.exe tools/verified_pointer_fixture.py --editor $editor --font 'C:/Windows/Fonts/malgun.ttf' --output $fixture
Server/.venv/Scripts/python.exe tools/verified_pointer_player_fixture.py --editor $editor --fixture $fixture
```

The Editor runner copies the maintained test sources and the installed TMP
shaders into the new fixture, then runs the two selected Unity tests. The
Windows Mono standalone runner builds the saved pointer scenario through
`PlayScenarioPlayerBuild` and uses the same verified payload for the blocked
and repaired processes. Each scenario request, result, process exit and
fixture witness is checked against its job and build identity.

## Diagnose and replay the click

The fixture uses an active `CanvasGroup` with zero alpha over the button. The
Editor reads the current hit order through `manage_ugui` and dispatches the
scenario through `click_ui` with `click_mode: raycast`. The Player observes
the native `EventSystem.RaycastAll` order and uses the runtime scenario
backend. A button listener changes whether the panel is open. An independent
`EventTrigger` records deliveries and the actual event position.

| Control | Native order at the shared position | Scenario | Independent state |
| --- | --- | --- | --- |
| Blocking overlay | Overlay, button | Click times out with `input_blocked` | Panel closed, zero deliveries |
| Repaired overlay | Button | Click and open-state checkpoint pass | Panel open, one delivery at the observed point |

The repair sets `CanvasGroup.blocksRaycasts=false`. Scenario dispatch uses the
native raycast plus synchronous uGUI events. OS input injection is outside
this fixture. To investigate a live scene, adapt the target and pixel
coordinates below. Coordinates are in current Game View pixels with the origin
at the bottom left.

```json
{
  "action": "diagnose_raycast",
  "target": "/UI09Canvas/UI09Button",
  "screen_position": [320, 240]
}
```

In the Editor fixture, look in `exports/verified-pointer-overlay.json` for
the hit order and each replay's step results. The replay begins with the
already-loaded synthetic scene. In the standalone fixture, look in
`player/blocked` and `player/repaired` for `run.json`,
`verified-pointer-witness.json`, and `launch.json`. The standalone replay
includes its saved scene-load step.

## Inspect localized layout

The Editor fixture draws an English label and a Korean label with CJK fallback
glyphs into actual camera render targets sized 320 × 240 and 960 × 480. The
labels use the same anchors, margins and font size at both sizes. At 320 × 240
both labels truncate. At 960 × 480 both fit. In a live scene, let the native
layout settle at each requested viewport and call `manage_ugui` with
`action: diagnose_text` for the selected text root. Check
`native_overflow`, `first_overflow_index` and `native_truncated`.

The fixture writes `exports/verified-localized-layout.json` with the locale,
observed viewport, native TMP metrics, and a SHA-256 for each PNG. The runner
checks the captures against the exact viewport and digest. The separate
390 × 844 `diagnose_device_profiles` result records a virtual DPI geometry
estimate and identifies the TMP provider as unsupported in the geometry preview.

The top-level `receipt.json` identifies the starting checkout, fixture sources,
compiled assemblies and Editor evidence. The Player receipt identifies the
frozen Windows build and its process evidence.
