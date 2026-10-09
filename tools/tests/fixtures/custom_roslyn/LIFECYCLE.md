# Custom Roslyn compiler lifecycle regression

This Windows harness compiles the complete production `RoslynRuntimeCompiler.cs`
and shared `UnityFindObjectsCompat.cs` with `UNITY_EDITOR`. It executes the actual compiler and window methods, including
real installed Roslyn parse/emit and `Assembly.Load`. There is no source extraction
or rewritten implementation. Installed Unity Mono framework/Roslyn and .NET SDK
assemblies are used without downloading dependencies or starting the Editor.

```powershell
pwsh -File tools/tests/fixtures/custom_roslyn/RunLifecycleRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401' `
  -WorkPath 'reports/CS-20261004-sample-lifecycle-probe/lifecycle-current'
```

`-SourcePath <baseline-file>` runs the identical fixture against saved production
source. The runner prints source and harness SHA-256 and returns 0 only when every
assertion passes. `WorkPath` is an explicit disposable output directory: binaries,
copied dependencies and executable-local binding redirects remain for inspection.
The hierarchy and companion runner also compiles the same production shim.
Use `-UnityDefines UNITY_2021_3_OR_NEWER` for the legacy branch or
`-UnityDefines 'UNITY_2022_3_OR_NEWER,UNITY_6000_5_OR_NEWER'` for the deprecated
branch. The default is the supported 2022.3+ branch. `-WarningsAsErrors` rejects
any diagnostic; the deprecated non-generic ordered seam is an obsolete error.

The scene seam tracks exact component identities, inactive helper lookup, children,
object destruction, injected AddComponent null/exception failures and coroutine
start calls. Inert IMGUI seams permit execution of actual `OnGUI`; a named button
can be selected once. No test writes a scene, asset or history export.

Coverage includes adopted user helper preservation; owned hidden helper cleanup;
exact ordered selection among multiple active/inactive helpers for both window
lookup paths, and active-only ordered selection for the static helper;
repeated enables and fallback adoption/creation; serialized-field restoration
model; state synchronization; stale coroutine methods and failed entry resolution;
fresh-Type replacement across target/class interleaving; failed-add preservation;
same-Type remove-before-add ordering; untracked same-name preservation; coroutine
target/host/signature controls; and GUI execution history enabled/disabled.
Invalid empty/whitespace GUI code/type attempts must invalidate previous runnable
state, and the simplified public overload must return its existing validation
error for null source. Input validation keeps its existing no-history policy;
failed compilation or execution still uses the shared history path.
Repeated generated-target attachment failures (invalid input, syntax, missing entry,
and thrown execution) release only the owned target in both play-mode branches.
Caller-owned targets and successfully attached generated targets remain intact.

This is managed partial runtime evidence. Object destroyed-null behavior,
serialization restoration, lookup ordering and destruction timing are modeled.
It does not prove native Unity domain reload, Awake/OnEnable callbacks,
DisallowMultipleComponent enforcement across assembly Types, deferred Play Mode
Destroy, Editor rendering or actual dynamic MonoBehaviour attachment. The same-Type
ordering guard checks production call order only. Adding a fresh compiled version
before destroying its tracked predecessor can briefly coexist and trigger native
component callbacks; those native effects require Editor validation separately.

Unity serialization evidence was retrieved through Context7:
[Serialization and hot reloading](https://docs.unity3d.com/Manual/script-serialization-how-unity-uses.html).
Private object references are explicitly marked SerializeField; the restoration
test copies those fields between managed window instances and does not simulate
an actual Editor script reload.
