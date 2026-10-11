# Player state, progress and repeat sessions

Use these controls with the standalone workflow in [scenario execution](play-scenario-execution.md).
The Editor and Player share the same condition engine. A built synthetic fixture checks the harness;
coverage of a real game requires scenarios and state providers integrated with that game.

## Read game state explicitly

Use `wait_state` when readiness belongs to application state rather than a scene object's presence.
Register an exact, case-sensitive ID with an explicit read-only provider:

```csharp
using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

public sealed class PlayerReadyProbe : MonoBehaviour, IPlayScenarioStateProvider
{
    private IDisposable registration;
    public bool IsInitialized { get; set; }

    private void OnEnable() => registration = PlayScenarioStateRegistry.Register("player.ready", this);
    private void OnDisable()
    {
        registration?.Dispose();
        registration = null;
    }

    public bool TryRead(out PlayScenarioStateValue value)
    {
        value = PlayScenarioStateValue.FromBoolean(IsInitialized);
        return true;
    }
}
```

Set `IsInitialized` at the game's actual initialization boundary. Keep providers fast and without
side effects: the engine reads once per scheduled condition evaluation, never on status reads or
progress publication. It does not discover or invoke arbitrary reflected methods or properties.

After the mandatory initial scene load, author this step in the Editor or saved JSON:

```json
{
  "name": "Player initialization completed",
  "action": "wait_state",
  "state_id": "player.ready",
  "state_equals": true,
  "stable_for_ms": 500,
  "timeout_seconds": 30
}
```

The action accepts only the fields shown above. IDs follow `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`.
Equality accepts boolean, signed 64-bit integer, finite number or a string of at most 1,024 UTF-16
units. Numeric equality preserves integer precision; booleans never compare as numbers. Strings
compare ordinally. Use `FromInteger`, `FromNumber` or `FromString` for the other provider value types.

The registry holds weak references and at most 128 registrations. Register and read on Unity's main
thread; token disposal is thread-safe. Registration also works in ordinary Players and never starts a
runner. Retain the provider in its real owner and dispose its registration when that lifetime ends.
Initial absence and `TryRead=false` wait within the step timeout. Once a step has bound to a provider, disposal, replacement or destruction
fails with `state_provider_unavailable`; it never silently switches to a new object. Provider exceptions
fail with `state_provider_error`. Stable waits require uninterrupted matching sampled observations,
not proof of stability between polls. State reads add no hierarchy-query counts.

## Identify the actual build

New explicit builds produce version-2 bundles with an embedded random `build_id` and optional
`build_source_revision`, independent of the execution's caller-supplied `source_revision` label.
Supply the build label through the Editor's source revision field or
`--mcp-scenario-source-revision` on the batch builder. CI supplies its trusted checkout revision.

The external bundle also contains the exact `payload_inventory_json` bytes and their SHA-256
`payload_hash`. Its inventory covers the executable, managed assemblies and data files, including
the embedded identity. Only the external bundle itself is excluded to avoid a self-reference.
The inventory is bounded to 1,024 files, 512 UTF-16-unit relative paths, 1 MiB of inventory text and
16 GiB of total payload. The complete serialized bundle also has a 1 MiB limit. Discovery stops after
4,096 directories, including empty ones. Paths must stay inside the build; links, missing files, unexpected files and
changed contents are rejected. Keep requests and reports outside the immutable build directory.

Before launching, the CLI checks the inventory against the actual payload. The Player verifies the
request against its embedded build identity. `payload_verification: launcher_admission` identifies
the launcher's verification scope; a hash echoed in a native report is not a second runtime hash
verification. This provides identity and tamper detection, not a signed supply-chain attestation or
proof that a caller's revision label describes a clean Git tree. Protect the build after admission.

Version-1 bundles remain usable with explicit legacy/unverified provenance. They cannot satisfy a
version-2 provenance gate. Use `--require-verified-payload` on `player-run` to require version 2;
`player-session` and `player-soak` require it automatically. Pin admission with `--expected-build-revision` and, when
needed, `--expected-build-id`. These checks are separate from the caller label `--source-revision`.
Do not confuse matching scenario-definition hashes with matching binaries.

