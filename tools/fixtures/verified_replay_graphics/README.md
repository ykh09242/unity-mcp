# Graphical replay checkpoint

This fixture adds a camera to the isolated trigger-matrix scenario. A native
trigger callback changes the camera clear color from red to green when the mover
enters. The existing independent state provider counts contacts. A named
640 by 360 frame is captured before the scenario requires a count of one.

Run the following in PowerShell 7 from the repository root with a licensed
Unity 6000.0.69f1 Editor, Windows standalone Mono support, a graphical desktop
and the server's locked Python environment. Choose a new output directory.
The existing project generator also accepts an offline package cache.

```powershell
$root = [IO.Path]::GetFullPath('.unity-ci/graphical-checkpoint-example')
$editor = 'C:\Program Files\Unity\Hub\Editor\6000.0.69f1\Editor\Unity.exe'
& tools/fixtures/verified_render_physics/New-FixtureProject.ps1 -Kind physics -Output $root
$project = Join-Path $root 'project'
Copy-Item tools/fixtures/verified_replay_graphics/TriggerFrame.cs "$project/Assets"
Copy-Item tools/fixtures/verified_replay_graphics/GraphicalCheckpointBuild.cs "$project/Assets/Editor"
foreach ($phase in 'repaired', 'blocked') {
    $arguments = @('-batchmode', '-nographics', '-quit', '-projectPath', "`"$project`"",
        '-executeMethod', 'VerifiedWorkflows.GraphicalCheckpointBuild.Build',
        '--fixture-output', "`"$root/$phase-inputs`"", '--fixture-matrix', $phase,
        '--mcp-scenario-output', "`"$root/$phase-build`"", '-logFile', "`"$root/$phase.log`"")
    $job = Start-Process $editor -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($job.ExitCode -ne 0) { throw "$phase build failed. See $root/$phase.log" }
}
```

Capture the passing control and use its returned job directory for both profile
runs. Each run launches a fresh graphical Player under the same fixed pixel
policy. The control compares two separate runs of the repaired build. The
blocked build uses the same replay and different layer collision settings.

```powershell
$env:PYTHONPATH = (Resolve-Path Server/src).Path
$python = (Resolve-Path Server/.venv/Scripts/python.exe).Path
$reference = & $python -m cli.main --format json play-scenario player-run "$root/repaired-build" --output-dir "$root/reference" --graphical --require-verified-payload | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Reference Player failed' }
$baseline = Join-Path $reference.output_directory 'run.json'
@{ schema_version=1; width=640; height=360; channel_threshold=@{ red=0; green=0; blue=0; alpha=0 }; max_changed_pixel_ratio=0; ignored_rectangles=@() } |
    ConvertTo-Json -Depth 5 | Set-Content "$root/policy.json" -Encoding utf8NoBOM
foreach ($phase in 'repaired', 'blocked') {
    $profile = @{
        schema_version=1; name="contact-$phase"; analyze=$false
        execution=@{ kind='run'; graphical=$true; build_directory="$phase-build"; timeout_seconds=30 }
        visual_checks=@(@{
            baseline_report=$baseline; policy='policy.json'
            checkpoint=@{ stage='main'; iteration=1; step_index=2; name='contact-frame' }
        })
    }
    $profile | ConvertTo-Json -Depth 6 | Set-Content "$root/$phase-profile.json" -Encoding utf8NoBOM
    & $python -m cli.main --format json play-scenario regression-run "$root/$phase-profile.json" --output-dir "$root/$phase-report"
    $expected = if ($phase -eq 'blocked') { 1 } else { 0 }
    if ($LASTEXITCODE -ne $expected) { throw "Unexpected $phase profile exit code" }
}
```

Inspect `blocked-report/report.html` for the two captured frames, heatmap,
pixel diff and first failed step. `regression.json` and `junit.xml` carry the
same checkpoint identity, hashes and semantic failure. `*-inputs/build-inputs.json`
lists the per-phase source files hashed into the build revision.
