# Prefab move control

This test project owns the gameplay names, readiness provider and independent
verdict for one example. The package supplies the general scenario runner and
build verification. A different project can supply its own scene, loader and
state provider.

Run from the repository root with the development Python environment, Unity
6000.0.69f1 and Windows Standalone Mono support. Choose fresh directories for
each execution. The three phases share one disposable project so the original
prefab keeps its GUID.

```powershell
$editor = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
$python = 'Server/.venv/Scripts/python.exe'
$work = '.unity-ci/prefab-move-example'
& $python tools/unity_ci_packages.py prepare --unity-version 6000.0.69f1 --unity-data (Join-Path (Split-Path $editor) 'Data') --output "$work/prepare"
if ($LASTEXITCODE) { throw 'Could not prepare the owned project' }
& $python TestProjects/UnityMCPTests/Verification/run_prefab_move.py --editor $editor --project "$work/prepare/project" --output "$work/results" --reduced-builds
```

| Candidate | Editor after reopening the scene | Windows Player |
| --- | --- | --- |
| Original | The serialized field and the Resources key resolve to the same prefab GUID. | One active banner surface and a passing readiness checkpoint. |
| Moved | The serialized field keeps its GUID. The old Resources key resolves to no object. | Zero active banner surfaces. The readiness checkpoint times out and the process exits with code 1. |
| Repaired | The new Resources key resolves to the original GUID. | One active surface, the expected loaded object and process exit 0. |

Open `comparison.json` in the results directory. Each row points to the native
report and Player log. The adjacent `editor.json` records the scene dependency,
serialized GUID, loader result and digests of the actual source inputs. Each
candidate retains those inputs under `sources/`. The driver hashes live inputs
again after the Player exits. A concurrent edit stops the comparison.

The independent Player observation reads the loaded object's identity and active
UI surfaces. The `shop.ready` provider reports whether the instance is active in
the scene. These observations establish the state of this test fixture. The
driver also requires the expected scenario step, process exit and frozen payload
identity. Pass `--reduced-builds` to retain the shorter candidate replay
alongside each moved and repaired full build.