## Keep progress when finalization fails

Each Player writes bounded `progress.json` beside its request. The first snapshot is startup evidence;
its zero heartbeat is not proof that the main loop ran. Later snapshots are published at most once per
second after actual main-thread updates and include job/build/process identity, a sequence, main-loop
count, monotonic elapsed time, current phase/iteration/stage/step and a bounded last observation.
They do not trigger extra game-state or hierarchy queries.

Publication flushes a bounded sibling file and replaces the previous snapshot. On Windows, the CLI
opens readers with delete sharing and retries only transient file-open errors (2, 32 and 33), up to
three attempts with 10 ms between attempts and a 50 ms elapsed cutoff. It never retries malformed
JSON or other validation failures. Persistent read failures remain explicit diagnostics. A stalled
main thread stops advancing its heartbeat even if the process still exists. The CLI retains
this last observation and its own diagnostic evidence when the process crashes, is terminated, or
fails to produce a valid final report. A missing/invalid progress file is reported explicitly.

Progress never establishes success. A matching final report, completed finalization, released runner
resources and the actual process exit remain required. Cancellation allows bounded cleanup before
terminating an unresponsive owned child. Never manufacture a successful `run.json` from progress.

## Compare bounded repeat sessions

`player-run --repeat-count N` repeats in one Player, with `N` from 1 through 10. For longer foreground
investigations, `player-session` coordinates sequential owned processes:

```sh
unity-mcp --format json play-scenario player-session <build-directory> \
  --output-dir reports/player-session --mode compare --iterations 100 \
  --batch-size 10 --max-runtime-seconds 3600 --retain-reports 16 \
  --expected-build-revision <trusted-build-revision>
```

| Mode | Process boundary |
| --- | --- |
| `shared-batches` | Up to `--batch-size` iterations share a Player; the next batch starts a new one. |
| `fresh-process` | Every iteration starts a new Player. |
| `compare` | Executes both modes and reports their outcomes separately. |

Batch size is 2-10. Iterations are 1-1,000 per selected mode, and only one child runs at a time.
A shared-batch session does not keep one process alive across all batches. It can expose differences
within that batch size; it cannot rule out state retained only after more than ten continuous repeats.
The scenario's explicit setup/reset/cleanup contract still applies inside each batch.

The session deadline stops new admission and cancels an active child once, allowing its bounded
cleanup. Failure stops admission by default. Choosing the explicit continue policy collects subsequent
observations but cannot erase the first failure or turn the overall session into a pass. Incomplete
comparison modes are reported as incomplete, not successful or equivalent.

New native reports contain `iteration_results_version: 1` and one `iteration_results` entry for each
planned repeat. Each entry records its iteration number, status and nullable start/finish timestamps.
The first permitted evaluation, including resource baseline initialization, starts an iteration.
Completion follows cleanup and the resource/metrics boundary. A final native report has no pending or
running iteration. Untouched slots become `skipped` with no start timestamp; a failure between
iterations cannot start the next iteration or run its cleanup.

Session schema version 2 exposes `iteration_counts` separately for each selected mode:

| Counter | Meaning |
| --- | --- |
| `planned` | Requested slots for this mode. |
| `executed` | Iterations with established execution: running, passed, failed, timed out or cancelled. |
| `pending` | Slots not yet admitted while the session is running. |
| `running` | An observed attempt without an established terminal outcome; legacy evidence may retain this state. |
| `passed`, `failed`, `timed_out`, `cancelled` | Explicit terminal iteration outcomes. |
| `skipped` | Slots established as not started in this session snapshot. |
| `unknown` | Slots without enough evidence to establish execution or an outcome. |

