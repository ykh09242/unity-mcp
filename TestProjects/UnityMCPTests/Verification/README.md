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

## Previous-version save fixture

`SaveUpgradePlayerBuild` prepares fixture scenes and builds a Windows Mono scenario Player. Use an owned batch copy of this project, Windows Standalone Mono support, and a fresh build directory for each payload. Both Editor entry points require `--mcp-save-upgrade-owned-project`.

1. Run `MCPForUnityTests.PlayScenarios.Player.SaveUpgradePlayerBuild.PrepareFromCommandLine` with `--mcp-save-upgrade-variant Previous`. The resulting bootstrap and fixture scenes and their metadata must be in the previous fixture commit. The save itself is created only by the Player at runtime.
2. Prepare the inputs and dependency closure for that commit using the [reviewed pair instructions](../../../docs/verification-player-reproduction.md). Run `MCPForUnityTests.PlayScenarios.Player.SaveUpgradePlayerBuild.BuildFromCommandLine` with `--mcp-scenario-output <new-absolute-path>` and `--mcp-scenario-source-revision <expanded-source-revision>`.
3. Reuse the owned scenes and run the prepare entry point with `--mcp-save-upgrade-variant Candidate`. The fixture scene changes its serialized version, preserving the scene path and definition. Commit the candidate project inputs and repeat the proof and build steps.
4. Pin the three `SAVE_UPGRADE ` observations below, using `progress.json` for both save paths, `profile` and `coins` as preservation keys, and `Save checkpoint` / `wait_state` as the checkpoint.

| Run | Outcome | Schema | Profile | Coins | Migration count |
| --- | --- | --- | --- | --- | --- |
| Previous | `written` | 1 | `fixture-profile` | 37 | 0 |
| Candidate | `migrated` | 2 | `fixture-profile` | 37 | 1 |
| Restart | `reopened` | 2 | `fixture-profile` | 37 | 1 |

Expect `migration_error: null` for all three positive observations. Build and review separate candidate variants `MigrationFailure` and `DuplicateOnRestart` to exercise native negative controls against the same expectations. A failed migration keeps the schema-1 save and emits its error on each attempted open. The duplicate control migrates successfully once, then records a second migration during restart. Both produce `migration_failed` with the restart's disk-backed observation retained.
