# Local phase diagnostics

Set `UNITY_MCP_PHASE_DIAGNOSTICS=1` for the server process and enable the Editor's existing command log recording. Both are opt-in. Server log admissions sample one in 64 invocations. The generated random token is carried in the negotiated `phase_timing_v1` internal WebSocket envelope. Verification reports can also carry run-owned observations. Legacy/stdio connections retain their existing local timing. Cross-process joins currently require HTTP/WebSocket.

Each invocation owns its context and closes it on return, failure or cancellation. Readiness probes cannot claim the requested command's token. A shared read that outlives its originating observer cannot reopen that observer. Diagnostic records contain fixed phase names, scalar durations, outcomes and a random correlation, never arguments, code, exceptions, identities, paths or credentials. No external telemetry or metric label is added.

Python records admission, preflight, send, response wait and return processing. Its bounded 256-record background logging queue drops optional records under pressure; diagnostic writes use a separate process-specific file without sharing ordinary logging handler locks or shutdown hooks. A blocked diagnostic write cannot make commands wait. Pending records may be lost at shutdown. Unity retains the existing buffered logs and actual execution/delivery owners, adds the server correlation, and measures `manage_asset` imports/reimports as a subset of handler time. Other imports remain within handler time; an absent import duration means unmeasured, not zero.

Server diagnostics are stored in `phase-timing-<pid>-<session>.log` in the normal server log directory (`UNITY_MCP_LOG_DIR` can override it), with two 512 KiB rotated backups per session. No file is created while disabled. Aggregate explicitly selected diagnostic and Editor log files:

```sh
python tools/phase_timing_report.py phase-timing-1234-0123456789abcdef0123456789abcdef.log editor.jsonl
```

The report joins matching tokens and emits counts and p50/p95/max per measured phase without exporting tokens or input paths. It reports unmatched, duplicate/ambiguous, ignored and overflow records. `--max-traces` changes the default 10,000 retained diagnostic pairs; this limit never applies to Unity jobs or payloads. Input lines are read incrementally and oversized lines are skipped.

Clocks are independent: compare durations, not absolute cross-process timestamps. Native import is nested in handler and server response wait overlaps native work, so do not sum all columns into a total. `returned` describes Python response availability, not tool success. Delivery and actual late execution settlement remain separate. Sampling and optional record loss make this an observation aid, not an authoritative job history or a profiler for every import.

## Tracing a verification run

With phase diagnostics enabled, a terminal `manage_verification` report includes a
`timing` object. Its `run_id` groups the verification. Each observed HTTP/WebSocket
command has its own random correlation for joining the Editor command log. Direct
runs include inline preparation. Runs started with a preparation ID exclude that
earlier preparation. The run retains up to 96 command observations and reports
the count omitted. Regular server log sampling remains one in 64. A full
verification report can omit optional timing to fit its existing size limit.

The server measures each newly admitted WebSocket command's scheduler
ticket-to-grant interval. A verification request that inherits a public
operation can also report that outer lease's queue interval. The server measures scheduling
the detached run until its first execution, orchestration until the runner
returns, and cumulative time asleep between child job polls. Each launched
Player is keyed by the actual child job ID. The launcher measures process
creation, launch until the first valid progress snapshot it observes in the poll
loop, writing process evidence, and reading the final report. The first observed
progress includes polling delay. Editor queue, handler and response send use
the Editor's local clock. An asynchronous handler can include waits. CLI
`verification report` also records the MCP report-read round trip from entering
the client call until it returns.

For each trial, use the same project inputs, profile, launch configuration,
machine and load. Record a setup label and the starting condition. For a cold
trial, restore the same cache snapshot and restart the selected processes. For
a warm trial, use the same warm-up procedure with those processes running.
Export each distinct completed run once. For example:

```sh
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json verification report --options '{"run_id":"<returned-id>"}' > warm-1.json
python tools/verification_timing_report.py --trial warm fixtureA warm-1.json --trial warm fixtureA warm-2.json --trial cold fixtureA cold-1.json --editor-log editor.jsonl --operations
```