The state counters sum to `planned`; `executed` sums the five attempted states. `scheduled_iterations`
counts admitted slots, and the existing `completed_iterations` field remains an alias for `executed`,
including failures. It does not mean that those iterations passed. An arm is complete only when every
planned slot was executed and no pending, running or unknown evidence remains. For example, with six
planned slots and a three-repeat batch failing in iteration two, stopping gives two executed, one
passed, one failed and four skipped. Continuing with a second identically failing batch gives four
executed, two passed, two failed and two skipped. Neither result completes the arm.

Readers still accept older native reports without a ledger. A validated legacy success retains its
previous full-success meaning; unsuccessful reports require actual step starts, never the largest
preallocated iteration number. Missing evidence stays unknown and unresolved earlier attempts stay
unfinished. Outcome rows identify `iteration_results_source` as `native`, `legacy_steps` or
`unavailable`. Session and Player JUnit properties expose the same counters without changing the
existing one-testcase-per-child/session-scenario convention.

Child outcomes are flushed to an append-only JSONL journal before the next admission. The journal has
at most 2,000 bounded records; aggregate memory retains counters, the first failure and the last 32
summaries. Keep 1-64 detailed child output directories using `--retain-reports`; their report, log,
request and progress files share that retention boundary. Finalized child rows retain validated native
PID, actual exit, process timestamps and request hash after pruning; valid native reports also retain
their hash and build identity. Admission failures cannot claim process or report evidence. Summary
and JUnit preserve those outcomes. Compact failure text is bounded by its escaped JSON byte size;
the retained native report keeps the original message. This bounds runner evidence, not the game's
own allocations or files.

Storage failures stop further child admission, including under the continue policy. Outcome counters
advance only after the bounded outcome row is durably appended. Checkpoints and JUnit use sibling
temporary files and atomic publication; a failed write cannot publish a partial replacement. If the
output directory becomes unwritable, the command still fails and recovery uses the preserved journal
prefix. A returned failure cannot guarantee that a terminal file was saved on an unavailable disk.

## Run a long soak in one Player

Use `player-soak` to execute 2-1,000 iterations in one native Player process:

```sh
unity-mcp --format json play-scenario player-soak <build-directory> \
  --output-dir reports/player-soak --iterations 100 \
  --timeout-seconds 300 --max-runtime-seconds 3600 --retain-reports 4 \
  --expected-build-revision <trusted-build-revision>
```

Each iteration runs the complete saved setup/main/cleanup sequence using a one-repeat engine. The
Player PID stays unchanged across iterations; the existing `player-run` limit remains ten. The
scenario must reset game state explicitly where needed. Scene reloads do not clear static state or
`DontDestroyOnLoad` objects. `--timeout-seconds` limits each iteration; `--max-runtime-seconds` limits
the entire soak, followed by bounded cleanup. Defaults are 100 iterations and a one-hour deadline.
The native soak owns this deadline; the launcher separately allows bounded process startup and
cleanup time before terminating an unresponsive owned child.

The default `--failure-policy stop` stops after the first failure. `continue` permits later ordinary
scenario failures to be observed while preserving the first failure and a failing overall result.
Cancellation, an iteration timeout, the outer deadline, resource-release errors and persistence errors always stop the
soak. Untouched iterations count as skipped. A recorded engine that never reached its first permitted
evaluation does not count as attempted or passed.

The owned output directory contains these bounded artifacts:

| File | Evidence |
| --- | --- |
| `iterations.jsonl` | At most 1,000 ordered compact rows, each at most 16 KiB, with identity, PID, actual start/finish, outcome, query counts and optional boundary metrics. |
| `soak.json` | Atomic checkpoint and final aggregate, at most 64 KiB, with explicit `process_scope: same-process`, counts, journal integrity and selected iteration. |
| `iteration-NNNN.json` | Full native reports, each at most 2 MiB. Retention pins the selected attempted iteration and keeps the newest remaining reports within `--retain-reports` (1-64). |
| `run.json` | The first failed attempted iteration, otherwise the last attempted iteration, with its original local ledger and the aggregate soak result attached. |
| `client-soak.json`, `junit.xml` | Launcher assessment and automation result. Missing or inconsistent native evidence remains incomplete and fails the command. |

