# Build preflight regression

This Windows managed harness compiles the production build command, runner, mapping,
job and parameter sources against observable Unity API substitutes. It checks that
invalid option names, malformed option arrays and invalid subtargets are rejected
before backend changes, job registration, callbacks, platform changes or output
folder creation. Valid controls cover builds, batches and Unity 6 build profiles.

Run from the repository root with existing Unity Mono reference assemblies and a
.NET SDK. The script downloads nothing and does not start the Unity Editor.
Override `-UnityData`, `-SdkPath` and `-DotnetPath` for local installations.

```powershell
& tools/tests/fixtures/build_preflight/RunBuildPreflight.ps1 -WorkPath .tmp/build-preflight/unity6
& tools/tests/fixtures/build_preflight/RunBuildPreflight.ps1 -WorkPath .tmp/build-preflight/legacy -UnityDefines UNITY_2022_3_OR_NEWER
```

`-WorkPath` receives compiler output and an owned test output path. Use a disposable
directory. Optional `-BaselinePath` supplies alternative `ManageBuild.cs`,
`BuildRunner.cs` and `BuildTargetMapping.cs` files for before/after comparisons;
other sources come from the current checkout. The runner prints source hashes and
returns a nonzero exit code on compilation or assertion failure.

The managed boundary does not execute native builds, pump Editor update callbacks,
or verify native Unity lifecycle behavior. EditMode regressions also live in
`BuildPreparationIntegrityTests.cs`; compiling those tests is separate from running
them in Unity.
