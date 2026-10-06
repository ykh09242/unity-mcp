# ExecuteCode managed regression fixture

The runner compiles the selected **actual `ExecuteCode.cs`** together with the repository's responses, assembly compatibility helper, Unity JSON serializer, Unity converters and object ID helper. It runs on the installed Unity Mono runtime with real Newtonsoft.Json, Roslyn, CodeDom and `UnityEngine.CoreModule` math structs. It does not download packages or launch an Editor/server.

```powershell
& tools/tests/fixtures/execute_code/RunExecuteCodeRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401'
```

Use `-SourcePath '<absolute path to baseline ExecuteCode.cs>'` to test an unchanged source snapshot with the same assertions. Optional `-CaseFilter 'performance'` selects case names by a case-insensitive substring; no matching cases returns exit code 2. A failing behavior returns exit code 1; setup/compilation failures throw. Output includes the selected source path/hash, runtime identity, every case and an aggregate result.

Optional `-CompileEditModeTests` additionally compiles the repository's actual `ExecuteCodeTests.cs` against installed NUnit and the same actual production sources, using only a `TestUtilities.ToJObject` seam. It prints the test source hash and a distinct `COMPILE_ONLY` result. This does not execute NUnit or native-dependent Editor tests.

Use `-CaseFilter 'followed by codedom'` for the two isolated recovery cases: a fresh Roslyn compilation failure or success followed by a successful CodeDom return. Both use fresh managed AppDomains, unloaded in `finally`, to avoid changing unrelated cases.

Each backend must execute a basic return, statements without a return and conditional fallthrough ending in a comment; compilation diagnostics must retain the user's line number and runtime exceptions their cause. Cases reject malformed replay/limit/safety tokens without side effects, preserve accepted scalar strings, see an assembly loaded after reference discovery, reuse an identical compiled snippet and retain a regularly touched snippet across 70 unique fillers. Real Vector3/Quaternion values and nested anonymous values must expose numeric components. An invalid compiler must be rejected without execution or history mutation. The cache assertions count actual loaded assemblies containing `MCPDynamicCode`, and an independent counter confirms that cached calls still execute.

Before any explicit Roslyn load, cold auto compilation must discover a compiler assembly loaded during initialization and settle into stable cache reuse. This case runs in an isolated temporary managed AppDomain, unloaded in `finally`, so its Roslyn bootstrap dependencies do not change later backend cases. The child prints `ISOLATED_RESULT`; the enclosing case contributes once to the final aggregate. Subsequent auto and explicit Roslyn calls must return the same compiled module identity. The performance case observes elapsed time and thread allocations for 20 distinct Roslyn compilations after warmup; it verifies the returned values and sets no timing threshold. Compare observations on the same machine/runtime as evidence, not as a portable performance guarantee.

Seams are limited to Editor load attributes, tool registration, console logging and required action extraction. Unity math types and conversion implementations are real. Editor-only asset conversion branches are excluded; scene objects, native Unity APIs, Unity domain reload lifecycle, Editor compilation and transport integration require separate Unity tests. Reload isolation invokes the actual private reload handler between cases. Unity's Mono `4.5` runtime references are intentional: its BCL includes `Math.Clamp`, unlike the shipped `4.8-api` reference assemblies. A baseline serializer may touch native computed quaternion getters and print missing-internal-call diagnostics; the resulting string fallback fails the structured-value assertions. The fixed serializer should pass without those getter calls.

Dependencies are copied into a unique runner-owned temporary directory. TEMP/TMP are set only for the child Mono run so CodeDom artifacts stay there. Environment values are restored and the owned directory is removed in `finally`, including failed runs. The runner never deletes caller-supplied source files. The DLL-loading case ignores only Mono CodeDom's empty-number output artifact; numbered compiler errors or a missing DLL still fail.
