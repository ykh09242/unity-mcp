# Build preflight regression

This Windows managed harness compiles the production build command, runner, mapping,
job and parameter sources against observable Unity API substitutes. It checks that
invalid option names, malformed option arrays and invalid subtargets are rejected
before backend changes, job registration, callbacks, platform changes or output
folder creation. Valid controls cover builds, batches and Unity 6 build profiles.
Platform probes also cover same-target Player/Server changes, exact no-ops,
omitted/null subtarget preservation, and recovery when the switch returns false
or throws. The managed switch seam records the selected subtarget at invocation;
this does not establish native Unity reimport or compilation behavior.

Run from the repository root with existing Unity Mono reference assemblies and a
.NET SDK. The script downloads nothing and does not start the Unity Editor.
Override `-UnityData`, `-SdkPath` and `-DotnetPath` for local installations.

```powershell
& tools/tests/fixtures/build_preflight/RunBuildPreflight.ps1 -WorkPath .tmp/build-preflight/unity6
& tools/tests/fixtures/build_preflight/RunBuildPreflight.ps1 -WorkPath .tmp/build-preflight/legacy -UnityDefines UNITY_2022_3_OR_NEWER
```

`-WorkPath` receives compiler output and an owned test output path. Use a disposable
directory. Optional `-BaselinePath` supplies alternative `ManageBuild.cs`,
`BuildRunner.cs`, `BuildTargetMapping.cs` and optional `BuildPlan.cs` files for before/after comparisons;
other sources come from the current checkout. The runner prints source hashes and
returns a nonzero exit code on compilation or assertion failure.

The managed boundary does not execute native builds or pump real Editor update callbacks,
or verify native Unity lifecycle behavior. EditMode regressions also live in
`BuildPreparationIntegrityTests.cs`; compiling those tests is separate from running
them in Unity.

## Read-only plans and guarded builds

The harness also compiles `BuildPlan.cs` when present. It calls production `plan` and
`build_from_plan` handling, with managed substitutes for settings, scene metadata,
module support, saving and building. It invokes captured managed callbacks explicitly;
it never invokes the Editor or `BuildPipeline` native implementation.

Checks cover zero-effect valid and invalid plans; explicit empty scenes; bounded inputs;
raw observed backend values; deterministic declared-observation hashes; immediate and
next-update drift; cancellation; and detached caller arrays. Requested backend changes
must occur after the second check, followed by the existing save/build flow. A matching
controlled build records the exact options and returns a synthetic report. The ordinary
build negative control demonstrates that the read-only oracle detects settings,
scheduling, saving and build calls. An old source without `plan` fails the facility
assertion; that failure does not prove deeper drift behavior was exercised.

No UPM/package resolution occurs. Resolving build inputs is part of planning. The hash
does not cover all settings, scene/source contents or dependency contents. Native
read-only observations in `BuildPlanIntegrityTests.cs`, actual builds, module/toolchain
readiness and the supported Editor compile matrix are separate evidence; managed
substitutes cannot establish those outcomes.

For a deliberate negative oracle control, `-DisablePlanGuardControl` routes only the
managed fake-Unity guarded requests through ordinary `build`. The run must fail the
hash/drift/zero-effect assertions and observe fake build calls. This is test-harness
routing only, not a production bypass or native build mode. The normal invocation
retains the original request reference so caller-array detachment is genuinely tested.
