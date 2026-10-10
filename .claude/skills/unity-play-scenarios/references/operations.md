# Advanced Play scenario operations

Use this reference for registered resource release, stable IDs, saved suites and CI evidence.

## Target selection and input

- A step uses exactly one of `target` or `target_id`. The runtime marker is
  `MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget`, with public `TargetId` and an Inspector field.
- IDs match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`, resolve in the active scene including inactive objects,
  and fail on duplicate matches. `wait_object` with an ID permits count zero/one/omitted only.
- `click_mode` is permitted only on `click_ui`, with `direct` default or `raycast`. Raycast mode checks
  the target's center against configured EventSystem raycasters and waits through temporary blockage.
  It still dispatches simulated pointer events, not hardware input; no standalone Player is launched.
  Dispatch is synchronous: a Graphic created inside a callback may join native raycasts only on the
  next frame. Use follow-up readiness steps when a click creates new UI.
- Read the actual marker/path and runtime UI setup before authoring. Do not infer a working marker
  merely from an example name or add scene components as part of a read-only verification.

## Registered resource release

Add scenario `resources` with `enabled: true` and integer limits `max_scriptable_objects`,
`max_subscriptions`, `max_handles` (each 0..4096, default zero). This explicitly asserts release;
memory trend warnings remain diagnostic and do not themselves fail a scenario.

Game code must register owned runtime clones with
`MCPForUnity.Runtime.PlayScenarioResourceTracker.RegisterScriptableObject(clone)` and destroy them
at the real ownership boundary. Persistent source assets are rejected. `RegisterSubscription()` and
`RegisterHandle()` return tracking tokens; dispose a token only after the game actually unsubscribes
or releases the handle. The tracker never stores the delegate, callback or handle and cannot prove
that a caller's disposal claim is truthful. Unregistered resources are not covered.

Checks compare new registration identities against the pre-iteration baseline after cleanup, including
unsuccessful iterations when possible. They do not subtract net counts, retain strong Unity references,
scan every object, force GC or sample memory every poll. Native SO identity is checked in the Editor;
managed wrapper collection is not proof of native destruction. SO registration/capture belongs on the
Unity main thread. Exceeded capacity or unavailable measurement is a failure, not a zero count.
Registrations are inactive in Player builds and do not allocate tracking entries there.

Do not enable zero limits on intentional retained resources without understanding their lifecycle.
Represent expected retention explicitly and report the scope of the assertion.

## Tagged suites

A scenario may have up to 16 unique lower-case tags with the scenario-name grammar. Save a suite via
`manage_play_scenario(action="suite_save", suite=<definition>)`:

```json
{
  "schema_version": 1,
  "name": "smoke-suite",
  "scenarios": ["menu-start"],
  "tags": ["smoke"],
  "failure_policy": "stop"
}
```

The resolved union preserves explicit order then ordinal tag-match order, deduplicates, and must contain
1..16 scenarios. Tags use OR matching. Definitions are frozen at admission. `stop` skips future children
after failure; `continue` runs them while retaining the failed overall outcome. There is no automatic
retry. One native suite owns the runner until the current child has cleaned up and finalized evidence.

MCP actions are suite_save/get/list/delete/run/status/cancel/reports. Use name for get/delete/run,
optional name for reports and suite_id for status/cancel. suite_run accepts repeat_count1..10,
timeout_seconds1..1800 for the whole suite, optional source_revision<=128 and optional 32-hex suite_id.
It returns immediately. Keep the same suite ID after an ambiguous response; do not launch a duplicate.

```sh
unity-mcp --instance "MyProject@<hash>" --format json play-scenario suite-save smoke-suite.json
unity-mcp --instance "MyProject@<hash>" --format json play-scenario suite-run smoke-suite \
  --timeout-seconds 300 --source-revision COMMIT_SHA --output-dir reports/play-scenarios
```

The explicit foreground CLI waiter produces suite.json and junit.xml, observes at paced intervals,
and sends cancellation once on interruption/timeout before a bounded cleanup wait. The native queue
stops admitting children during cancellation. A lost connection or exhausted observation budget is
reported as unresolved failure; do not claim that remote cleanup completed without its result.

Exit zero requires suite success and successful persistence/export. Preserve failed/timed-out/cancelled
and skipped children in CI reports. Screenshot paths refer to actual Unity-host files, not downloaded
local images. Upload the explicit export directory and accessible original PNGs in the CI job's
always-run artifact step. Do not fabricate screenshots when batch/headless capture is unavailable.

`reproduction` includes definition hash, Unity/package versions and an optional caller source label.
The label is not independently verified. Compare like definitions/environments, use `failure.code`
instead of parsing error prose, and preserve secondary cleanup failures separately from the primary.
