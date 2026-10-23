# Operation timeouts and slow editors

Completion, not elapsed sleep, ends a healthy wait. Timeouts bound observation and
resource ownership; they cannot guarantee that an overloaded or paused machine
will make progress. Use a larger explicit budget for an unusually large project.
Test-only scaling is documented in [testing-timeouts.md](testing-timeouts.md).

## Refresh and compilation

`refresh_unity(timeout_seconds=1800)` permits up to 30 minutes for dispatch and
readiness observation. The public range is 1 to 86,400 seconds (one day), default
300. Both transports receive the remaining budget. Selection, dispatch, native
compilation start, readiness and compilation-status observation spend the same
elapsed budget rather than each starting a new full wait. HTTP retains five
seconds of response overhead outside the observation budget.

For the CLI, use `unity-mcp --timeout 1800 editor refresh --compile`. `--no-wait`
still requests dispatch without a readiness wait. Unity 6 returns at the compile
start edge before domain reload; the server observes readiness afterward.
Compilation deferred by Play Mode reports that start was not observed.

An observation timeout does not undo a refresh, import or snapshot already running
inside Unity. An ambiguous dispatch reports `inspect_state_before_retry`; inspect
editor state or the returned compilation ID before deciding to issue another write.
Only explicit pre-dispatch reload refusals are eligible for automatic replay.

## Play Mode, Console and client startup

`manage_editor(action="play", wait_until="first_frame", timeout_seconds=1800)`
uses a 30-minute readiness observation budget. The default is 300 seconds; native,
MCP and CLI callers accept integers from 1 to 86,400. CLI callers can use
`unity-mcp editor play --wait-until first_frame --timeout-seconds 1800`.
The native deadline uses [Editor elapsed time](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/EditorApplication-timeSinceStartup.html)
and persists in the same Editor session across domain reload. The wall-clock
deadline remains display metadata and cannot prematurely expire a new job after
a system clock correction.

Observation expiry leaves Play Mode unchanged and returns the original job ID.
Use `get_play_mode_job` (CLI: `editor play-status JOB_ID`) to observe late readiness;
do not replay `play`. A later successful observation includes
`observed_after_deadline=true` and does not claim that startup completed within the
original budget. `cancel_play_mode_job` retires monitoring, including an expired
observation, without stopping Play Mode. Scene-loaded and first-frame readiness
still do not guarantee application initialization or a rendered image.

`execute_menu_item` supports `timeout_ms` from 1 to 86,400,000 for the allowlisted
`console_window_open` observation only; the default is 300,000 milliseconds.
HTTP and stdio receive the corresponding command budget. Console presence is
checked before declaring expiry when the Editor resumes: a late positive result
reports `observed_after_deadline` with `observed_within_budget=false`.
Unavailable or cancelled observation remains distinct from an observed closed
window. The accepted invocation is never automatically repeated.

New Codex stdio entries use `startup_timeout_sec = 300` for cold package/server
bootstrap. This belongs to the client startup layer, independently of normal tool
request deadlines. Set a larger explicit value in the generated client entry for
a slower environment; subsequent configuration updates preserve existing values.

## Recovery and transport settings

Set environment variables on the process that owns the setting. Restart the server
or editor after changing launch configuration; remote clients do not change these
operator settings. Values are seconds, finite, with invalid values using defaults.

| Setting | Default | Scope |
| --- | ---: | --- |
| `UNITY_MCP_RELOAD_MAX_WAIT_S` | 300 | Stdio pre-dispatch reload refusals; no implicit 40-retry cutoff |
| `UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S` | 300 selected / 20 unselected | HTTP registry change events during reconnect |
| `UNITY_MCP_EDITOR_READY_TIMEOUT` | 300 | Readiness after script mutations |
| `UNITY_MCP_SESSION_READY_WAIT_SECONDS` | 6 | HTTP fast-read readiness probes |
| `UNITY_MCP_HANDSHAKE_TIMEOUT` | 30 | Server and native stdio greeting/authentication; maximum 300 |
| `UNITY_MCP_HEARTBEAT_TIMEOUT` | 30 | Stdio heartbeat receive |
| `UNITY_MCP_CONNECTION_TIMEOUT` | 300 | Ordinary stdio socket operation |
| `UNITY_MCP_COMMAND_TOTAL_TIMEOUT` | 600 | Ordinary stdio command, including retries |

