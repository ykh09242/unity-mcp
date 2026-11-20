# Verify a Unity project change

Use a project-owned scenario to test the effect of a change that can compile successfully and
still fail in gameplay. Start with a known result, freeze the relevant inputs, and test the
candidate through the project's ordinary scenario. Retain the report and the native evidence
that establishes its verdict. The fixtures below exercise this workflow without modifying
the maintained test project.

## Identify the project and build

Record the package checkout with `git rev-parse HEAD` and check for local changes with
`git status --short`. For a development run, use the package and server from the same
checkout. For an installed release, match the actual server archive to the package pin.
Use `unity-mcp doctor` and a read of a known active scene to check the connection.
Choose the intended Editor explicitly if more than one project is connected.
[Installation](../../website/docs/getting-started/install.md)
· [CLI configuration evidence](../CLI_CONFIG_DIAGNOSTICS.md)

1. Save a project-owned Play scenario with an observable checkpoint. The project can register
   its own state provider and use `wait_state` against an actual gameplay value. Give the
   baseline and candidate distinct IDs and capture exact source-file hashes for both,
   including relevant Unity metadata. Build the saved scenario with each source revision
   in its v2 bundle. [Scenario execution](../play-scenario-execution.md)
   · [Standalone Player assurance](../play-scenario-player-assurance.md)
