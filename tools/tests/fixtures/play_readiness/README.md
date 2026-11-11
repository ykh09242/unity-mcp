# Play mode readiness managed regression

This runner compiles the actual `PlayModeReadiness.cs` with the actual response types and real Json.NET, then executes on the installed Unity Mono runtime. Native Editor, scene and clock signals are controlled seams. The compilation seam separates the raw Editor flag from the authoritative actual-compilation predicate; the existing `EditorStateCache` implementation is not reimplemented or exercised by this fixture.

```powershell
& tools/tests/fixtures/play_readiness/RunPlayModeReadinessRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/11.0.100'
```

Optional `-SourcePath` runs the same assertions against a baseline source snapshot. The deferred raw flag must allow readiness, true compilation must block until the authoritative predicate clears, and the first-frame, asset-update, scene-loading, observed-entry and cancellation conditions must remain intact. No Editor is launched and no compilation or Play Mode transition is requested. Native lifecycle evidence remains the separate `PlayReadinessPersistenceTests` and `PlayReadinessLifecycleTests` fixtures in the isolated Unity test project.

The runner prints the selected source hash and individual results. A failing assertion exits 1; compilation/setup failures throw. All generated output stays in a uniquely named temporary directory, validated and removed in `finally`.
