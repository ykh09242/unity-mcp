# Reproduce a reflected type removed by managed stripping

Use this fixture when a reflective lookup works in the Unity Editor and fails in
an IL2CPP Player. It builds two fresh Windows x64 Players at the same revision
and keeps the scripting backend, managed stripping level, defines, build options
and fixture inputs fixed. The repaired case adds one `link.xml` method root.

## Collect evidence on Windows

Use Python 3.11 or later, PowerShell 7 (`pwsh` on `PATH`), a licensed Unity Editor
matching [`tools/unity-windows-il2cpp.json`](../../tools/unity-windows-il2cpp.json),
its Windows IL2CPP build support module and a compatible MSVC/Windows SDK toolchain.
From the repository root replace the Editor path below with the installed
`Unity.exe`. Choose a fresh output directory for each attempt.

```powershell
python tools/run_managed_stripping_fixture.py run `
  --editor 'C:\Program Files\Unity\Hub\Editor\6000.0.69f1\Editor\Unity.exe' `
  --output .work/managed-stripping-evidence
```

The command creates each Unity project and Player in its own temporary directory
and retains evidence under the chosen output path. It never opens an existing
Unity project. It installs no Editor modules or licenses. The Editor build gets
40 minutes per case by default, configurable with `--editor-timeout`. Each Player
gets two minutes. The fixture runs without a display or physical device.

## Read a completed evidence directory

Use the same source revision as the producing checkout. The verifier runs on any
host with Python and PowerShell 7 and reads CLI metadata from the archived DLLs
without loading or executing them.

```powershell
python tools/run_managed_stripping_fixture.py verify .work/managed-stripping-evidence
```

For a CI-produced artifact, pass `--source-revision` with the exact candidate
commit on both commands and retain the entire evidence directory. The manifest
checks fixture hashes, raw file integrity and the revision label. Obtain the
artifact from a trusted run. The manifest is unsigned.

| Evidence | What the verifier checks |
| --- | --- |
| `editor.log` | Both Editor processes invoke `Read` successfully using the same reflective lookup as the Players. |
| `build-report.json` | Both native builds succeeded with matching settings and distinct build GUIDs. |
| `before-link.dll` and `after-link.dll` | The pre-link assembly contains `ReflectionTarget.Read` in each build. The linker output drops the target type in the broken case and retains the type and method in the repaired case. The probe remains present in both outputs. |
| `player.log` and `launch.json` | Each process emits a fresh probe marker tied to its native build GUID. The broken Player reports `missing_type` and exits `42`. The repaired Player returns `reflection-target-invoked` and exits `0`. The launch receipt records hashes of the native executable, `GameAssembly.dll` and IL2CPP metadata. |
| `repaired/link.xml` | The repair preserves the `Read` method on the reflected type. |

An unavailable build module, missing native output or mismatched observation
makes the tool exit nonzero. Partial logs stay in the output directory. The
fixture needs the pinned Windows Bee input and `ManagedStripped` layouts, so use
the pinned Editor when an upgraded Unity version changes build paths. Missing
evidence leaves the diagnosis inconclusive.

For a project with a similar symptom, use the Player observation to identify the
missing type or member. Compare it with the matching linked assembly and preserve
only what the runtime path requires. Rebuild and launch under the original
backend and stripping settings to check the repair. This fixture covers managed
type removal. A Player that finds the type but fails to invoke its method needs
separate evidence about member stripping or IL2CPP AOT code generation.
See [Unity managed code stripping](https://docs.unity.com/en-us/engine/6000.0/manual/programming-environment/script-compilation/managed-code-stripping)
and the [link XML reference](https://docs.unity.com/en-us/engine/6000.0/manual/programming-environment/script-compilation/managed-code-stripping/xml-formatting).
