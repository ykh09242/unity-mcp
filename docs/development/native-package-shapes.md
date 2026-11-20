# Native optional package shapes

The native runner exercises the current checkout in small, disposable EditMode projects. Each suite selects one exact Editor build and each profile has its own package manifest and project. The Editor versions and package pins select repeatable test environments. Production capability detection continues to inspect the installed APIs.

| Suite | Editor | Profile | Native assertion |
| --- | --- | --- | --- |
| `package` | 6000.0.69f1 | `cinemachine-2` | Create a 2.10.7 virtual camera with its child pipeline, operate the Brain, and reject the unavailable Decollider extension. |
| `package` | 6000.0.69f1 | `cinemachine-3` | Create a 3.1.7 camera with its component pipeline, operate the Brain, and attach the native Decollider extension. |
| `package` | 6000.0.69f1 | `cinemachine-absent` | Verify the basic Unity camera and lens fallback, missing-package readiness, and refusal of Cinemachine actions. |
| `package` | 6000.0.69f1 | `atlas-v1`, `atlas-v2` | Create an atlas with the Editor's 2D Sprite package, then reopen the same project in a second Editor process and verify persisted assets. |
| `package` | 6000.0.69f1 | `addressables-legacy` | With Addressables 1.1.5 and Scriptable Build Pipeline 1.23.4, check unavailable layout data, missing entry writes, and guarded address/group moves with Undo. |
| `package` | 6000.0.69f1 | `auditor-package` | With Project Auditor and its rules at 3.2.0 and Mono.Cecil 1.11.6, require the native audit to report the source fixture diagnostic. |
| `package` | 6000.0.69f1 | `auditor-absent` | Require the missing provider result on an Editor without the integrated module or standalone package. |
| `integrated` | 6000.6.4f1 | `auditor-integrated` | Run the Editor Auditor module with its distributed rules archive 1.0.1 and require the source fixture diagnostic. |
| `integrated` | 6000.6.4f1 | `auditor-integrated-no-rules` | Require the Editor module to reject an audit before starting when rules are absent. |

The camera profiles compare the public inventory tool with the cameras resource. They check component identities, serialized values, object dirtiness and scene dirtiness before and after reads, including a missing legacy pipeline and a scene without a Brain. The package matrix records the [official releases](https://packages.unity.com/com.unity.cinemachine) by SHA-256 in [the registry pins](../../tools/unity-registry-pins.json). The two provider releases declare Unity 2022.3 as their package minimum. The MCP package retains its independent Unity 2021.3 floor.

Run from the repository root with a licensed Editor installation, access to Unity's official package registry, and a new output directory. Atlas phases require a graphics device. A Linux runner needs a working display for those phases.

```powershell
python tools/run_native_package_shapes.py --suite package --editor 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe' --unity-version 6000.0.69f1 --output .work/native-shapes-package
python tools/run_native_package_shapes.py --suite integrated --editor 'C:/Program Files/Unity/Hub/Editor/6000.6.4f1/Editor/Unity.exe' --unity-version 6000.6.4f1 --output .work/native-shapes-integrated
```

Add `--profile <name>` to select a single profile from the suite. Use a new output path for every invocation, including retries. Package preparation verifies official archive digests, package identities and dependency versions, stages only the selected fixture sources, and records their package dependencies in `<profile>/prepared/resolved-packages.json`. The test project and its package lock remain alongside the report.

Each phase retains `launch.json`, `process.log`, `editor.log` and the `results.xml` written by Unity. A phase has a 15-minute process timeout. The runner checks the native exit status, startup build version, exact passing NUnit methods and actual package lock before proceeding. A failed launch or early Editor exit can leave a partial set of artifacts. Inspect the phase's Editor log and process record in that case.
