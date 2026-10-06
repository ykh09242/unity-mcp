# Owned product transport measurement

Use the existing pinned Server environment; no dependency installation is needed. From the repository root:

```powershell
Server/.venv/Scripts/python.exe tools/bench_transport.py --output reports/transport/current.json --samples 30 --warmup 3 --large-bytes 4194304 --work-ms 0
Server/.venv/Scripts/python.exe tools/bench_transport_compare.py --baseline-root reports/CS-20261006-mcp-usability/phase8/baseline/source --baseline-manifest reports/CS-20261006-mcp-usability/phase8/baseline/source.json --baseline-revision 789a815bf3a7548b27fa63e364351827e243841d --candidate-root . --candidate-revision current-checkout --output reports/transport/comparison.json --rounds 3 --samples 30 --warmup 3 --large-bytes 4194304 --work-ms 0
Server/.venv/Scripts/python.exe -m pytest tools/tests/test_bench_transport.py tools/tests/test_bench_transport_compare.py -q
```

`--product-root` selects a root containing `Server/src/main.py`. The comparator keeps the harness, interpreter, dependencies, protocol and options fixed, selecting only different product source roots. It compares baseline stdio with candidate stdio, and baseline HTTP with candidate HTTP. Three rounds run serially in baseline/candidate, candidate/baseline, baseline/candidate order, with transport order alternating between rounds. Stop other heavy workloads and freeze product/harness sources before collecting timing evidence. There are no CI wall-time or RSS thresholds.

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

The historical phase8 baseline ordinary reads issued two state RPCs and two readiness pings; its candidate ordinary fallback shared one state RPC and one ping. For any selected version, the declared ordinary-resource strategy determines the exact paired contract: per-call is 2/2, shared is 1/1. Authoritative reads remain two private RPCs and two pings. Raw resource outputs are preserved. Semantic parity validates and removes exactly `data.observed_at_unix_ms` and `data.staleness.age_ms`; all other fields, including sequence, staleness flags and advice, remain significant. The comparator rejects extra normalization, invalid timestamps, schema/runtime/harness mismatches, output drift, unexpected call counts, missing positive ownership or leaks.
