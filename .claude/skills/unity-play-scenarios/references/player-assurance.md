# State probes and Player assurance

Use the repository's `docs/play-scenario-player-assurance.md` for full contracts and examples.
These options supplement existing scenario steps; never silently replace an unsupported assertion.

## Read-only state

Discover a real registered provider ID before authoring `wait_state`. If game integration is in scope,
implement `IPlayScenarioStateProvider.TryRead(out PlayScenarioStateValue)`, use the Boolean/Integer/
Number/String factories, register with `PlayScenarioStateRegistry.Register(id, provider)`, and dispose
the token at the owner's actual lifetime boundary. Keep reads fast and without side effects. The
registry is weak and capped at 128; registration does not own or keep the provider alive. Register
and read on Unity's main thread. Ordinary Player registration does not activate the scenario runner.

A step uses `state_id`, scalar `state_equals`, optional `stable_for_ms`, name/action/timeout_seconds.
No target, component or reflected property is involved. Initial missing providers and TryRead=false
wait within the timeout. Once bound, loss/destruction/disposal/replacement fails; do not substitute
another object to turn a failure green. The engine reads once per scheduled evaluation. Status,
progress and report inspection must not add reads. Sampled stability cannot prove between-poll state.

## Identity and failure progress

New Player builds use bundle/request version 2 with embedded `build_id` and `build_source_revision`.
Use `--mcp-scenario-source-revision` on the builder to freeze the trusted checkout label. The execution
`--source-revision` is separate caller metadata. The CLI checks all payload files against the bounded
inventory before launch; the Player matches embedded identity. `launcher_admission` describes the
payload verification scope. Neither an echoed hash nor an arbitrary revision label proves a clean
source tree. Pin admission with `--expected-build-revision` and optionally `--expected-build-id`.
Use `--require-verified-payload` on `player-run`; `player-session` always requires version 2.
Legacy version-1 builds remain explicitly unverified and cannot satisfy v2 evidence gates.

Keep request/output directories outside the build. Never repair a hash mismatch by editing the manifest
or accepting another binary silently; rebuild the intended source. Preserve original mismatch evidence.

Inspect progress.json for the last stage, observation, sequence and actual main-loop heartbeat when a
Player hangs or exits without a valid final report. Initial heartbeat zero is startup only. Progress
is bounded and replaced at most once per second, without extra game queries. Use the CLI reader, which
shares deletion and bounds transient Windows file-open retries; never retry malformed evidence. Treat it as
diagnostic evidence; only a matching finalized report and actual process exit can establish success.
Never fabricate run.json or mark missing finalization successful from a heartbeat.

## Longer bounded sessions

Use foreground `play-scenario player-session` for many sequential repetitions. `shared-batches` runs
2-10 iterations per process and then restarts; `fresh-process` starts one process per iteration;
`compare` runs both arms. Keep the batch boundary explicit when describing results: the entire session
does not share one process. Choose iterations (1-1,000 per arm), a session deadline and retained report
count (1-64) within the task. The game still needs its explicit setup/reset/cleanup semantics.

Only one owned child runs. On timeout/cancel, stop new admission and allow bounded cleanup. Preserve
the first failure even when an explicit continue policy collects later observations. No retry-to-green.
The append-only outcome journal and bounded summary/JUnit retain outcomes after detailed child folders
are pruned. Retention includes logs, requests and progress; do not accumulate raw reports in memory.
Incomplete comparison arms are incomplete, not equivalent or successful.

## Required built-Player CI

Use Unity Tests `require_player_e2e:true` when actual standalone execution is required. Windows serial
activation, installed Mono support and the existing licensed-test policy are prerequisites. Missing
prerequisites, skipped jobs, stale receipts or absent/mismatched reports fail required mode. Default
fork checks retain their explicit skip. Do not change repository variables or secrets implicitly.
The maintained harness validates real positive/negative native cases and an ordinary no-define Player;
keep fresh case receipts, hashes, logs and reports on failure. Compile-only checks are separate evidence.