Recovery wait overrides allow up to one day and zero disables that recovery wait.
An enclosing request deadline still wins. Explicit `max_retries` remains honored.
Fast read-only probes keep their two-second attempt budget and retry advice; a
larger recovery setting does not turn a probe into a long-running mutation.
The authentication window is separately bounded and does not extend credential
validity, idle-session retention or permission leases.

Readiness uses monotonic elapsed clocks. Native update and compilation subscriptions
detach on completion, timeout, editor shutdown and assembly reload.

## Large result transfers

Negotiated chunked responses, including gzip responses, spend the admitted HTTP
command's remaining elapsed budget. A request with time left beyond five minutes
is no longer cut off by a separate 300-second assembler limit. Selection, native
execution and earlier request phases still consume the enclosing budget; result
arrival does not restart it. Peer-supplied timeouts and chunk progress cannot
extend the admitted deadline.

Existing result byte limits, socket-generation ownership and cancellation fences
remain in effect. Expiry, cancellation or disconnect releases partial buffers;
a timeout does not authorize replaying the original command. Standalone internal
assembler callers retain a 300-second default and may configure up to one day.

## Skill installer readiness

Each skill preview or apply shares a cumulative Editor selection-read allowance
of 300 seconds. Set `UNITY_MCP_SKILL_SYNC_EDITOR_WAIT_TIMEOUT` on the Editor process
for unusually slow imports; finite positive values up to one day are supported.
The `PreviewSourceAsync` and `ApplyAsync` cancellation overloads also accept an
explicit `editorWaitTimeoutSeconds`. Original callback signatures remain available.

Only Editor observation spends this allowance. Download and staging time retain
their existing policies, so a large multi-file synchronization does not acquire
a new global five-minute deadline. Every package-bound write still revalidates the
installed selection, and repeated reads consume the same remaining allowance.

Cancellation retires a queued Editor read and releases its callback captures.
Closing the installer cancels its operation; an in-progress download or file action
settles through existing ownership and rollback before completion. A cancelled
apply cannot pass a later write boundary, and recovery cleanup is not cancelled.

## Test discovery and native startup

`get_tests(timeout_seconds=1800)` allows a 30-minute total discovery observation
budget, including semaphore admission and both EditMode and PlayMode. Its default
is 300 seconds and accepted values are integers from 1 to 86,400. The server passes
the budget to the built-in native service and preserves response overhead. A root
accepted before expiry remains accepted if the main-thread continuation is delayed;
an expired request cannot begin another mode. Disposal still cancels the request.
Existing custom `ITestRunnerService` implementations keep their existing contract.

`UNITY_MCP_TEST_DISCOVERY_TIMEOUT` configures direct native discovery and the
first-page test resources (default 300, maximum 86,400). Set it on the process that
owns that entry point. The `get_tests` tool exposes its own explicit budget.
`UNITY_MCP_STDIO_START_TIMEOUT` configures native bridge startup (default 300,
maximum 86,400). Invalid, zero or negative native settings use the default.
Stopping or replacing a pending start cancels that wait; a late success or failure
cannot overwrite the current start's state.

## Captures

`manage_profiler(action="memory_take_snapshot", timeout_seconds=1800)` permits a
30-minute capture observation budget. The default is 300 seconds, with integer
values from 1 to 86,400. Path validation and invocation consume that budget too.
The same parameter on `window_start` retains its 1–120 second sampling-window
range: those are bounded sample jobs, whose result is read separately.

A timed-out memory snapshot can still be writing inside Unity. The response includes
`snapshot_path` and `reason="outcome_unknown"`; inspect the file before retrying.
Only the native completion callback owns output-folder cleanup. The observation
timer is canceled when completion wins, so successful captures do not retain timers
for the remainder of their budget.

