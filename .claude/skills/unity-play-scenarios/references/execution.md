# Execution controls and Player authoring

Read the repository's `docs/play-scenario-execution.md` for the complete build and evidence contracts.
Use these controls only when they express the requested scenario; retain existing defaults otherwise.

## Reset between iterations

Discover a real registered `IPlayScenarioResetParticipant` ID. Do not invent it or reflect arbitrary
methods. If game integration is authorized, implement `BeginReset()` and `IsResetComplete`, register
with `PlayScenarioResetRegistry.Register(id, participant)`, and dispose the token at the owner's real
lifetime boundary. The registry is weak and bounded to 128 registrations; the game must own the object.

Place `{"name":"Reset session","action":"reset_state","reset_ids":["session"],"timeout_seconds":15}`
after the mandatory initial `load_scene` in setup. Each repeat then calls BeginReset once and waits
for completion. Use 1-16 unique case-sensitive IDs matching the target-ID grammar. Reset accepts no
scene/target/property fields. Missing or replaced participants fail explicitly; cancellation does not
undo application work, and partial reset dispatch must never be retried automatically.

## Query cost and timeline

Use `query_budget.enabled` after measuring representative target work. The default disabled limits
are `max_target_searches:4096` and `max_hierarchy_visits:1000000`; valid integer ranges are 0..1000000
and 0..10000000. Zero is a strict budget, not unlimited. Totals cover all iterations and cleanup.
Budget excess fails with `query_budget_exceeded`; cleanup still runs. Status reads and preflight do
not add execution counts. Never replace condition waiting with aggressive client-side polling.

Set `diagnostics.record_timeline:true` when changed observations and lifecycle transitions will help
diagnosis. Reports retain the last 128 events with bounded details and a dropped count; unchanged
polls add no event. Inspect `query_counts`, per-step counts, timeline and the first structured failure.
The Editor exposes these options and selected history details without new background scene scans.

For retained registered resources, inspect up to 32 `retained_resources` owner/call-site descriptions
and `omitted_resource_count` per iteration. Optional `owner:` labels supplement automatically captured
source basename/member/line. Registration captures scalar metadata, not full paths, stacks or strong
resources. Do not represent tracker attribution as automatic whole-game leak detection.

## Actual standalone execution

Save a compatible scenario and use **Build Player** with an empty explicit output directory. The
project needs installed Windows x64 support and a Mono Standalone backend. Do not change backend,
install modules or overwrite builds implicitly. The builder freezes definition/hash/scenes into the
bundle and uses a per-build test define; ordinary Players never auto-start this runner.

Player supports scene/object/component-presence checks, explicit read-only state providers, stable IDs, reset, query budgets, timeline
and optional native uGUI direct/raycast dispatch. Reject resources.enabled, metrics.enabled,
screenshot_on_failure and every property condition before execution. Use Editor execution when
those are required. Never silently drop unsupported checks to make a Player run pass.

```sh
unity-mcp --format json play-scenario player-run <explicit-build-directory> \
  --output-dir reports/player --repeat-count 2 --timeout-seconds 300 \
  --source-revision COMMIT_SHA
```

This command does not need a connected Editor or MCP instance. It launches one owned executable and
exports actual run JSON/JUnit in a unique output child directory. Repeats share that process; explicit
reset remains necessary. On interruption, cancel once and permit bounded cleanup; only the owned
process may be terminated if unresponsive. Missing/stale/mismatched reports, export failures and
nonzero exits are failures. Do not infer cleanup or success from process launch alone.

Check actual terminal status, job ID, definition hash, execution_environment=player, finalization,
runner resource release and process exit. A source_revision remains caller metadata. Retain the
output directory and provenance. A built synthetic fixture proves the harness; it does not prove
user-game fidelity, OS mouse input, UI Toolkit, IL2CPP or screenshot pixel agreement.

## Required native CI

Use Unity Tests `require_native_e2e:true` when native E2E is required. It fails unavailable policy or
license prerequisites, skipped/zero required methods, missing end-body receipts, stale sessions and
bad exported reports. Preserve default fork skips when strict execution is not requested. Never
change repository policy or expose license secrets implicitly.

Initialize the evidence session before native tests, keep the returned nonce separately, and check
NUnit XML plus exact required body receipts and actual report hashes afterward. Upload evidence on
failure too. Distinguish expected negative test outcomes from a failure to execute. A successful
compile or test runner process alone is never proof that the scenario body reached its assertions.
