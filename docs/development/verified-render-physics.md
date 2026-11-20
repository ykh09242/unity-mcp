# Verify a renderer route and a trigger matrix

These Unity 6 fixtures exercise the existing graphics and physics diagnostics in disposable
projects. The renderer fixture verifies saved camera bindings and can render its own camera.
The physics fixture builds the same saved scenario against two project collision matrices and
checks actual trigger callbacks in the standalone Player.

## Prepare the projects

Use PowerShell 7 on Windows, Unity 6000.0.69f1 with Windows Mono build support, the Unity CLI, and
the repository's `unity-mcp` CLI. Use an empty output location outside the maintained Unity
project. The helper accepts an optional `-PackageCache` pointing to an existing directory of
unversioned package folders with the exact pinned versions. Without it, Unity resolves the
manifest's package pins through its normal package manager.

```powershell
$setup = 'tools/fixtures/verified_render_physics/New-FixtureProject.ps1'
$out = Join-Path (Get-Location) '.unity-ci/render-physics-example'
$editor = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
$revision = (git rev-parse HEAD).Trim()
$graphical = & $setup -Kind urp -Output (Join-Path $out 'graphical') -PackageRevision $revision
$headless = & $setup -Kind urp -Output (Join-Path $out 'headless') -PackageRevision $revision
$physics = & $setup -Kind physics -Output (Join-Path $out 'physics') -PackageRevision $revision
```

Each `fixture-inputs.json` records the package revision, manifest digest and fixture source
digests. The local package is a snapshot of that Git commit. The fixture scripts are copied from
the checkout.

## Camera using the wrong renderer

```powershell
unity run $graphical --editor-path $editor --timeout 300 --log-file (Join-Path $out 'graphical.log') -- -force-d3d11 -job-worker-count 2 -forgetProjectPath -executeMethod VerifiedWorkflows.UrpCameraFixture.Run --fixture-output (Join-Path $out 'graphical-report')
unity run $headless --editor-path $editor --timeout 300 --log-file (Join-Path $out 'headless.log') -- -nographics -job-worker-count 2 -forgetProjectPath -executeMethod VerifiedWorkflows.UrpCameraFixture.Run --fixture-output (Join-Path $out 'headless-report')
```

Both launches save and reopen `Assets/RendererBinding.unity` before asking
`pipeline_get_diagnostics` about `FixtureCamera`. The unexpected configuration explicitly
selects renderer 1, whose opaque-layer mask excludes the quad. The repair changes the camera
index to `-1`, selecting pipeline default 0. The camera, pipeline assets, viewport and scene
objects are identical between the two observations in each project.

Inspect `renderer-bindings.json` for the configured and effective indices, renderer asset,
opaque-layer mask and capture status. In a graphical run, the report names and hashes two
128×128 PNGs. The corner remains blue. The center changes from blue to red when the quad
returns. The headless report records `saved_camera_route` and marks both captures unavailable with
`null_graphics_device`. The route comes from the normal read-only diagnostic. The PNGs are a
separate render request made by the fixture.

## Missing 3D trigger callback

```powershell
foreach ($label in @('blocked', 'repaired')) {
    unity run $physics --editor-path $editor --timeout 180 --log-file (Join-Path $out "$label-editor.log") -- -nographics -job-worker-count 2 -forgetProjectPath -executeMethod VerifiedWorkflows.PhysicsTriggerSetup.Configure --fixture-matrix $label --fixture-output (Join-Path $out "$label-editor")
    if ($LASTEXITCODE) { throw "Editor configuration failed for $label" }
    $build = Join-Path $out "$label-build"
    unity run $physics --editor-path $editor --timeout 300 --log-file (Join-Path $out "$label-build.log") -- -nographics -job-worker-count 2 -forgetProjectPath -executeMethod MCPForUnity.Editor.Services.PlayScenarios.PlayScenarioPlayerBuild.BuildFromCommandLine --mcp-scenario-name fixture-trigger-matrix --mcp-scenario-output $build --mcp-scenario-source-revision $revision
    if ($LASTEXITCODE) { throw "Player build failed for $label" }
    unity-mcp --format json play-scenario player-run $build --output-dir (Join-Path $out "$label-player") --timeout-seconds 35 --cleanup-wait-seconds 5 --require-verified-payload --expected-build-revision $revision --source-revision $revision
}
```

The fixture changes only the interaction between layers 8 (`FixtureTrigger`) and 9
(`FixtureMover`) after initial scene and scenario setup. `editor-pair.json` comes from
`manage_physics(action="diagnose_pair")` and records the configuration before simulation.
The Player reads the saved matrix, moves the same kinematic collider through X positions
`-3, 0, 3` with 0.02-second simulation steps, and counts `OnTriggerEnter` calls for that
collider. The Player callback provides a check independent of the Editor's configuration diagnosis.

In each Player's `run.json`, read the controlled inputs and the ignored-layer observation
(`1` for ignored, `0` for allowed). The expected count is one. The blocked run should fail at
`Require one trigger entry` with observed count zero and a CLI failure. The repaired run
should pass with observed count one. Compare the build IDs and payload hashes against the
respective bundles and check that the scenario definition hashes match each other.

The base workflows are described in [renderer diagnostics](../GRAPHICS_DIAGNOSTICS.md),
[scenario execution](../play-scenario-execution.md) and [Player assurance](../play-scenario-player-assurance.md).
