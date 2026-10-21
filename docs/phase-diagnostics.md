# Local phase diagnostics

Set `UNITY_MCP_PHASE_DIAGNOSTICS=1` for the server process and enable the Editor's existing command log recording. Both are opt-in. Server admissions sample one in 64 invocations; the generated random token is carried only in the negotiated `phase_timing_v1` internal WebSocket envelope. Public tool parameters and results are unchanged. Legacy/stdio connections retain their existing local timing; cross-process joins currently require HTTP/WebSocket.

Each invocation owns its context and closes it on return, failure or cancellation. Readiness probes cannot claim the requested command's token. A shared read that outlives its originating observer cannot reopen that observer. Diagnostic records contain fixed phase names, scalar durations, outcomes and a random correlation, never arguments, code, exceptions, identities, paths or credentials. No external telemetry or metric label is added.

Python records admission, preflight, send, response wait and return processing. Its bounded 256-record background logging queue drops optional records under pressure; a blocked disk cannot make commands wait. Pending records may be lost at shutdown. Unity retains the existing buffered logs and actual execution/delivery owners, adds the server correlation, and measures `manage_asset` imports/reimports as a subset of handler time. Other imports remain within handler time; an absent import duration means unmeasured, not zero.

Aggregate explicitly selected local server and Editor log files:

```sh
python tools/phase_timing_report.py server.log editor.jsonl
```

The report joins matching tokens and emits counts and p50/p95/max per measured phase without exporting tokens or input paths. It reports unmatched, duplicate/ambiguous, ignored and overflow records. `--max-traces` changes the default 10,000 retained diagnostic pairs; this limit never applies to Unity jobs or payloads. Input lines are read incrementally and oversized lines are skipped.

Clocks are independent: compare durations, not absolute cross-process timestamps. Native import is nested in handler and server response wait overlaps native work, so do not sum all columns into a total. `returned` describes Python response availability, not tool success. Delivery and actual late execution settlement remain separate. Sampling and optional record loss make this an observation aid, not an authoritative job history or a profiler for every import.