The aggregate counters distinguish requested, recorded, attempted, passed, failed and skipped.
`attempted = passed + failed` and `requested = attempted + skipped`; recorded also includes any
terminal engine that never started evaluation. A later successful selected report cannot override a
failed aggregate. A successful command requires all requested attempts to pass, matching process and
build identity, ordered journal counts and SHA-256, completed native finalization, released runner
resources and a natural zero process exit. A progress heartbeat or running checkpoint cannot prove it.

Enable metrics in the saved scenario before building to observe memory across the whole soak.
Warmup and increasing-trend counters use global iteration numbers while only the latest ten snapshots
are retained in the aggregate. Missing measurements break a growth sequence and remain unavailable.
Growth warnings are diagnostics, not leak assertions, and do not change a successful scenario result.
Resource assertions still compare explicit registrations at each iteration's existing boundaries.
No extra sampling runs during polling or report viewing. See the
[wire contract](play-scenario-soak-contract.md) for exact fields and bounds.

On a storage failure, inspect `run.json.persistence_error` when available. It identifies
the first failed stage, exception type, bounded message and global iteration. The runner
stops even under `continue`, preserves the original scenario failure, and exits 2. If the
final report cannot be written, the first cause is logged and saved evidence stays incomplete.

## Compare saved soak metrics

Compare two completed successful same-process soaks without launching or sampling again:

```sh
unity-mcp --format json play-scenario compare-soak-metrics \
  reports/baseline/JOB_ID reports/candidate/JOB_ID
```

Pass the actual job directories containing request, definition, process receipt, native
checkpoint, journal, final report and client summary. The command verifies their identities,
hashes, complete counts and natural successful process exit before comparing measurements.
Definitions, metric options, iteration count, time limits and Unity/package versions must
match. Build IDs, payload hashes and revision labels remain visible so different builds can
be compared. Recorded platform/runtime fields must match when present on both sides; absent
fields appear in `environment_unverified`, not as verified equal environments.

Each metric reports aligned post-warmup sample counts, missing/invalid/unpaired observations,
first/last/minimum/maximum, observed net change and growth per iteration. Consecutive sample
segments receive separate intercepts in the trend calculation; gaps are never filled or
joined into a growth streak. Net change is only the difference between the first and last
observed values. Segment detail retains the latest 16 segments and reports the omitted count.
Managed bytes, Unity allocated bytes and scene object counts remain separate measurements.

Schema-1 output has `diagnostic_only: true`. `status: compared` and exit 0 mean that a trend
comparison was computed for at least one metric, not that a build is leak-free or meets a
performance budget. Check each metric's state. `insufficient_evidence` and `not_comparable`
exit 2; missing or invalid evidence is never treated as zero growth. The command does not
rewrite reports, force GC, query the game or create a new test run.

## Recover an incomplete session snapshot

The launcher writes an immutable `session-start.json` and flushes a bounded `admissions.jsonl` entry
before each child launch. It flushes `outcomes.jsonl` after each observed child outcome. The replaceable
`session.json` remains a compact checkpoint, so a damaged or stale checkpoint does not erase the
original plan or establish that an admitted child passed.

Recover into a different output directory:

```sh
unity-mcp --format json play-scenario player-session-recover reports/original-session \
  --output-dir reports/recovered-sessions
```

The command creates a new directory named by `recovery_id`, retaining the original `session_id` for
journal identity. It reads source evidence without launching, replaying, cancelling or changing any
Player, source report or log. It cannot establish that the original process has stopped. Recovery is
an evidence snapshot, never an automatic resume operation.

Valid journal prefixes survive a torn final line. Admitted slots without a trustworthy outcome stay
unknown. Missing or damaged admission evidence also leaves the unclassified remainder unknown;
`recovery_admissions_complete` identifies whether the captured admission journal was intact. Legacy
sessions can be recovered from their existing header, but old aggregate completion values are not
trusted as iteration evidence. Invalid headers, unsafe paths and unsupported schema versions fail
explicitly. Reads reject links/reparse points and enforce the existing row, byte and child bounds.

