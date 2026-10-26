# Native job coordination

Native command admission checks the Editor's current compilation, import and Play
transition state, plus jobs owned by this package. A job's public success, failure,
cancellation or timeout does not prove that its native resources have been released.
Status and cancellation remain available through the reviewed control commands.

- Builds reserve ownership before scheduling their native callback. Ownership spans
  preparation, BuildPlayer, result finalization and gaps between batch children.
- Test jobs retain ownership through their runner's actual cleanup. A cancellation
  acknowledgement does not release the barrier. Supported native test control exposes
  `native_control.released` and a confirmed termination witness.
- Play scenarios and suites retain their existing execution/finalization ownership.
  Pending owned Play entry and actual transitions block conflicting work. Stable Play
  Mode alone does not impose a barrier.
- Retained UPM Add, Remove and Embed requests block conflicting Editor operations.
  Retained List and Search requests only fence subsequent UPM Client method starts
  and arbitrary code that could call Client. Request completion, including failure,
  is the native release witness; desired package state is not. Lost request handles
  after reload report `recovery_required` instead of inventing completion.
- UPM Resolve and registry changes that call void Resolve have `trigger_only`
  coordination. Their status explicitly reports `native_lifetime_supported: false`.
  An Unknown Resolve record does not impose a permanent global barrier. Unity exposes
  no per-invocation completion handle for this API; an up-to-date Resolve can emit no
  package registration event. Actual compilation/import flags still apply.
- An owned `refresh_unity` compilation request reserves before the call that queues
  compilation, including its synchronous asset refresh. If that owned refresh itself
  produces an observed pipeline start, its cycle satisfies the compilation goal and
  the tool skips a redundant void compilation request. The response distinguishes
  `compilation_trigger: refresh`, `compilation_goal_satisfied: true` and
  `compile_requested: false`; clients still inspect the returned compile job for
  completion and diagnostics. This observes a cycle, not a source revision verdict.
  A zero-event refresh falls through to the explicit compilation request.
  Matching pipeline start/finish events, including a finish with no compiled assemblies,
  retain ownership until the refresh decision has settled and later Editor updates
  observe raw compilation/import/reload quiescence. A known finish witness survives
  reload; an unfinished context does not. A missing event at the existing caller
  observation deadline becomes `recovery_required`, never a fabricated release. A
  lost event context across reload also requires recovery. Synchronous refresh without
  an explicit compilation request does not leave a speculative pending reservation.

`native_job_active`, `editor_compiling` and `editor_importing` are temporary blockers.
`recovery_required` is not automatically retryable: inspect the job and Editor state,
allow real native completion to be observed where possible, or restart the Editor
after its work has safely stopped. Do not replay an operation whose outcome is unknown.
Compilation coordination remains conservative until native no-op lifecycle behavior
is validated for the supported Editor. Event absence cannot establish quiescence.

These checks cover package-owned jobs and observed native hazards. They cannot track
every detached operation started by arbitrary custom code or unrelated Editor tools.
Network-only generation work does not impose an Editor-wide job barrier.

Unity documents [UPM Client method sequencing](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/PackageManager.Client.html),
the [void Resolve contract](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/PackageManager.Client.Resolve.html),
and [Request.IsCompleted](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/PackageManager.Requests.Request.IsCompleted.html).
