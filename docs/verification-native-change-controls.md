# Reproduce a Unity change on a Windows runner

Open **Actions → Windows Player Assurance → Run workflow**, select the reviewed `beta` revision, keep the `mono` backend, and choose a `native_control`. The quick Player profile is sufficient for running one additional control. Each dispatch provisions an isolated Windows project or distribution and retains the selected control's observations under `native-<control>-<commit>-<attempt>`.

| Control | What to reproduce | Files to inspect |
| --- | --- | --- |
| `project-states` | Two projects use the same package and their own typed state expectations. A second run of the Harbor build exercises an additional loader branch. | `comparison.json`, both projects' `runs/` files, `load-branches/candidate.json`, and `load-branches/comparison.json` |
| `scene-index` | A numeric scene call resolves to another scene after a build-order change, then to the original GUID after repair. | `comparison.json`, each phase's `build.json`, `Assets`, build settings and Player log |
| `enum-value` | The same saved prefab integer takes a different enum meaning after a script change. The Editor reloads the old asset for each version. | `comparison.json` and each phase's `capture` directory with the prefab, script, metadata and `enum.json` |
| `shader-variant` | A specific material renders with the requested variant retained or stripped. The control needs a runner with a usable D3D11 graphics device. | `comparison.json`, build and payload identities, Player measurements and frames |
| `addressables` | An installed Player encounters a missing bundle and then a rolled-back catalog with cold and warm caches. A separate run follows the consumer that retains a handle. | `result.json`, per-run `observation.json`, HTTP requests in `origin.json` and Player logs |

Selecting a native control requires the repository's existing licensed Windows execution policy and credentials. A default no-license run can validate source, compare recorded inputs, and execute approved prebuilt Players when available. Record the native outcome only for a control that actually executed. The separate Editor tests for repeated Play sessions and optional Input System tests retain their NUnit results in Unity Tests.
