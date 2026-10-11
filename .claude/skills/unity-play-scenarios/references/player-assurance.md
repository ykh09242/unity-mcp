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

## Same-process soaks and saved metrics

Use `player-soak` for 2-1,000 iterations in a single Player PID. Keep the engine's local
iteration-1 ledger separate from global journal ordinals; skipped or unstarted iterations
are not attempted. Persistence errors stop even under `continue` and exit 2. Inspect the
first `persistence_error` stage/type/message/iteration in `run.json`, or the Player log when
the final file could not be saved. Preserve the original scenario error and incomplete files;
do not retry an append or relabel unfinished evidence successful.

Use `play-scenario compare-soak-metrics BASELINE_DIRECTORY CANDIDATE_DIRECTORY` for an offline
comparison of two actual successful job directories. It validates native/process/client proof
and matching definition, metrics, counts, time limits and Unity/package versions. Build identities
remain distinct provenance. Missing environment fields are explicitly unverified. Read per-metric
states and missing/unpaired counts; gaps break trend segments. Exit 0 means at least one comparison
was computed, not a leak verdict or budget pass. Insufficient/incompatible evidence exits 2. Do not
launch, query or resample merely to inspect saved metrics unless further execution is in scope.

New runs capture `reproduction.environment` once and reuse it across reports. OS, runtime
platform, architecture, backend, build mode and reported managed runtime version must all
be known and equal for a budget decision. Unknown values remain null; older reports can
omit the object. Do not guess missing metadata from a build target or treat the reported
managed runtime version as an installed Mono/.NET Framework patch level.

For an explicit CI threshold, use `play-scenario check-soak-budget BASELINE_DIRECTORY
CANDIDATE_DIRECTORY --policy POLICY_FILE`. The schema-1 JSON policy selects one to three
metrics (`managed_bytes`, `allocated_bytes`, `object_count`) and requires each metric's
`max_net_change_increase`, `max_slope_increase_per_iteration`, `min_paired_samples` and
`min_consecutive_samples`. Choose limits from the project's repeated comparable observations;
there are no automatic safe defaults. Consult the repository assurance guide for exact
bounds and examples. Consecutive segments too short for the policy are excluded before
testing the paired-sample minimum. Warmup, missing data and gaps never become zero growth.
Exact inclusive comparisons produce `within_budget`/exit 0, `budget_exceeded`/exit 1 or
`not_comparable`/exit 2. Preserve policy hashes and reasons; `leak_verdict` remains false.

Editor session/soak imports are manual background snapshots. Repeated same-source clicks coalesce;
source changes and window lifetimes cancel delivery. A last-good same-source display is labeled
stale during refresh/failure. Only identified file races receive at most three bounded retries;
malformed or contradictory evidence fails. Paging adds no reads or game queries.

Use **Compare saved Player soaks** for two manually imported native snapshots. The cached
metric chart breaks gaps, shades warmup and pages raw values/differences. Known metadata
mismatches or stale/incomplete snapshots suppress differences; unknown environment stays
unverified. This UI does not establish process-exit evidence or replace the CLI budget gate.

## Required built-Player CI

Use Unity Tests `require_player_e2e:true` when actual standalone execution is required. Windows serial
activation, installed Mono support and the existing licensed-test policy are prerequisites. Missing
prerequisites, skipped jobs, stale receipts or absent/mismatched reports fail required mode. Default
fork checks retain their explicit skip. Do not change repository variables or secrets implicitly.
The maintained harness validates real positive/negative native cases and an ordinary no-define Player;
keep fresh case receipts, hashes, logs and reports on failure. Compile-only checks are separate evidence.
The maintained concurrent-import cases require an installed Editor and an isolated prepared project,
including prebuilt-Player runs. Keep their Editor NUnit/process receipts and live-prefix observations;
reading only a completed journal does not establish concurrent import coverage.

Select `player_profile` in CI or `--profile quick|standard|long` in the maintained local
initializer, runner and independent checker. Quick selects 11 core cases; standard has
43 cases; long adds the 1,000-iteration same-PID case. Each profile also requires the three
actual offline budget CLI outcomes against controlled native baseline/growth runs. Limits
are fixed in `tools/unity-player-e2e-profiles.json`; do not weaken the manifest or relabel
partial evidence to pass. Keep the same externally selected profile across initialization,
execution and verification. Local default is standard; long is explicit. No recurring
execution or repository license/secret changes are implied by selecting a profile.
