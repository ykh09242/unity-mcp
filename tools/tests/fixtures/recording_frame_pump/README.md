# Recording frame pump factory ownership regression

This fixture compiles the **entire selected production `RecordingFramePump.cs`**, without
method extraction or source rewriting, together with a minimal `UnityEngine` boundary
facade in `Harness.cs`. The installed .NET compiler targets Unity's existing Mono 4.8
reference assemblies; only `mono.exe` runs. It never launches the Unity Editor or installs
packages. Generated executables stay in the explicit task work directory.

The facade injects failure during `hideFlags`, `DontDestroyOnLoad`, `AddComponent`, and
returning null from `AddComponent`, plus failure after attaching a component. Each failure
is repeated 100 times. Tests count remaining owned hosts, exact destroys, attached component
cleanup, callbacks, preserved caller objects, and original exception types. Other cases
cover Play Mode preflight, successful transfer and repeated cancellation, external
`OnDestroy`, clearing callback captures, and cancellation after the coroutine yields.
Unity destroyed-object equality is modeled at the boundary. It is not a native API.

## Run from the repository root

Use installed paths appropriate to the host. The recorded environment was Unity
`6000.0.69f1`, .NET SDK `10.0.401`, and Windows PowerShell.

```powershell
& tools/tests/fixtures/recording_frame_pump/RunRegression.ps1 `
    -UnityData 'C:\Program Files\Unity\Hub\Editor\6000.0.69f1\Editor\Data' `
    -SdkPath 'C:\Program Files\dotnet\sdk\10.0.401' `
    -WorkPath '.tmp/CS-20261009-memory-query-resources/tools-recording'
```

To reproduce the baseline independently without modifying the checkout:

```powershell
New-Item -ItemType Directory -Force -Path '.tmp/CS-20261009-memory-query-resources/tools-recording' | Out-Null
& Server/.venv/Scripts/python.exe -c "import pathlib, subprocess; pathlib.Path('.tmp/CS-20261009-memory-query-resources/tools-recording/BaselinePump.cs').write_bytes(subprocess.check_output(['git', 'show', '43ee728d0691254920e2d8e3c43dcf657b294437:MCPForUnity/Runtime/Helpers/RecordingFramePump.cs']))"
& tools/tests/fixtures/recording_frame_pump/RunRegression.ps1 `
    -UnityData 'C:\Program Files\Unity\Hub\Editor\6000.0.69f1\Editor\Data' `
    -SdkPath 'C:\Program Files\dotnet\sdk\10.0.401' `
    -WorkPath '.tmp/CS-20261009-memory-query-resources/tools-recording' `
    -PumpSource '.tmp/CS-20261009-memory-query-resources/tools-recording/BaselinePump.cs' `
    -Baseline
```

`-Baseline` expects exactly **4 passing cases and 5 failing cleanup cases**. Its zero
exit status means the expected failures reproduced, not that the product passed the tests.
The regular run requires all **9 cases** to pass. Printed hashes identify the compiled
source and facade. The baseline copy in the recorded run exactly matched the commit blob.

## Interpretation and limits

Observed baseline: each failing factory stage leaves 100 hosts after 100 attempts.
Observed fix: all five counts are zero; 9 cases pass, with 1,232 assertions.
Assertions include loop checks and are not 1,232 independent test cases.

This verifies the full managed source's cleanup decisions under specified failure
boundaries. It does **not** demonstrate that native Unity raises each injected error,
measure Unity native memory, execute rendering/coroutine scheduling, or validate actual
`DestroyImmediate` destruction timing. Compiling against native Unity reference assemblies
is a separate check. The Unity Editor/native runtime was not run; this check executes the source-linked boundary model.

The facade initially used its own destroyed-object equality after setting `Destroyed`,
which incorrectly skipped modeled host removal. That test-model defect was corrected to
reference equality before either recorded result; no production change depended on it.
