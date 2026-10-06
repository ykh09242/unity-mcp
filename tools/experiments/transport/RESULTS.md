# Roadmap 9–11 experiment results

Recorded 2026-10-07 on Windows 10 build 19045, 24 logical processors. The product roadmap baseline is beta `f053700a`; these experiments are isolated synthetic programs, not measurements of that product's real Editor performance. Python 3.14.6/FastMCP 4.0.11/MCP 2.3.0 and .NET SDK 10.0.401/runtime 10.0.12/official C# MCP SDK 2.2.0 were used. Resolved .NET transitive versions are preserved in `dotnet/packages.lock.json`.

Commands and conditions are in [README.md](README.md). Raw samples are saved in `reports/CS-20261006-mcp-usability/phase7/prototypes/{raw-streams,mcp-sdk-stdio,startup}.json`. Reports are ignored local evidence; this summary is tracked with the runnable probes.

## 9. Paired control and data streams

| TCP configuration | Pong p50 (ms) | Pong p95 (ms) | Samples |
| --- | ---: | ---: | ---: |
| Single ordered stream | 1004.6140 | 1011.8462 | 30 |
| Paired control/data streams | 0.0475 | 0.0871 | 30 |

Each sample starts an actual 4MiB data write while the receiver reads 64KiB chunks with a requested 2ms delay. A frame writer gate prevents frame interleaving on the single stream. Paired mode uses the same data writer gate and a separate authenticated control stream. 64KiB socket buffers intentionally create backpressure. Windows timer granularity made the total slow-reader drain about one second; this is a forced contention scenario, not a normal latency forecast.

The synthetic binding rejects foreign session, stale generation, wrong role, wrong token, malformed token, and duplicate role. Both valid channel roles bind successfully. Closing a real Named Pipe control peer cancels the pending data read in **2.224ms**, below the **2000ms** bound. Sessions have a **15000ms** lifetime bound. All invariants passed.

**Recommendation: defer product adoption; preserve the paired-channel design as a candidate.** The demonstration supports separating heartbeat/control traffic when large ordered frames block a connection. Production adoption still needs negotiated capability/version fallback, owned session generation/reconnect behavior, queue limits, teardown review, and real product WebSocket behavior measured with the same contention. These prototype invariants are not a production authentication implementation.

## 10. Local IPC

Actual OS endpoints were used: Windows Named Pipe with `CurrentUserOnly` on both endpoints; AF_UNIX socket with an owned random path; TCP on loopback with an ephemeral port. **Unix domain sockets were supported and exercised on this Windows host.** No Linux or macOS run was performed.

| Payload | TCP p50 / p95 (ms) | Named Pipe p50 / p95 (ms) | Unix socket p50 / p95 (ms) |
| --- | ---: | ---: | ---: |
| Small | 0.0723 / 0.1307 | 0.0595 / 0.0787 | 0.0186 / 0.0263 |
| State-shaped JSON | 0.0775 / 0.1060 | 0.0664 / 0.0940 | 0.0188 / 0.0250 |
| 4MiB console payload | 6.0783 / 28.2248 | 3.3562 / 4.8237 | 2.4096 / 3.0530 |
| Completed job | 0.0791 / 0.1140 | 0.0461 / 0.0648 | 0.0235 / 0.0459 |

Each case has three warmups and 30 samples. Bytes are precomputed outside timing, uploaded, and echoed back; output equality is checked. This measures stream RTT across actual OS endpoints with peers scheduled in one process. It excludes JSON encoding, MCP, process scheduling between processes, Unity work, and product auth/discovery. Socket buffers use OS defaults and TCP_NODELAY; pipe buffers are requested at 64KiB. TCP tail variability and configured buffer differences limit generalization.

**Recommendation: defer product adoption.** Both IPC mechanisms are runnable and show a lower microbenchmark cost in this configuration. The microseconds saved on small payloads do not alone justify a protocol migration. A usable implementation must prove two-process/editor behavior, discovery, access controls, managed client packaging, platform fallback, reconnect, and fair comparison against the actual product transport. No product IPC transport was installed.

## 11. Representative official C# MCP server

