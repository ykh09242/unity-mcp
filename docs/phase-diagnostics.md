# Local phase diagnostics

Set `UNITY_MCP_PHASE_DIAGNOSTICS=1` for the server process and enable the Editor's existing command log recording. Both are opt-in. Server admissions sample one in 64 invocations; the generated random token is carried only in the negotiated `phase_timing_v1` internal WebSocket envelope. Public tool parameters and results are unchanged. Legacy/stdio connections retain their existing local timing; cross-process joins currently require HTTP/WebSocket.

Each invocation owns its context and closes it on return, failure or cancellation. Readiness probes cannot claim the requested command's token. A shared read that outlives its originating observer cannot reopen that observer. Diagnostic records contain fixed phase names, scalar durations, outcomes and a random correlation, never arguments, code, exceptions, identities, paths or credentials. No external telemetry or metric label is added.

Python records admission, preflight, send, response wait and return processing. Its bounded 256-record background logging queue drops optional records under pressure; diagnostic writes use a separate process-specific file without sharing ordinary logging handler locks or shutdown hooks. A blocked diagnostic write cannot make commands wait. Pending records may be lost at shutdown. Unity retains the existing buffered logs and actual execution/delivery owners, adds the server correlation, and measures `manage_asset` imports/reimports as a subset of handler time. Other imports remain within handler time; an absent import duration means unmeasured, not zero.

Server diagnostics are stored in `phase-timing-<pid>-<session>.log` in the normal server log directory (`UNITY_MCP_LOG_DIR` can override it), with two 512 KiB rotated backups per session. No file is created while disabled. Aggregate explicitly selected diagnostic and Editor log files:

```sh
python tools/phase_timing_report.py phase-timing-1234-0123456789abcdef0123456789abcdef.log editor.jsonl
```

The report joins matching tokens and emits counts and p50/p95/max per measured phase without exporting tokens or input paths. It reports unmatched, duplicate/ambiguous, ignored and overflow records. `--max-traces` changes the default 10,000 retained diagnostic pairs; this limit never applies to Unity jobs or payloads. Input lines are read incrementally and oversized lines are skipped.

Clocks are independent: compare durations, not absolute cross-process timestamps. Native import is nested in handler and server response wait overlaps native work, so do not sum all columns into a total. `returned` describes Python response availability, not tool success. Delivery and actual late execution settlement remain separate. Sampling and optional record loss make this an observation aid, not an authoritative job history or a profiler for every import.

## Diagnostic file retention

Each new server session has a random file identity and an immutable `.owner` marker protected by a nonblocking operating-system file lease. PID reuse never resumes an old file. The lease stays with the writer until its background thread actually exits; a short shutdown wait does not release a still-blocked writer's protection.

The worker inspects at most 128 directory entries once per minute, continuing a partial scan on the next pass. Positively owned, unlocked sessions are retired after seven days without writes (or seven days after graceful close). Active leases, recent rotated files, invalid owners, legacy PID-only logs, unknown names, links and hard-linked files are preserved. A cleanup error leaves the owner for a later attempt. There is no recursive cleanup and no new execution, payload or memory limit.

For permanent exports, copy logs under a different name or outside the configured log directory; matching session files plus their valid ownership marker remain managed diagnostic storage. Existing legacy logs are not silently adopted. Rotation still applies only to the current session's three log files.

`BufferedSink.retention_snapshot()` returns the last worker inventory without file I/O: observed owned/active sessions, retained bytes, oldest retained age, deleted sessions and cleanup errors. `scan_complete=false` means the inventory is partial. Values cover that scan, not an atomic filesystem snapshot; inaccessible entries are unknown and reported as errors. No diagnostic maintenance runs while the feature is disabled or before its first sampled record.
