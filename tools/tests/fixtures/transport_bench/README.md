# Owned product transport measurement

Use the existing pinned Server environment; no dependency installation is needed. From the repository root:

```powershell
Server/.venv/Scripts/python.exe tools/bench_transport.py --output reports/transport/current.json --samples 30 --warmup 3 --large-bytes 4194304 --work-ms 0
Server/.venv/Scripts/python.exe tools/bench_transport_compare.py --baseline-root reports/CS-20261006-mcp-usability/phase8/baseline/source --baseline-manifest reports/CS-20261006-mcp-usability/phase8/baseline/source.json --baseline-revision 789a815bf3a7548b27fa63e364351827e243841d --candidate-root . --candidate-revision current-checkout --output reports/transport/comparison.json --rounds 3 --samples 30 --warmup 3 --large-bytes 4194304 --work-ms 0
Server/.venv/Scripts/python.exe -m pytest tools/tests/test_bench_transport.py tools/tests/test_bench_transport_compare.py -q
```

`--product-root` selects a root containing `Server/src/main.py`. The comparator keeps the harness, interpreter, dependencies, protocol and options fixed, selecting only different product source roots. It compares baseline stdio with candidate stdio, and baseline HTTP with candidate HTTP. Three rounds run serially in baseline/candidate, candidate/baseline, baseline/candidate order, with transport order alternating between rounds. Stop other heavy workloads and freeze product/harness sources before collecting timing evidence. There are no CI wall-time or RSS thresholds.

## Fixed transport order and same-source controls

The comparator's `--order alternating` default preserves that historical schedule. `--order http-first` or `--order stdio-first` fixes the transport order in every capture; both transports and all four workloads still run. Revision order independently alternates AB/BA, so `--rounds 2 --order http-first` runs A/B/B/A with HTTP first in all four captures. Each capture requires an actual `stdio-first` or `http-first` order, the result sequence must match it, and a baseline/candidate pair with different orders fails before output/latency comparison. The paired report records both the transport-order policy and the full revision/capture schedule.

`--same-source-control` explicitly marks a control comparison. Both caller revision assertions must be identical before a child can start. Every capture's entire product/common-harness source map must be nonempty in both scopes, equal before/after, and identical to every other control capture. Matching labels or roots alone cannot pass. The report records `comparison_kind=same_source_control`, a verified control flag and the actual common hash map. This proves equality of the captured source scope; it does not independently verify the caller pin against Git or hash excluded non-Python/archive files. Retain the separate Git/archive proof for that baseline pin. Normal product comparisons continue to permit different product source bytes while requiring the same harness.

For a bracketed control matrix, run a baseline A/A control (`--rounds 1 --same-source-control`), product A/B/B/A (`--rounds 2`), then another baseline A/A control (`--rounds 1 --same-source-control`), all strictly serial with `--order http-first`. Controls select the same verified baseline pin/source for both roots. Use distinct output paths, the same interpreter/dependencies, 30 samples/three warmups/4MiB/zero requested delay, and gate/resource/diagnostic OFF. This is eight captures for C1; repeat the same matrix with concurrency two for C2. Each capture retains cold initialization, excluded warmup calls, public/schema/hash/RPC checks, cancellation/reconnect, partial receiver-frame cleanup and source guards. The separate gated/resource contract remains required to prove effective sharing.

Fixed HTTP-first profiles differ from historical phase10 alternating-order profiles; report the new scope explicitly. Preserve all control and product ratios/observations, including slower conditions and failures. Controls expose same-source procedural variation in their own scheduled window; they do not establish external-host idle, CPU warmup stability, statistical significance or a causal explanation. Do not subtract control ratios from product ratios, pool controls as product samples, or turn their variation into an automatic performance threshold. No HTTP-only/workload filtering or extra warmup policy is added.

The phase8 capture schema is `unity-mcp-transport-bench-v2`; the paired report is `unity-mcp-product-comparison-v1`. Phase7 captures compared current stdio with HTTP through a FastMCP subset. Phase8 imports the full selected `main` module and uses its actual `UnityMCP` class, stdio adapter and response middleware. Its newly measured historical baseline is the valid before/after reference; phase7 latency numbers are not interchangeable with it.

