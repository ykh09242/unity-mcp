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
