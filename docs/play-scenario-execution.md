# Scenario execution and CI evidence

Use these optional controls when repeats must reset game state, query cost needs a regression limit,
or the scenario must run in an actual standalone Player. Existing saved definitions keep their defaults.
See [saved Play scenarios](play-scenarios.md) for basic authoring, resource registration and suites.
For read-only state probes, build identity, crash progress and longer repeat sessions, read
[Player assurance](play-scenario-player-assurance.md).

## Explicit repeat reset

Implement `MCPForUnity.Runtime.PlayScenarios.IPlayScenarioResetParticipant` at the game's real reset
boundary. Register an exact stable ID and retain its disposable registration token with the owner:

```csharp
using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

public sealed class SessionReset : MonoBehaviour, IPlayScenarioResetParticipant
{
    private IDisposable registration;
    public bool IsResetComplete { get; private set; }

    private void OnEnable() => registration = PlayScenarioResetRegistry.Register("session", this);
    private void OnDisable()
    {
        registration?.Dispose();
        registration = null;
    }

    public void BeginReset()
    {
        IsResetComplete = false;
        // Reset the state owned by this component, then signal actual completion.
        IsResetComplete = true;
    }
}
```

Replace the comment with the application's actual synchronous or asynchronous reset. A component
must stay alive until completion. The registry holds weak references; registration is not ownership.
Disposing a token unregisters that exact registration and never disposes the game state itself.

Place a reset after the first scene load in setup so each iteration begins with it:

```json
{
  "name": "Reset session",
  "action": "reset_state",
  "reset_ids": ["session"],
  "timeout_seconds": 15
}
```

The engine resolves the complete requested set before calling `BeginReset`, calls it once per step,
and only polls `IsResetComplete` afterward. Missing, destroyed, released or replaced participants fail
with `reset_participant_unavailable`. Partial dispatch is never replayed. A timeout or cancellation
still permits configured best-effort cleanup; it does not undo the reset or cancel application work.

Each step accepts 1-16 unique case-sensitive IDs using `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`. The registry
allows at most 128 live registrations and rejects duplicate IDs. This action accepts only `name`,
`action`, `timeout_seconds` and `reset_ids`. Preflight defers runtime registration checks. Scene loads
alone still do not clear arbitrary static state, save data or `DontDestroyOnLoad` objects.

## Query budgets and bounded timeline

Use measurements from representative successful runs to select a budget:

```json
{
  "query_budget": {
    "enabled": true,
    "max_target_searches": 100,
    "max_hierarchy_visits": 20000
  },
  "diagnostics": {
    "screenshot_on_failure": false,
    "record_timeline": true
  }
}
```

`query_counts.target_searches` counts target resolution attempts during actual step evaluations;
`hierarchy_visits` counts inspected nodes, including evaluations that throw. The report contains both
run totals and per-step totals. Status reads, history, preflight and waiting between scheduled polls
add no counts. Counts include cleanup and every repetition. They measure target hierarchy work, not
all Unity, filesystem or game queries.

Budgets are disabled by default, with limits of 4,096 searches and 1,000,000 visits. Valid limits are
integers from zero through 1,000,000 searches and 10,000,000 visits. Zero is an intentional strict limit.
The first exceeded budget produces `query_budget_exceeded`; cleanup remains executable and an
existing primary failure is preserved. This detects an excess after evaluation, rather than aborting
an in-progress hierarchy scan. Hosts also maintain their own per-search inspection bound.

Timeline recording is off by default. When enabled, `timeline` retains the last 128 lifecycle and
changed-observation events, each with a sequence, timestamp, stage, iteration, step index, event and
at most 512 detail characters. Identical polling observations add no event. `dropped_timeline_count`
reports eviction. It contains scalar data, survives report export, and holds no scene references.
The Editor exposes these options and shows recorded details in the selected history report without
adding scene queries or rebuilding unchanged displayed report controls.

## Locate retained resources

Pass an owner label at existing registration boundaries, for example
`PlayScenarioResourceTracker.RegisterHandle(owner: "InventorySession")`. Existing calls remain valid.
Compiler caller information supplies the source basename, member and line; no full source paths or
stack traces are stored. SO registration also snapshots its type and name. Owner/name/basename/member
are bounded to 128 characters, and type to 256.

Each `resource_checks` entry includes at most 32 `retained_resources` descriptions for new identities,
plus `omitted_resource_count`. Rows contain `id`, `kind`, `owner`, `type_name`, `resource_name`,
`source_file`, `source_member` and `source_line`. Releasing an old resource cannot hide a new retained
one. Metadata does not introduce strong resource references. The 4,096-registration cap, sticky
coverage overflow and Editor-only native SO liveness rules still apply. This is attribution for the
explicit tracker, not automatic detection of every leak in the game.

## Build and run a standalone Player

