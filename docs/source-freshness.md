# Source freshness

Editor readiness and current loaded code are separate observations. An external
Git pull or editor write can change source while an unfocused Unity Editor still
has the previous assemblies loaded. Verification observes source and compilation
inputs before admitting tests; it does not depend on foreground focus.

`get_compile_job` with `observe_source=true` returns an additive
`source_freshness` envelope (`unity-mcp/source_freshness@1`). The strict native
`compile_job@1` DTO is unchanged. Editor state also includes a cached freshness
observation and its age. Reading either surface does not request compilation.

| Status | Meaning |
| --- | --- |
| `observed_current` | Stable sampled input hashes, associated successful compilation, and matching loaded/output assembly MVIDs were observed. |
| `changed` | Observed source or compilation inputs differ from the attributed compilation. |
| `pending` | Observation, compilation, or reload has not settled. |
| `compile_failed` | Associated native compilation failed. |
| `incomplete` | Required scan, context, compilation, or loaded assembly evidence is missing. |
| `unavailable` | The peer, target, permission, or observation is unavailable. |

Evidence names the project/session, source token, content/context digests, compile
ID/generation, observation time, coverage gaps, and loaded/output identities.
Hashes include content and membership: equal timestamps do not establish equality.
An incremental no-op cannot establish a cold current-source baseline. The
`source_revision_confirmed` flag remains false: sampling is not an atomic proof of
every byte consumed by Unity's compiler.

Verification profiles use `refresh_policy=if_needed` by default. They may issue
one import and one explicit script compilation within the original deadline.
`force_recompile=true` is supported only with `scope=scripts`, `compile=request`;
it deliberately requests Unity's clean compilation when ordinary incremental
evidence is insufficient. `refresh_policy=never` observes and refuses stale or
incomplete evidence. No focus change or perpetual background watcher is required.

New public native `run_tests` starts require `source_token`. The existing public
MCP test tool obtains it through its preflight; the native runner checks it again
immediately before execution, including queued admission and reload recovery.
Older peers cannot satisfy a freshness-required request. A source change during
verification marks the report stale even if the tests passed.

Timeout, transport loss, or cancellation intent does not establish native
completion. Observe the existing compile/test ID and cleanup state; do not replay
refresh or tests to replace an uncertain response.

## Recovery observations

Doctor receipts and Editor state expose `source_recovery` with reason, certainty,
evidence origin/time/age, source/compile identity where available, limitations and
the next inspection action. `compilation_failed`, `reload_in_progress`, and
`transport_disconnected` describe distinct evidence. A stale heartbeat alone
cannot establish a modal dialog, Safe Mode, an exited process or a frozen Editor.

The current adapter does not inspect Editor.log or local process tables: routing
does not provide a verified PID/start-time binding. It reports that gap explicitly.
The classifier supports independently verified local process observations, but a
remote server's files/processes are never treated as Editor-local evidence.
Doctor's SDK connection verdict remains independent of source recovery advice.
No diagnostic action restarts, focuses, clears Console, or installs software.

See [verification profiles](verification-profiles.md) for complete stage reports
and [readiness](editor-readiness-and-previews.md) for native ownership barriers.
