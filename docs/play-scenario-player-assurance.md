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
`player-session` requires it automatically. Pin admission with `--expected-build-revision` and, when
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
