# Custom Roslyn hierarchy regression

This Windows managed harness compiles the actual `ManageRuntimeCompilation.cs`
source and shared `UnityFindObjectsCompat.cs` with `USE_ROSLYN`; it does not
extract or reproduce the lookup helper.
It uses installed Unity Mono .NET Framework references/Roslyn and the installed
.NET SDK's Newtonsoft.Json and concrete System.Numerics.Vectors implementation,
without downloading packages or launching Unity. Executable-local binding
redirects reconcile the installed Roslyn dependency versions.

```powershell
pwsh -File tools/tests/fixtures/custom_roslyn/RunPathRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401'
```

Pass `-SourcePath <baseline-file>` to run the same tests against a saved baseline.
The runner prints its source SHA-256 and removes its own temporary binaries.
`-WorkPath <directory>` retains binaries in an explicit disposable output directory.
`-UnityDefines <symbols>` selects the compatibility branch; it defaults to
`UNITY_2022_3_OR_NEWER`. Add `-WarningsAsErrors` for strict diagnostics.
Exit 0 means all assertions passed; exit 1 means a regression assertion failed.

Add `-CompanionProbe` to compile the complete `RoslynRuntimeCompiler.cs` instead
and execute its actual static helper. This mode checks explicit missing targets,
omitted/default compiler host targets, and existing named targets. A baseline
companion file can also be supplied with `-SourcePath`.

The scene seam models documented active-only `GameObject.Find`, hierarchical
paths, and inactive child `Transform.Find`. The helper and both real command
callers execute. `compile_and_load` uses real Roslyn emit and assembly loading;
the compiler-instance seam records execution targets and component attachment.
Tests cover missing first/intermediate segments, direct names, active nested
children, inactive children, leading slash paths, and interior empty segments.

This is partial runtime evidence, not Unity Editor execution. It does not verify
Unity lifecycle callbacks, native object null semantics, duplicate-name ordering,
or actual dynamic MonoBehaviour attachment in the Editor. The fixtures live
outside CustomTools so copying that example into Assets does not import doubles.

The companion probe omits `UNITY_EDITOR`, so its real `CompileInMemory` stops at
the documented unsupported-compilation branch. It observes the real compiler's
selected `targetGameObject` and history entry at that boundary, not successful
static method execution. Missing explicit targets must return before compilation
or history changes. The Editor-only compiled assembly/type/method caches start
at null explicitly, so this branch does not emit unassigned-field warnings.

`RunFindFirstRegression.ps1` accepts `UnityData`, `SdkPath`, and `WorkPath` like
the lifecycle runner. It compiles the complete production shim against narrow
legacy, supported, deprecated, and two missing-ordered-API surfaces, then executes
each result. Obsolete APIs are marked errors where reflection is required. Tests
check exact ordered identity against deliberately different enumeration order,
inactive flag roundtrips, runtime Type forwarding, no match, cached overload
selection, observable engine failure, and independent FindAll/FindAny calls.
Missing ordered APIs must throw rather than returning null or selecting any object.
These managed seams do not establish native Unity ordering or runtime behavior.

API references (Context7 manual lookup followed by exact official API fallback):
[GameObject.Find](https://docs.unity3d.com/ScriptReference/GameObject.Find.html)
and [Transform.Find](https://docs.unity3d.com/ScriptReference/Transform.Find.html).