Save a compatible scenario, leave Play Mode, and choose **Build Player** in the Play Scenarios window.
Select an explicit empty output directory outside Assets. Compilation, import, existing builds,
unsaved drafts and active jobs must finish first. Save loaded untitled scenes before an interactive
build. An unmodified batch startup may be replaced by the owned empty bootstrap without saving its
contents; dirty or mixed untitled scene states are rejected before output writes. The project needs Windows x64 standalone
support and its Standalone scripting backend set to Mono. The builder does not change that setting.

For batch builds, use a closed dedicated test project and these arguments to the installed Editor:

```text
-batchmode -quit -projectPath <absolute-project-path>
-executeMethod MCPForUnity.Editor.Services.PlayScenarios.PlayScenarioPlayerBuild.BuildFromCommandLine
--mcp-scenario-name menu-start --mcp-scenario-output <absolute-empty-output-directory>
--mcp-scenario-source-revision <build-commit-sha>
-logFile <absolute-build-log>
```

The explicit build freezes the normalized definition, included scene paths and SHA-256 in
`scenario-bundle.json` beside `MCPScenarioPlayer.exe`. It embeds the same bundle and a dedicated empty
bootstrap scene. A temporary owned asset directory is removed afterward. The
`MCP_FOR_UNITY_PLAY_SCENARIOS` define applies to this build only; ordinary builds never activate the
scenario bootstrap. This uses Unity's
[per-build scripting defines](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/BuildPlayerOptions-extraScriptingDefines.html).

```sh
unity-mcp --format json play-scenario player-run <build-directory> \
  --output-dir reports/player --repeat-count 2 --timeout-seconds 300 \
  --source-revision COMMIT_SHA
```

The CLI runs that executable directly without an Editor instance or MCP server. It creates a unique
owned directory with `request.json`, launches one child process, waits at paced intervals, and exports
its actual `run.json` plus `junit.xml`. Repetitions run in the same Player process with the scenario's
setup/reset/cleanup contract. A new invocation launches a new process.

Cancellation writes one `cancel` marker and allows bounded cleanup. If the process stops responding,
only the owned child may be terminated; the result must remain unresolved failure unless final report
and exit evidence establish what finished. A stale, missing, oversized or mismatched report is never
accepted as success. Exit zero requires matching job/definition/revision, `execution_environment: "player"`, completed finalization, successful status, released runner resources and actual process
exit zero. Scenario failure exits nonzero; invalid startup or persistence also cannot pass. Keep the
whole per-run output directory and build provenance when diagnosing CI failures.

The initial Player host supports exact included scenes, hierarchy paths/stable IDs, object count and
activity, exact component presence, explicit read-only state providers, reset steps, query counts/timeline, and optional direct/raycast
uGUI EventSystem input. It rejects enabled resource assertions, memory metrics, screenshots and all
serialized-property conditions before execution. No reflected property getter is substituted. These
capabilities still belong to the Editor host. Windows Mono is the supported build target here; OS
input, UI Toolkit, IL2CPP and visual pixel comparison are not claimed.

Both bundle versions retain exact canonical `definition_json` bytes so .NET and Python hash the same
input. New version-2 builds also embed a build ID and build-time revision and provide a hashed payload
inventory. The CLI verifies actual binaries/data before launch, while the Player checks its embedded
identity against the bounded request. Legacy version-1 builds remain explicitly unverified for payload
provenance. See [identity and progress contracts](play-scenario-player-assurance.md).

Requests contain job identity, scenario name, definition hash, repeat count, timeout and optional run
source label. Version 2 also binds the build identity and payload attestation. Final reports, progress
and cancellation markers use fixed sibling names; requests cannot select arbitrary report destinations.
Paths reject symbolic-link/reparse traversal, and final evidence never replaces an existing report.

## Require real native CI evidence

Dispatch **Unity Tests** with `require_native_e2e: true`, or pass that boolean to the reusable workflow.
The default remains false so forks can retain their documented license-free checks and native skips.
Required mode fails when licensed execution policy or license prerequisites are unavailable. It does
not alter repository variables or supply licenses. A skipped licensed job is never evidence of E2E.

Required mode checks the maintained `tools/unity-native-e2e-tests.json` manifest against NUnit XML,
a fresh independently initialized session nonce, and explicit receipts emitted after the test body's
final assertions. Receipts reference the actual exported reports by ID, status and SHA-256. The gate
rejects zero execution, skips, stale sessions, missing body receipts, persistence errors and wrong
scenario outcomes. Negative fixtures must produce their expected negative report, not a blanket
success. Suite evidence validates children as well as the aggregate.

For the same local evidence check, initialize before native execution and retain the returned nonce:

```sh
python tools/check_play_scenario_evidence.py <evidence-directory> --initialize
python tools/check_play_scenario_evidence.py <evidence-directory> \
  --results <results.xml> --runner-outcome success --session-id <returned-nonce>
```

The workflow uploads XML, logs, reports, receipts and resolved package metadata even on failure.
Its always-run required gate also checks prerequisite and native-job outcomes. These receipts prove
the maintained synthetic fixture bodies ran; author and execute scenarios using real game paths
before claiming coverage of a particular user's game.
