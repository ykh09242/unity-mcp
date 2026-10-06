# Owned transport performance measurement

From the repository root, use the existing Server environment without installing or updating dependencies:

```powershell
Server/.venv/Scripts/python.exe tools/bench_transport.py --output reports/transport/sequential.json --samples 30 --warmup 3 --large-bytes 4194304 --work-ms 2
Server/.venv/Scripts/python.exe tools/bench_transport.py --output reports/transport/concurrent2.json --samples 30 --warmup 3 --large-bytes 4194304 --work-ms 2 --concurrency 2 --order http-first
Server/.venv/Scripts/python.exe -m pytest tools/tests/test_bench_transport.py -q
```

Run multiple times and alternate `--order` when assessing small differences. Thirty samples describe this run; their p95/p99 do not establish statistical significance. `--work-ms 0` removes the synthetic delay while preserving payload construction and all actual transport processing. Windows timer granularity can make an asynchronous 2ms synthetic delay take much longer; the report records actual elapsed synthetic work rather than the requested delay.

## Measured pipeline

The installed MCP SDK initializes a real, separate FastMCP server process and calls its tools through stdio pipes or authenticated local Streamable HTTP. The fixture invokes this repository's actual public `read_console` and `get_test_job` functions, `send_with_unity_instance` routing, legacy `UnityConnection`, HTTP `PluginHub`, response normalization, validation, negotiated large-result assembly, and MCP client response parsing. The TCP fixture implements reciprocal v2 HMAC authentication with per-listener and per-connection generations. The WebSocket fixture authenticates with the actual local token middleware and registers its owned project before receiving commands. Fresh synthetic launch tokens remain in process memory/environment and are never written to token/discovery files.

This is a product transport comparison over a limited FastMCP tool subset. It excludes `main.py` startup, status-file discovery, global connection-pool selection, telemetry/log wrappers, the complete tool catalog, Editor execution, and Unity main-thread scheduling. It is not an SDK-only echo comparison or a claim about real Editor wall time. The prototypes under `tools/experiments/transport` provide a separate protocol-overhead control.

The HTTP peer negotiates plain `large_result_v1` chunks only, excluding `large_result_gzip_v1`. These captures do not measure the new gzip path; `peer_profile.http_gzip_negotiated=false` and the explicit report limits record that boundary.

All listeners bind to `127.0.0.1` with port `0`, and the HTTP listener keeps the bound socket through server startup. The script contacts only these owned endpoints, never ports 6400/6401 or a real Editor. It does not inspect user credential files or inherit credential environment variables. The product connection receives an explicit in-memory credential provider. Legacy status-file preflight is replaced with owned in-memory non-reloading state before any product call; user status/discovery files are not queried. Temporary roots and subprocesses are isolated per run; bounded diagnostic traces are copied beside the JSON report before teardown, including on failure. HTTP child termination/reaping/handle closure runs under bounded cancellation shielding, including already-exited children.

## Workloads and comparison

The common schema is `unity-mcp-transport-bench-v1`; `workload.py` provides deterministic payloads also used by transport prototypes.

| Workload | Actual route | Payload |
| --- | --- | --- |
| small | public `read_console`, one JSON log entry | Small success/data result |
| state | `get_editor_state` command via actual product routing | 100 object names, settings and deterministic scene state |
| large | public `read_console`, one JSON log entry | Configurable ASCII message, default 4MiB; HTTP negotiates `large_result_v1` |
| job | public `get_test_job` | Terminal succeeded job with valid test result summary |

State measures raw command routing and JSON, excluding the `editor_state` resource formatter and pushed-state cache. Job measures terminal status polling, excluding job creation, test execution, focus nudges and multi-second server-side waiting. These exclusions are recorded in every report.

Each workload has a cold first call, configurable excluded warmup calls, and warmed observations. Calls may run in bounded concurrent batches. Every observed output—including cold, warmup and recovery calls—is canonicalized, hashed and compared; a transient mismatch cannot be hidden by a matching last sample. Output byte counts are canonical decoded-result bytes, while peer byte counts include its transport envelope and readiness-ping results. RPC counters distinguish user commands from product HTTP readiness pings.

## Stage meanings

| Field | Actual observation | Limits |
| --- | --- | --- |
| `client_total_ms` | Client call initiation through SDK reply decoding | Includes all stages below |
| `queue_ms` | Existing legacy connection lock admission plus owned peer execution admission | Framework scheduling and uninstrumented hub locks remain in residual |
| `synthetic_unity_work_ms` | Actual elapsed declared synthetic work at owned peer, including readiness pings | Includes timer/scheduling effects; never real Unity time |
| `peer_serialization_ms` | Payload construction and peer JSON encoding, including readiness results | MCP serialization and response validation are in residual |
| `wire_response_framework_ms` | Client total minus the measured stages above | Combines wire, writes, framework dispatch, MCP serialization and response/client handling; cannot be called pure network time |

The fixture captures correlations out of band while sending the original product WebSocket envelopes unchanged. Owned tracing/instrumentation overhead is included in the observed pipeline, including the peer's work interval; these timings are not an uninstrumented CPU profile. Stage durations are measured on the process that owns them; clocks from separate processes are never subtracted. Stage percentiles are computed independently and should not be added together. Missing peer observations or materially negative residuals fail the run.

## Lifecycle and provenance

Cancellation waits until the owned peer confirms the actual user command started, then cancels the client call. Synthetic peer work deliberately continues for 500ms. The tool records cancellation return latency, drains the late result, checks pending/retained results, and verifies a following call. This demonstrates request cleanup, not cancellation of Unity execution.

Reconnect deliberately closes the owned TCP connection or replaces the registered WebSocket peer, keeping the MCP client session. A succeeding public call and registration count confirm recovery; process restart and MCP session resumption are excluded.

The JSON report records interpreter/platform/dependency versions (including native Pydantic codecs), options, Git HEAD, SHA256 of all `Server/src/**/*.py` source files and owned harness files before/after, cold startup to MCP initialized, distributions using nearest-rank p50/p95/p99, raw observations, output equivalence and explicit limits. A source change/addition/removal during measurement saves the report and fails the run so it can be repeated against a stable checkout. These are current-checkout stdio/HTTP measurements, not before/after measurements against a historical commit.
