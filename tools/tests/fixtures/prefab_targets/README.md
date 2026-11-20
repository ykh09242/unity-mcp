# Prefab target regression

Run from the repository root with an installed Unity Editor's `Editor/Data` path in
`$unityData` and the compiler's .NET SDK directory in `$sdkPath`.

```powershell
& tools/tests/fixtures/prefab_targets/RunPrefabTargetRegression.ps1 -UnityData $unityData -SdkPath $sdkPath
```

The runner compiles the current production lookup and prefab-create methods with
controlled scene, Prefab Stage, and asset persistence boundaries. The 17 checks
cover instance IDs, names, absolute and relative hierarchy paths, ambiguity,
inactive objects, stage precedence, and the response when the saved root name
differs from the source instance name. `-SourcePath` accepts a compatible source
snapshot. The runner prints its source hash and runs under Unity Mono in an owned
temporary directory.

The Windows package-shapes smoke step executes the standalone assertions. For
native asset persistence and stage behavior, run `ManagePrefabsCrudTests`,
`ManagePrefabsStageTests` and `PrefabLifecycleIntegrityTests` in Unity EditMode.