2. Register the builds in the operator's local `UNITY_MCP_VERIFICATION_ROOT`. Each ID
   has a `candidate.json`, a `build/` bundle and optionally retained copies under
   `sources/`. The registration binds the absolute project root, source and input hashes,
   scenario, build ID and payload hash. Add project-owned asset-to-test/scenario mappings,
   a broader check where appropriate, and an independent expected runtime observation.
   [Registration fields and bounds](../verification-profiles.md#verify-a-registered-standalone-player)
3. Use `current` to require live project inputs to match the build you are testing.
   Use `frozen` to replay a historical build after later edits. A new edit to a selected
   file during either run invalidates the comparison. Report any missing baseline, source
   snapshot, instrumented observation or test mapping. A mapping can recommend several
   checks. The registered Player run executes its selected scenario, so inspect outstanding
   checks before using the report to approve a change.

## Run from MCP or CLI

Open the registered project and connect it to a local MCP server started with
`UNITY_MCP_VERIFICATION_ROOT` set to the absolute registration directory. Use the installed
checkout's server for candidate features. For an HTTP server started from source, run
`uv run --directory Server mcp-for-unity --transport http` in the shell with that environment
variable. Connect the Editor and MCP client to that server. In a second shell, list targets
with `unity-mcp --no-project-context --format json instances`.

Send each row to `manage_verification` with the same explicit `unity_instance`, and keep
the returned handles with the report.

| `action` | `options` |
| --- | --- |
| `prepare` | `{"candidate_id":"candidate","baseline_candidate_id":"baseline","source_mode":"current"}` |
| `run` | `{"preparation_id":"<returned preparation_id>"}` |
| `status` | `{"run_id":"<returned run_id>"}` |
| `report` | `{"run_id":"<returned run_id>"}` |

~~~json
{
  "action": "prepare",
  "unity_instance": "Project@0123456789abcdef",
  "options": {
    "candidate_id": "candidate",
    "baseline_candidate_id": "baseline",
    "source_mode": "current"
  }
}
~~~

The CLI sends the same parameters through the same service.

~~~powershell
$instance = 'Project@0123456789abcdef'
$prepare = '{"candidate_id":"candidate","baseline_candidate_id":"baseline","source_mode":"current"}'
unity-mcp --no-project-context --instance $instance --format json verification prepare --options $prepare
unity-mcp --no-project-context --instance $instance --format json verification run --options '{"preparation_id":"<returned preparation_id>"}'
unity-mcp --no-project-context --instance $instance --format json verification status --options '{"run_id":"<returned run_id>"}'
unity-mcp --no-project-context --instance $instance --format json verification report --options '{"run_id":"<returned run_id>"}'
~~~

Wait for a terminal `data.state`. The transport can successfully retrieve a failed report.
Check the project/Editor identity and running-server provenance, before/after change and
test-selection reasons, and the final verdict. For a Player step, retain the linked native
`run.json`, launcher diagnostics, process result and Player log. Compare the build ID, payload
hash, source revision, scenario definition hash and job ID before accepting a previous report
as evidence.

## Reproduce a loader regression

For native reproduction, run from the repository root with an activated Unity 6000.0.69f1
Editor and Windows Mono build support. Use a fresh
directory under `.unity-ci` for each experiment. The preparation copies the maintained test
project and local package, and the experiment changes only its disposable copy.

~~~powershell
$python = 'Server/.venv/Scripts/python.exe'
$editor = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe'
$output = '.unity-ci/verified-change-example'
& $python tools/unity_ci_packages.py prepare --unity-version 6000.0.69f1 --unity-data (Join-Path (Split-Path $editor) Data) --output "$output/prepare"
& $python TestProjects/UnityMCPTests/Verification/run_prefab_move.py --editor $editor --project "$output/prepare/project" --output "$output/results" --reduced-builds
~~~

The three phases contain the same serialized prefab GUID. The native Editor moves the prefab
and its metadata, then the Player uses the saved scene and its own loader. The fixture
independently checks the loaded object against the serialized reference and counts visible
banner instances.

| Phase | Source evidence | Expected native result |
| --- | --- | --- |
| `original` | Prefab and string key both reference `VerifiedShop/UI/ShopBanner`. | `shop.ready` is true, one banner is visible, Player exits 0. |
| `moved` | The prefab and metadata move to `VerifiedShop/Store/ShopBanner`. The source still uses the old key. | The scene loads. The readiness step times out, the loader returns no object, the banner count is zero and Player exits 1. |
| `repaired` | The loader key follows the new path. The serialized reference still has the same GUID. | `shop.ready` is true, one banner is visible, Player exits 0. |

Read `results/comparison.json` for the phase receipts and follow its relative paths to
`editor.json`, `run.json` and `player.log`. Each phase includes its own registration,
source snapshots and bundle. Open the prepared project after the exercise. In the shell that
starts the server, set `$env:UNITY_MCP_VERIFICATION_ROOT = (Resolve-Path "$output/results").Path`.
In the MCP or CLI sequence above, use `original` as the baseline. Prepare `moved` with
`source_mode: "frozen"` and `repaired` with `source_mode: "current"`. Keep both reports and compare the failed
readiness checkpoint with the independently measured repair.

In the local development run, MCP and CLI each returned `failed` for the moved
build and `passed` for the repair. Each verified a fresh Player job and matched the
fixture's independent banner observation. The moved build reported that current
source differed, as expected for a frozen replay.

Use the same builds to remove an unnecessary step from the failing replay. The shorter broken
build must fail at the original readiness checkpoint, and its shorter repaired control must
pass that checkpoint.

~~~powershell
unity-mcp --format json play-scenario reduce-replay --broken "$output/results/moved/build" --repaired "$output/results/repaired/build" --candidate-pair "$output/results/moved/reduced-build" "$output/results/repaired/reduced-build" --output "$output/reduction"
~~~

Read `reduction.json` for before/after step indices and the matched failure. Open the
job-level reports for the actual and expected state. For a scenario with named captures,
compare matching stage, step, iteration and viewport, then pass the comparison to HTML
export. Missing, stale and wrong-size captures leave the required comparison unsatisfied.
[Replay and report commands](../play-scenario-regressions.md)
· [Graphical replay fixture](../../tools/fixtures/verified_replay_graphics/README.md)
· [Runtime loader evidence](reference-graph-runtime.md)

## Diagnose a blocked click and clipped text

Run the graphical fixture with a Latin/CJK font that contains the fixture glyphs.

~~~powershell
$pointer = '.unity-ci/verified-pointer-example'
& $python tools/verified_pointer_fixture.py --editor $editor --font 'C:/Windows/Fonts/malgun.ttf' --output $pointer
& $python tools/verified_pointer_player_fixture.py --editor $editor --fixture $pointer
~~~

The fixture runs the same raycast-qualified `click_ui` step in both cases. At screen position
`(320, 240)` in the 640×480 viewport, the transparent cover ranks ahead of the button
and the panel stays closed. Setting the cover's `CanvasGroup.blocksRaycasts=false` puts
the button first, and the project-owned state provider reports an open panel after one click.
Inspect `exports/verified-pointer-overlay.json` for hit ranks, pointer coordinates, callback
count and the scenario's `input_blocked` failure. In the standalone fixture,
`player/blocked` and `player/repaired` retain the corresponding native run and independent
witness. This exercises uGUI routing from the scenario runner. An operating-system input
test requires its own observation. [Pointer workflow](verified-pointer-workflow.md)

Open `exports/verified-localized-layout.json` and the two matching PNGs. The long English
and Korean labels clip at 320×240 and fit at 960×480. The report records overflow indices
for the observed TextMesh Pro layouts and identifies the projected device profile as an
estimate. `receipt.json` and `native/results.xml` bind these exports to the Editor run.

## Inspect an interrupted asset edit

Use `preview_prefab` against an explicitly selected project. The preview identifies the
prefab destination, the catalog target and the registration's dependency on the new asset
GUID. Keep the operation ID and receipt metadata when submitting `create_prefab`. If the
response is lost, use
`manage_addressables(action="prefab_status", operation_id="<original operation ID>")` with
the same project, receipt store and caller with a fresh status idempotency key. Inspect
`data.completed_steps` and `data.outstanding_steps`. A confirmed prefab creation with
registration outstanding gives you the prefab path and GUID to confirm before calling
`register_prefab` on the same operation. A conflict
from an intervening edit requires inspecting the reported field and live state.
[Addressable prefab recovery](../addressable-prefab-recovery.md)
· [Execution receipts](../execution-receipts-ledger.md)

Reproduce response loss in the disposable Addressables profile.

~~~powershell
& $python tools/run_native_package_shapes.py --suite package --profile addressables-legacy --editor $editor --unity-version 6000.0.69f1 --output '.unity-ci/verified-recovery-example'
~~~

Find `PrefabRegistrationRecoversDroppedCreationResponseWithoutDuplicatingNativeWrites`
in `addressables-legacy/EditMode/results.xml`. The fixture drops the response after native
asset creation and reconciles through a fresh authenticated connection. Verify the observed
creation and registration outcomes, one asset and one entry, alongside the domain-reload and
replacement-Editor cases. A replacement Editor can inspect current state without inheriting
the previous process's authority.

## Review build cost and compatibility

For a file-size change, retain two completed native build reports from the same target and
settings. Use `manage_build(action="compare_reports", baseline_report_id="...",
candidate_report_id="...")`. Review comparability, the attributed packed asset changes and
the unattributed remainder. If the summary identifies missing or bounded detail, page the
retained sections and report the remaining coverage gap. [Build report tool contract](../../website/docs/reference/tools/core/manage_build.md)
· [Retained build reports](../build-report-storage.md)

The recorded development controls used Unity 6000.0.69f1 with Unity Pro. The loader and replay evidence comes
from Windows x64 Mono Players. The pointer fixture ran in a graphical Editor and a Windows
Mono Player. The localized layout evidence comes from the graphical Editor. The recovery
command uses Addressables 1.1.5 on the same Editor. See the feature matrix for separate
compilation, native Editor and native Player results across Editor roles, stable inputs and
the Android pilot.
[Compatibility evidence](verified-workflows-compatibility.md)

### Verify the pushed revision without a Unity license

Locate the exact pushed SHA in GitHub Actions. Open **Compile Check (no license)** for the
managed package compilation results and **Fork Beta Tool Tests** for the selected Python,
contract and tooling checks. Record each job's conclusion, including skipped native jobs.
The native observations above have their own source, build and process receipts. A new
native observation needs a matching Editor or Player run. Keep local evidence in the
fixture output directory and use the workflow's artifact store for CI evidence.
[CI validation](ci-validation.md)
