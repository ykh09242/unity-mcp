# Editor readiness and isolated previews

## Compilation and refresh

`refresh_unity` reports `data.no_op=true` with a reason when neither refresh nor
compilation was dispatched. This is distinct from a dispatched incremental
compilation which found no assemblies to rebuild.

`data.compilation.status` (also available from `get_compile_job`) is:

| Status | Evidence |
| --- | --- |
| `compiled` | Complete, request-associated assembly compilation without errors. |
| `already_up_to_date` | Complete, request-associated cycle with no assemblies or errors. |
| `failed` | Complete, request-associated compilation with errors. |
| `pending` | Request or compilation still in progress. |
| `unknown` | Missing, ambiguous or incomplete evidence. |

The existing `outcome` and native `compile_job@1` contract remain compatible.
`last_successful_compilation` is optional, session-scoped historical evidence.
Neither that history nor readiness proves that a specific source revision is
loaded. A timed-out observation does not cancel compilation or replay refresh.

## Test and console observations

New test runs wait for fresh readiness observations to remain stable for 750 ms,
including import/reload state and initial external scan coverage. The native
runner rechecks readiness immediately before execution. Subsequent external
edits can still interrupt a run; tests are never automatically replayed.

Before reload, completed test records are checkpointed. Expected test-runner
reloads can resume with progress intact. An interrupted run reports
`completion_kind=interrupted_by_reload` where reload evidence exists, retaining
partial results and the expected test count. This is an execution interruption,
not an assertion failure. Completed records survive callback errors and accepted
cancellation as well as reload. Replay is reconciled by run-scoped test identity,
not display name; older records without identity remain readable. A shorter resumed
summary cannot replace the original expected count or erase earlier failures.
Existing native-control release evidence still applies; CLI output also identifies
cleanup that has not yet released native ownership.

Console reads distinguish `observation.status=available` (including an empty
console) from `unavailable`. A transient readiness rejection gets one bounded
three-second readiness recovery before another read. Console clear is not part
of that retry path. A reconnect continuation is restricted to the same console
read, owner and project and retains the original deadline. It cannot replay a
clear or an unrelated operation.

External scan diagnostics include start time, entries and bytes processed,
elapsed milliseconds and a suggested polling delay. These describe the current
scan pass, not a guessed total or percentage. An active scan recommends
`wait_for_external_changes_scan`; limited or unknown local coverage instead
requires `inspect_scan_scope` and does not recommend endless polling. Native
readiness remains a separate signal and its blockers take priority.

## Response schemas

Object-valued built-in responses are emitted directly at `structuredContent`.
Existing `success`, `message`, `error` and `data` fields retain their meanings;
consumers should stop relying on an SDK-added `result` wrapper. Scalar/list
results keep the SDK's required wrapper. `asset_edit` advertises typed action
alternatives with action-specific required inputs rather than untyped conditional
intersections. Runtime validation uses the same action rules.

## Prefab capture

`capture_prefab(prefab_path, width=1024, height=1024)` returns a PNG image and
metadata. Paths must name `.prefab` assets under `Assets/` or `Packages/`;
width and height are integer pixels in 64..2048. A graphics-capable Editor in
Edit Mode is required. Remote scoped callers need edit permission because
instantiation can run edit-time callbacks.

The tool owns a preview scene, camera, cloned prefab and rendering resources,
then reports `cleanup_completed`. It does not save the prefab or change the
active scene. Root `RectTransform` UI uses a preview canvas; other prefabs use
front-facing renderer bounds. The owned UI canvas preserves the prefab's scaling
policy against the requested viewport. The PNG has exactly the requested pixel
dimensions and uses an explicit sRGB render target in Gamma and Linear projects.
The capture is static and is not an in-game
screenshot. Runtime initialization, animation and localization systems are not
assumed or invoked. Project edit-time callbacks can still execute on the clone.
A failed capture is not automatically replayed.

## Validation reports

The local harness reads a fresh report from each bridge smoke subprocess and
retains its case names, timings and outcomes in both the smoke and combined JUnit
files. A missing, invalid or contradictory report is an explicit infrastructure
error; an older report cannot stand in for the current run.