Composited screenshots allow 30 seconds for a rendered frame before using their
existing camera-render fallback. Known batch mode still takes that fallback
immediately. Native C# callers can supply a finite positive `timeoutSeconds` up to
one day to `ScreenshotCapturer.Begin`; invalid values fail before creating a hidden
GameObject or registering an update callback. This frame fallback is distinct from
a command deadline. The outer transport/client deadline must also accommodate a
caller-selected unusually long capture.

## Local validation harness

The harness uses a monotonic clock for boot readiness, license warmup, UTF polling,
smoke processes and the overall watchdog. A system clock correction cannot create
an immediate timeout or an unexpectedly extended run. Clean-EditMode recovery uses
the remaining `--overall-timeout` budget instead of a separate 30-second cutoff,
and cannot launch another test after that budget expires. Owned-process cleanup
still has its own finite grace period. Successful observations return immediately.

## Audit classification

The audit covered server/CLI transports and tools, native Editor/runtime helpers,
test watchdogs, the local harness and CI configuration. Numeric time values have
different purposes; they are not a single global workload limit.

| Family | Policy and reason |
| --- | --- |
| Refresh, discovery, snapshot and reload observation | Explicit/configurable finite budgets; elapsed time is shared across phases where the request owns them. |
| Fast status probes and reconnect polling | Short attempts return retry advice or continue inside an enclosing budget; a probe is not a large task execution deadline. |
| Async test/build/play jobs | Start/initialization, job execution and status-poll windows remain distinct; poll expiry does not cancel the resumable job. Existing per-job overrides remain available. |
| Screenshot frame availability | Completion or camera fallback, with batch mode detected before waiting; invalid time values cannot leak callbacks. |
| Authentication, credential leases and idle eviction | Security/freshness lifetimes stay bounded; increasing a job budget does not extend authorization. |
| Cache TTL, debounce, progress and heartbeat intervals | Scheduling/freshness controls, not proof that work has failed. Stdio receive/handshake waits are separately configurable. |
| Version, executable, keychain and network probes | Small bounded diagnostics; no project import/build executes inside them. External client/request deadlines can still be configured separately by their owners. |
| Package/download and explicit network operations | Existing operation budgets and cancellation remain; a timeout does not authorize replaying an ambiguous write. |
| Shutdown/drain/worker cleanup | Finite ownership and cancellation grace remain to prevent orphaned workers or an unresponsive exit. |
| Test completion watchdogs | The Python watchdog multiplier controls infrastructure slack; deliberate timeout fixtures and performance thresholds retain their asserted values. |
| CI job limits | Finite runner-level budgets remain; these bound the CI job rather than a user's Unity scene or build. |

No finite timeout guarantees progress on a suspended machine. Configure the specific
operation and its external client's request deadline for exceptionally large work,
and use resumable job/status interfaces when available rather than an unlimited
foreground request.

## Asset generation provider requests

Managed generation requests use the remaining job deadline at the moment each HTTP
request is sent, including requests after recovery. Generation POST/PUT requests,
large input uploads and delayed submission receipts can use the remaining execution
budget (currently up to two hours per job). GET/HEAD polling and metadata reads keep
the 120-second default and are also capped by the remaining job time. Adapters can
set `HttpRequestSpec.TimeoutSeconds` explicitly; it must be finite, positive and at
most 86400 seconds. An explicit value cannot extend the enclosing job deadline.

OpenRouter's synchronous image request carries the generation allowance even for
direct adapter callers. The native HTTP transport rounds fractional seconds up to
Unity's whole-second timeout; the job retains its precise deadline and cancellation.
No retry is introduced. A lost submission response remains `outcome_unknown`, and
recovery never automatically resends a potentially billable request. Cancellation
or a timeout does not confirm that the remote provider stopped processing.

Artifact downloads retain their separate streaming and outbound validation policy.
Applying an API request budget creates a shallow request copy, preserving caller
state without copying large input buffers or retaining completed job payloads.
