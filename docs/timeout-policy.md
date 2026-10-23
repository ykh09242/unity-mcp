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