The report groups by declared setup and cold/warm condition, observed project
and source identity, selection, profile, installation, entry path and verdict.
An Editor trial requires complete source coverage and both the source and
compilation-context digests from the initial freshness stage. This separates
build targets, compiler options, defines and resolved references for identical
source files. Mapped Editor children retain a combined token that also includes
the Editor session, so those trials compare only within that session. A standalone
Player trial uses the registered candidate manifest.
It reports sample counts, median and interpolated p95 in milliseconds. Each
command metric samples individual invocations, so a run that polls more often
contributes more poll samples. The optional `--operations` view shows each
run ID, its command correlations, the matched Editor durations and Player job
IDs. An unmatched or ambiguous native record leaves those durations unknown.
Clocks have no measured offset. One-way network delivery and exact Player
startup are unknown. Response waits can contain the entire Editor handler.

## Diagnostic file retention

Each new server session has a random file identity and an immutable `.owner` marker protected by a nonblocking operating-system file lease. PID reuse never resumes an old file. The lease stays with the writer until its background thread actually exits; a short shutdown wait does not release a still-blocked writer's protection.

The worker inspects at most 128 directory entries once per minute, continuing a partial scan on the next pass. Positively owned, unlocked sessions are retired after seven days without writes (or seven days after graceful close). Active leases, recent rotated files, invalid owners, legacy PID-only logs, unknown names, links and hard-linked files are preserved. A cleanup error leaves the owner for a later attempt. There is no recursive cleanup and no new execution, payload or memory limit.

For permanent exports, copy logs under a different name or outside the configured log directory; matching session files plus their valid ownership marker remain managed diagnostic storage. Existing legacy logs are not silently adopted. Rotation still applies only to the current session's three log files.

`BufferedSink.retention_snapshot()` returns the last worker inventory without file I/O: observed owned/active sessions, retained bytes, oldest retained age, deleted sessions and cleanup errors. `scan_complete=false` means the inventory is partial. Values cover that scan, not an atomic filesystem snapshot; inaccessible entries are unknown and reported as errors. No diagnostic maintenance runs while the feature is disabled or before its first sampled record.

## Capture coverage and loss

`BufferedSink.capture_snapshot()` exposes cumulative scalar counters for sampled offers, successful writes, failed writes, unsettled samples and drops (queue saturation, worker startup contention/failure, offers after close, and queued records abandoned at shutdown). Its short counter lock never covers disk, maintenance or external callbacks. The disabled invocation path still avoids a trace or token allocation. The 1-in-64 sampling policy and command deadlines are unchanged.

The writer saves a `phase_capture_coverage` checkpoint on its first opportunity, at most once every 30 seconds thereafter, and at shutdown. Each sink has a fresh random session identity; counters reset only with a new sink. Sample records carry that identity and their write-attempt sequence, without arguments, paths or credentials. A checkpoint describes counters through that observation window, not future records. Its written plus write-failed count bounds completed write attempts; later drained records cannot be counted in that window, even if they were already offered. A blocked or failed final write leaves an unknown tail; an earlier saved zero-loss count cannot prove a complete process lifetime. A later successful checkpoint includes the count of failed checkpoint writes.

The existing report adds `capture_coverage`. `capture_fraction` is successful sample writes divided by sampled offers. `selected_log_fraction` is the fraction of those writes found in the selected files through the last checkpoint: rotation, omitted files and retention can make it smaller even without queue loss. Both are unknown (`null`) without the necessary evidence. Missing checkpoints, samples after a checkpoint, conflicting/reset counters under the same session, duplicate sequences and report overflow prevent a complete result. Legacy logs still produce phase summaries with unknown capture coverage.

The report validates retained checkpoint history in chronological order and chooses the latest checkpoint independently for each session, accepts rotated files in any order, and strips random identities from its output. `windows` also includes each writer's last retention scan; these scans can describe the same directory and must not be summed. The existing `--max-traces` analysis budget also bounds distinct retained checkpoints across all sessions; an overflow remains explicitly incomplete. It never limits Unity work. Shutdown releases abandoned queue rows instead of retaining their payloads in a stopped sink.