Both servers perform real MCP initialization, tool listing, and calls over stdio using the same Python JSON-RPC client. Each workload has five warmups and 30 samples. The two experimental tool descriptors copy the parameter shape of `read_console` and `get_test_job` at baseline commit `f053700a`; phase7 additions, including `read_console.fields`, are excluded. These fixture descriptors match exactly across the SDKs, which does not establish equivalence with the final phase7 product schema. Every text JSON response and structured JSON response matches the common workload fixture; semantic SHA256 hashes agree across servers.

| Workload | Python p50 / p95 (ms) | C# p50 / p95 (ms) | Python / C# wire bytes |
| --- | ---: | ---: | ---: |
| Small | 1.047 / 1.358 | 0.173 / 0.360 | 282 / 321 |
| State-shaped JSON | 1.410 / 1.745 | 0.361 / 0.541 | 7878 / 10349 |
| 4MiB console payload | 56.318 / 75.617 | 42.720 / 52.002 | 8388860 / 8388899 |
| Completed job | 0.917 / 1.325 | 0.072 / 0.134 | 548 / 667 |

The client timer includes SDK dispatch/serialization, OS pipes, client write, and JSON parsing. Output canonicalization/hashing is excluded. Both SDKs include text and structured content, so a 4MiB semantic payload becomes roughly 8MiB on the wire. Different escaping/envelope defaults account for unequal wire bytes. State is a synthetic fixture selected via `read_console`, not a replacement Editor state resource.

Five alternating fresh-process startup trials, including fixture loading and MCP initialize, produced:

| Launch | p50 (ms) | p95 (ms) |
| --- | ---: | ---: |
| Actual Python interpreter with pinned repository packages | 1093.672 | 1123.130 |
| Release C# framework-dependent Windows apphost `.exe` | 279.269 | 287.927 |

Build/import files were cached. C# was not launched through `dotnet run`, and this does not claim NativeAOT or cold-machine startup.

Process counters sampled outside request timers:

| Case | Python CPU delta (s) | C# CPU delta (s) | Python / C# working set after case (MiB) |
| --- | ---: | ---: | ---: |
| Small | 0.046875 | 0.250000 | 96.82 / 51.16 |
| State | 0.062500 | 0.265625 | 96.95 / 58.46 |
| 4MiB | 1.203125 | 0.875000 | 108.95 / 691.04 |
| Job | 0.031250 | 0.000000 | 96.95 / 689.78 |

CPU deltas include background/tiered JIT/GC work between coarse Windows snapshots and are not precise CPU-per-request figures. Working set is an observed post-case sample, not a managed allocation profile or a continuous peak measurement; no forced GC is performed. C# memory retention after the large case was material and varied between runs. Final counters use the verified actual server PID. The Python server launches directly through its owned native process handle, avoiding a Windows venv redirector descendant; owned termination regression passed within five seconds.

**Recommendation: defer server migration.** C# is feasible and faster for this representative subset, especially startup and small responses. C# uses low-level official SDK list/call handlers and preloaded JsonElements; Python uses typed FastMCP function binding and dictionaries. C# does not reproduce all parameter validation, wrappers, resource/tool behavior, or Unity routing. The memory observation warrants an allocation/GC investigation. Before adoption, prove functional/schema/error/cancellation parity and deployment/client compatibility across the full intended scope, then benchmark real Unity transport work. No all-tools parity or full replacement is claimed.

## Verification

- `RunProbes.ps1`: restore in locked mode, Release build with zero warnings/errors, actual IPC and paired scenarios, MCP descriptor/output equality, and startup trials passed. Final MCP/startup evidence was rerun after removing the Windows redirector launch.
- `basedpyright --pythonpath Server/.venv/Scripts/python.exe --project tools/experiments/transport/pyrightconfig.json`: zero errors/warnings.
- Programming rule checker: no violations in the five Python files.
- `pytest tools/experiments/transport/test_probes.py -q`: **1 passed**, actual owned process exit verified within five seconds.
- Python compilation passed. Source modules own one responsibility each and remain under 200 nonblank/noncomment lines; no product transport or public product schema was modified.

Context7 was attempted outside the sandbox and rejected by its monthly quota. The authorized fallback used [official C# SDK documentation](https://csharp.sdk.modelcontextprotocol.io/v2/concepts/getting-started.html), restored official package XML, [FastMCP documentation](https://gofastmcp.com/servers/tools), and [Microsoft pipe/socket API documentation](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.pipeoptions?view=net-10.0). Credentials/session secrets were synthetic and stayed in memory; no real Editor, Main project, account secret, token, or license content was accessed.