The [same-protocol results](../../../../docs/development/transport-performance.md#same-protocol-beforeafter-measurements) include all four workloads for sequential and two-concurrent-call profiles, including higher observed medians. To reproduce without the local evidence snapshot, extract `git archive` of the desired commit's `Server/src`, `Server/pyproject.toml`, `Server/uv.lock` and `mcp_source.py` into the selected baseline root. Both revision labels are required explicit caller assertions; no historical revision is supplied by default or inferred. The optional `--baseline-manifest` checks its root/revision against those arguments and records its file hash and claims. It does not verify source bytes against Git or archive content. Omit it when no separately verified manifest exists; measured source fingerprints and caller-asserted provenance still remain distinct.

## Actual paths and isolation

The installed MCP SDK initializes a separate actual `UnityMCP` server process and calls public `read_console` and `get_test_job`, plus a representative raw state command, through stdio or authenticated local Streamable HTTP. The subset includes product routing, legacy `UnityConnection`, HTTP `PluginHub`, normalization, validation, response limits, negotiated assembly, response retention, SDK serialization and client reply decoding. Full catalog registration, status/discovery selection, global connection-pool selection, actual Editor execution, main-thread scheduling, job creation/test execution and pushed-state caching are excluded.

Both synthetic peers use owned endpoints on `127.0.0.1` port `0`. The TCP peer performs reciprocal v2 HMAC authentication with new listener/connection generations. The registered WebSocket peer uses the actual local token middleware and negotiates only plain `large_result_v1`; `large_result_gzip_v1` is excluded. Tokens exist only in memory/child environment. The fixture supplies owned in-memory status preflight and an explicit credential provider. It never queries real status/credential files or ports 6400/6401, and no Unity Editor is involved.

Children run the runtime's native base interpreter with isolated startup and the pinned environment's explicitly selected site-packages; this avoids Windows venv redirector ownership ambiguity. The fixture's actual PID must equal the held HTTP process PID. Environment variables are allowlisted; telemetry is disabled, and log/status/home directories belong to temporary roots. Bounded shielded termination, kill/reap and handle closure cover cancellation and already-exited children. Bounded traces survive beside the raw report even on failure. A real long-lived native-child cleanup regression is included.

Selected product imports are checked before calls and at final metadata, including lazy `main/core/models/services/transport/utils` imports. An import escaping selected `Server/src` fails. Reports retain SHA256 of every selected product Python source and every common harness/fixture Python source before/after. Additions, removals or changed bytes fail the run; cross-round drift also fails. Caller-provided revision labels are assertions, not Git verification. `harness_repository_head` identifies the harness checkout, not an archived product tree. Optional parent archive manifests retain their file hash and provenance claims separately from independently measured product source hashes.

## Four natural latency workloads

| Workload | Actual representative path | Deterministic payload |
| --- | --- | --- |
| small | public `read_console` | one small JSON log entry |
| state | authoritative raw `get_editor_state` command | 100 objects, settings and scene state |
| large | public `read_console` | one ASCII log message, default 4MiB |
| job | public `get_test_job` | terminal succeeded job and test summary |

Payload size must be 256KiB..8MiB so every HTTP run exercises actual chunked partial-transfer cleanup. Each workload has a first cold call, excluded warmup calls and warmed calls in bounded batches. Raw state intentionally does not measure ordinary resource fallback; terminal job polling does not measure job start, focus or long waiting. Every completed output, including cold/warmup/recovery, must be successful and have equal text/structured content. Canonical fingerprints and decoded-result byte counts are checked outside the measured call span. All samples must agree, so a transient mismatch cannot be hidden by the last result.

`client_rpc_counts` separates initialization, tool listing, all tool calls and public tool calls. Peer counters separately record user commands and HTTP readiness pings. For `N=1+warmup+samples`, the pre-partial lifecycle totals are `read_console=2N+3`, `get_editor_state=N`, `get_test_job=N`. The historical phase8 per-call baseline HTTP path performed `3N+3` readiness pings. A selected baseline may already share readiness: both versions are checked against their own declared strategy. Per-call or sequential captures require `3N+3`; natural concurrent shared captures permit the strategy's lower bound through `3N+3`, and gated captures require the exact lower bound. Authoritative raw state remains private in versions declaring that policy. Shared small/large calls can reduce concurrent readiness pings without changing public RPC counts.

`--cohort-gate` is a separate deterministic CI contract. It holds the owned readiness producer until all actual sharing subscribers join, so counts equal each revision's strategy lower bound. This rendezvous affects peer work and latency: compare identical gate settings only, and do not present gated timings as natural latency. It is off by default. Natural captures enforce each revision's budget independently and retain both counts; valid scheduling differences can increase one run's count without failing the comparison. Cross-revision readiness nonincrease is enforced only with the gate. Declared readiness or ordinary-resource sharing downgrades (`inflight_shared` to `per_call`) fail in every mode, even when either probe flag is disabled.

Natural counts and feature-presence metadata do not prove effective sharing. Verify that behavior with a separate paired capture enabling both `--cohort-gate` and `--resource-contract`; this is not automatically run or mixed into natural latency. The paired report records this proof boundary explicitly. Per-revision public/client RPC counts, partial-transfer cleanup, bounded positive reservations and payload/schema parity continue to be enforced in both capture types.

## Stages and lifecycle

| Field | Observation and limit |
| --- | --- |
| `client_total_ms` | client call initiation through installed SDK reply decoding; excludes harness text parsing/canonical parity hashing |
| `queue_ms` | product legacy admission lock plus owned peer execution admission; other scheduling stays in residual |
| `synthetic_unity_work_ms` | actual elapsed synthetic peer work, including readiness; never actual Unity time |
| `peer_serialization_ms` | synthetic payload construction/JSON encoding, including readiness |
| `wire_response_framework_ms` | residual including wire/writes/framework scheduling/product validation/MCP serialization and SDK handling; not pure network time |

Correlations are out of band; product wire envelopes are unchanged. Fixture tracing overhead remains included. Stage clocks are local to their owning processes and never subtracted across processes. Independent percentiles must not be added. Missing observations and materially negative residuals fail. Thirty samples give descriptive nearest-rank p50/p95/p99, without statistical significance claims. With `--work-ms 2`, Windows timer granularity may extend the synthetic delay; actual elapsed work is retained. Zero delay still includes synthetic payload processing.

Cancellation waits for actual peer command admission, then cancels the MCP call while synthetic work deliberately continues for 500ms. A late-result drain and public recovery call follow. Reconnect replaces the owned TCP/WS peer within the same MCP session, verifies a new registration and a successful call. These are cleanup/recovery tests, not Editor execution cancellation or process restart/session resumption.

HTTP additionally pauses a large transfer after actual first-chunk admission. The recorded held assembler/hub reservations must be positive and within capacity. It cancels and drains the request, releases the remaining chunks, then waits for the exact request UUID's final binary frame to pass the actual product receiver handler before asserting zero reservations again. Sender-side send completion alone is insufficient.

Accounting is sampled reservation ownership, not continuous peak allocation, RSS, or all transient memory. Overlapping assembler and hub reservations are recorded separately, not added as independent allocations. A positive held partial/resource seam proves the relevant reservation path was exercised. Stdio observes its actual response-delivery ContextVar and shared-read budget; a zero `_retained_results` count is not a stdio memory proof, and legacy/SDK transient allocation peaks remain outside this harness's scope.

## Separate real resource contract

`--resource-contract` adds four actual MCP `resources/read` calls after the four latency workloads and lifecycle probes, outside their observations. Two ordinary and two authoritative reads exercise the product editor-state resource with an owned empty project directory (`Assets` only), no pushed-state capability, and actual routing/authentication. This limits the scanner to an owned empty tree. The fixture holds two actual ASGI response bodies before their sends finish, preserving positive source/delivery ownership until release, then requires bounded reservations and eventual zero.

The historical phase8 baseline ordinary reads issued two state RPCs and two readiness pings; its candidate ordinary fallback shared one state RPC and one ping. For any selected version, the declared ordinary-resource strategy determines the exact paired contract: per-call is 2/2, shared is 1/1. Authoritative reads remain two private RPCs and two pings. Raw resource outputs are preserved. Semantic parity validates and removes exactly `data.observed_at_unix_ms`, `data.staleness.age_ms` and `data.diagnostics.heartbeat_age_ms` when diagnostics are present. Heartbeat age must equal the non-negative integer staleness age before normalization; all other fields, including sequence, staleness flags, diagnostic status, activity age and advice, remain significant. The comparator rejects extra normalization, invalid timestamps, schema/runtime/harness mismatches, output drift, unexpected call counts, missing positive ownership or leaks.

## Optional diagnostic observation

`--diagnostic` is OFF by default. OFF installs no diagnostic wrappers, records no diagnostic events, adds no control tool/RPC, and preserves public schemas/output and natural call budgets. ON labels the capture `data_kind=diagnostic` and writes a separate `<output-stem>-diagnostic.json` sidecar. The natural comparator rejects diagnostic kind/flag markers before validating normal counts or comparing latency. Diagnostic wrapper/recorder overhead remains included; these samples must never be pooled with natural or historical phase9 results.

After an explicit coordinated measurement slot, an example selected-product diagnostic command is:

```powershell
Server/.venv/Scripts/python.exe tools/bench_transport.py --product-root . --product-revision 'caller-asserted-product-pin' --output reports/CS-20261006-mcp-usability/phase10/diagnostic/candidate-C1.json --samples 10 --warmup 3 --large-bytes 4194304 --work-ms 0 --concurrency 1 --diagnostic
```

Use `--concurrency 2` for a separate concurrent diagnostic profile; natural gate/resource flags remain off. Revision labels remain caller assertions, with actual selected-product/common-harness fingerprints in both the capture and sidecar. Supported diagnostics require the source-reviewed MCP 2.3.0 and FastMCP 4.0.11. Parameter-name/coroutine mismatch, wrapper stacking, unsupported versions or missing seam source fail clearly; installed seam file hashes and expected parameter names are retained. Every added class/instance patch is scoped and restored on success, failure or cancellation.

| Diagnostic span | Actual observed boundary and limit |
| --- | --- |
| `fixture_tool` | actual public/representative fixture delegate entry to return; includes its waits, not end-to-end MCP/SDK time |
| `hub_command_result`, `hub_large_result` | actual product PluginHub result/chunk handler entry to return; local elapsed time with owned command correlation |
| `mcp_envelope_validation` | synchronous `response_size` alias used by the actual product response middleware; elapsed and current-thread CPU, not all validation sites |
| `stdio_handoff` | actual `DeliverySendStream.send` entry to return; typed JSON-RPC identity rather than a fabricated workload link |
| `stdio_write_flush` | actual synchronous `_BinaryWriteOperation.run` around bytes write/flush; elapsed and writer-thread CPU, batch scoped/unlinked to a specific request |
| `client_sdk_request` | actual installed `ClientSession.send_request`, including its prepare/typed result validation and dispatcher await |
| `client_dispatch_wait` | actual installed session dispatcher `send_raw_request`; includes transport/decode/dispatcher handling, not pure wire time |

These local spans overlap and must not be added. Nested client spans narrow the remaining SDK preparation/typed-validation envelope but include observer overhead; they do not isolate raw JSON decode CPU. Client and child clocks are never subtracted across processes. Batch labels identify a workload envelope, not per-request CPU attribution or unobserved framework queue time.

ON adds exactly eight diagnostic controls per mode: begin/end for each of four workload batches. Their count is separate in the sidecar; `client_rpc_counts` still exposes the actual total, and `normal_tools_call_count` discounts only those eight controls. Public/Unity command counts and payload/schema parity remain unchanged. Child end waits for every warmed peer completion and actual product delivery accounting drain before sampling CPU. Client process CPU includes all client threads and harness output parsing/parity/hash work, excluding batch-control waits. Child process CPU includes all child threads, synthetic peer work and control overhead through the drain boundary. Await spans never carry a request-CPU field. CPU clocks can be quantized on Windows: small snapshots may read zero and do not prove zero work. Functional smoke CPU values are compatibility evidence, not performance conclusions.

Storage is bounded to 8192 numeric/short-label events per recorder, 1024 owned aliases, 16 seam descriptions and 4MiB per encoded sidecar; diagnostic profiles permit at most 30 samples and three warmups. Recorder overflow preserves delegated exceptions/cancellation, records incompleteness, and fails only after owned cleanup and bounded evidence saving. Encoded byte overflow saves an explicit failure stub instead of an oversized sidecar. `snapshot_scope` distinguishes pre-metadata child snapshots from graceful/error-finally snapshots after patch restoration. A hard OS kill can prevent Python `finally` or an early child snapshot; client trace, available child snapshot and existing stderr/peer evidence are preserved where available. These observations do not extend the existing sampled reservation evidence into an RSS/transient-allocation proof.