Recovered summaries have `recovered: true`, an `interrupted` status, bounded `recovery_issues`, and an
explicit incomplete JUnit case. The CLI exits **1**, even if every preserved child passed: producing
a recovery snapshot does not turn an unconfirmed overall session into a successful automated check.
Recovery does not copy native artifacts; recovered rows have no artifact path. Open the original
session separately when its retained logs are needed.

## Inspect Player sessions in the Editor

In **Play Scenarios > Run & results**, expand **Imported Player session**. Choose **Open session.json**
or enter a local **Session file** path, then use **Import snapshot**. The view shows per-mode planned,
executed and outcome counters, compact failure location, and 25 child outcomes per page. Legacy
sessions show their available legacy totals without inventing a missing outcome breakdown.

**View saved steps and logs** reads the selected retained native report, verifies its recorded
SHA-256, and supports stage, iteration and step filters. Logs are paged in bounded sections. The
separate Player-log preview is bounded too. Evicted, missing, mismatched or unsafe artifacts are shown
as unavailable. Recovered snapshots keep their explicit incomplete status and can still be inspected.

Importing, paging and filtering use stored snapshots only. They add no scene/provider/status queries,
file watcher or recurring refresh. Import the snapshot again to see newer persisted results. Version-2
success claims and complete journal counters must agree; contradictory reports are rejected.

Session and soak snapshot imports perform bounded file reads and validation in the background.
Duplicate requests for the same source coalesce; changing the path cancels the previous request
and clears its display. Refreshing the same source keeps its last successful snapshot, explicitly
labeled stale while pending or after a failed refresh. Window close, panel detach and assembly
reload dispose the importer; a superseded completion cannot update the selected view.

Only identified sharing/locking errors or changes to already observed files receive automatic
retries, with at most three attempts and a five-second cancellation deadline. Invalid JSON,
encoding, schema or completed hashes fail immediately. An OS-blocked file read cannot be forcibly
interrupted, but it cannot deliver a result to a disposed view. Retained detail/log previews are
still bounded reads on explicit selection; they are not recurring refreshes.

For continuous single-process runs, expand **Imported same-process Player soak** and open
`soak.json` or `client-soak.json`. **Import soak snapshot** reads the bounded local evidence and shows
the PID, counters, first failure, retained metrics and journal completeness, with 25 iterations per
page. Incomplete, missing or contradictory evidence stays explicit. This native snapshot view cannot
independently establish a natural child exit. Reimport to read newer evidence; paging never rereads
the Player state or adds file watchers.

## Require actual Player CI

Use `require_player_e2e: true` on **Unity Tests** when built Player execution is mandatory. The reusable
Player workflow provisions a pinned Windows Editor with Mono support, prepares an isolated maintained
fixture project, builds the scenario and ordinary Players, and executes the real CLI/native cases.
It uses the existing licensed-test policy and explicit Windows serial activation credentials; it does
not change repository settings or treat unrelated license formats as verified Windows activation.

Required mode fails unavailable prerequisites, a skipped job, failed builds, unexpected exit codes,
missing final reports or stale/missing case receipts. Default license-free fork behavior stays skipped.
The evidence checker validates fresh session identity, real expected positive and negative outcomes,
report/progress hashes, build provenance and the ordinary no-define Player boundary. Logs and evidence
are uploaded on failure as well. A compile result alone cannot satisfy this gate.

The maintained cases include 50 successful same-PID iterations while an actual Editor repeatedly
imports the live journal, plus cancellation during iteration 13 of a 20-iteration request.
They verify background reads, main-thread delivery, multiple changing prefixes observed before
Player exit, final snapshot/hash agreement and disposed Editor hooks. The harness owns and reaps
both processes, including failure paths. Retain the `editor-import` NUnit result, process log,
markers and receipt alongside native artifacts. Even when supplying prebuilt Players to
`tools/run_play_scenario_player_e2e.py`, provide `--editor` and a prepared isolated `--project`
under repository `.tmp` or `.unity-ci` for these actual import cases.
