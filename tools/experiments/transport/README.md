# Experimental transport probes (roadmap 9–11)

These are owned, synthetic experiments. They never connect to a Unity Editor, read project scenes, import product startup, or replace a product transport. Run from the repository root on Windows with its existing Python environment and .NET 10 SDK:

```powershell
tools/experiments/transport/RunProbes.ps1
```

Use `-OutputDirectory reports/my-run` to preserve a separate run. The runner namespaces fixtures and diagnostic files with random run IDs. Build output, NuGet cache, generated fixtures, and stderr are ignored beneath this directory. Evidence JSON is written to the chosen report directory (repository-wide reports are ignored). The default evidence location is `reports/CS-20261006-mcp-usability/phase7/prototypes/`.

First restore requires network access to NuGet. Dependencies are isolated under `.artifacts/nuget`. `packages.lock.json` records exact resolved versions; subsequent restores use `--locked-mode`. No global toolchain or product dependency changes are made. The tested toolchain is Python 3.14.6, FastMCP 4.0.11, Python MCP 2.3.0, Pydantic 2.13.5, .NET SDK 10.0.401/runtime 10.0.12, and official C# ModelContextProtocol 2.2.0.

## What each result measures

| File | Measurement | Exclusions and limits |
| --- | --- | --- |
| `raw-streams.json` / echo | Actual TCP, Windows Named Pipe, and Unix domain socket framed echo; three warmups, 30 samples for each workload | Same-process OS peers. No MCP SDK, JSON serialization/deserialization, process boundary, Unity, or product authentication in the timed region. Each echo uploads and returns the entire payload. |
| `raw-streams.json` / backpressure | Single versus paired TCP streams, 30 pong samples while a 4MiB data frame is consumed in 64KiB chunks with a requested 2ms pause per chunk | Deliberate slow reader and bounded socket buffers; Windows timer granularity can make each pause much longer than 2ms. This demonstrates frame-order head-of-line blocking, not a prediction of normal product latency. Both modes use the same writer frame gate and exact frame size. |
| `mcp-sdk-stdio.json` | Real MCP initialize/list/call over OS stdio pipes, five call warmups then 30 timed calls per workload | Same Python client for both SDKs. Timing includes client write, pipes, SDK dispatch/serialization, and JSON parse. Canonical output verification is outside timing. Synthetic handlers only; no Unity transport or work. |
| `startup.json` | Five alternating fresh-process starts through MCP initialize | Cached files/builds, fixture loading included. C# launches a Release framework-dependent Windows apphost `.exe`, not `dotnet run` or NativeAOT. This is not cold machine boot. |

Raw socket echo uses OS default buffers and TCP_NODELAY; the pipe requests 64KiB buffers. Backpressure explicitly requests 64KiB TCP send/receive buffers. Buffer defaults and scheduler behavior are part of the measured configuration. Do not interpret raw echo as WebSocket-versus-IPC performance.

The four payloads reuse `tools/tests/fixtures/transport_bench/workload.py`: small console reply, state-shaped object list, a deterministic 4MiB ASCII console message, and a completed job reply. State is a fixture selected through `read_console.filter_text`; it does not implement a `get_editor_state` resource. Raw bytes must match exactly; SDK structured content and text content must both match the canonical fixture. The 4MiB semantic payload becomes roughly 8MiB on MCP stdio because both structured and text content are sent. JSON escaping differs by SDK, so wire byte counts can differ while semantics remain equal.

## Session and cleanup experiment

`SessionBinding` owns a random in-memory session ID and 256-bit synthetic secret. Both channels must match that session, generation, expected role, and secret before use. A role may bind once. Named Pipes use random names with `PipeOptions.CurrentUserOnly` on both peers. Unix socket paths are random and deleted after stream disposal; TCP binds loopback and an ephemeral port. Credentials are never printed or saved.

The executable asserts rejection of foreign session, stale generation, wrong role, wrong secret, malformed secret, and duplicate role. It then closes an authenticated Named Pipe control client and verifies that the pending data read cancels within a two-second bound. Every session also has a 15-second cancellation lifetime. These invariants are a local experiment; they do not provide a production authentication/reconnection state machine or a cross-user security proof. The protocol intentionally has only length-prefixed frames capped at 8MiB.

MCP processes are always spawned and owned by the harness. Python uses the actual base interpreter with only the repository venv package directory added explicitly, avoiding a Windows venv redirector descendant. The server PID emitted to owned stderr must match the owned process handle. PowerShell samples CPU seconds, working set, peak working set, and private bytes outside request timing. CPU deltas include server background work between snapshots; the Windows counter granularity is coarse. Memory is sampled after each case rather than continuously and no forced GC is performed. Watchdogs terminate only the owned process through its existing OS handle; the cleanup regression test verifies actual exit within five seconds. No PID-based termination is needed.

## Schema scope and interpretation

The Python fixture copies the parameter names, scalar unions, enum values, and defaults of the two baseline tools `read_console` and `get_test_job` at beta commit `f053700a`. It excludes parameters added during phase7, including `read_console.fields`; it does not represent the final phase7 product schema. The C# official SDK advertises the exact exported Python fixture descriptors, and the harness checks equality between those two experimental descriptors. Descriptions/annotations are experimental and only the workload selector is functional. Other optional parameters are shape coverage, not implemented product behavior.

C# uses official SDK low-level list/call handlers with preloaded `JsonElement` fixtures. Python uses bound async FastMCP functions with typed arguments and preloaded dictionaries. C# therefore does not reproduce Python parameter binding/validation or every product wrapper. This is a representative SDK feasibility comparison, not an isolated language benchmark, a server replacement, full schema validation parity, or parity across all product tools.

The current recommendation is to retain all three as experimental evidence. Paired channels show strong isolation under forced backpressure but need a reviewed product session/reconnect design. IPC requires discovery/packaging/platform/authentication work and a real editor integration comparison. C# is feasible and fast for this subset, but migration needs functional parity and memory/allocation investigation before adoption. See the phase7 prototype findings report for recorded numbers.

## Verification and source documentation

```powershell
dotnet build tools/experiments/transport/dotnet/TransportProbe.csproj -c Release --no-restore
basedpyright --pythonpath Server/.venv/Scripts/python.exe --project tools/experiments/transport/pyrightconfig.json
Server/.venv/Scripts/python.exe -m compileall -q tools/experiments/transport
```

`RunProbes.ps1` is the real-surface verification: it fails on byte or semantic output mismatch, descriptor differences, session rejection/cleanup failures, restore/build errors, and child timeout/error responses. `pyrightconfig.json` keeps exhaustive `assert_never` defaults without reporting them as unnecessary comparisons.

Context7 lookup was attempted outside the sandbox and returned a monthly quota error. The authorized fallback was official primary documentation and restored package XML, not recalled version/API assumptions:

- [Official C# SDK v2 getting started](https://csharp.sdk.modelcontextprotocol.io/v2/concepts/getting-started.html)
- [Official C# tools and handler concepts](https://csharp.sdk.modelcontextprotocol.io/v2/concepts/tools/tools.html)
- [Official SDK NuGet releases](https://www.nuget.org/profiles/ModelContextProtocol)
- [FastMCP tools](https://gofastmcp.com/servers/tools)
- [Microsoft PipeOptions / CurrentUserOnly](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.pipeoptions?view=net-10.0)
- [Microsoft UnixDomainSocketEndPoint](https://learn.microsoft.com/en-us/dotnet/api/system.net.sockets.unixdomainsocketendpoint?view=net-10.0)

Context7 access can be expanded through `npx ctx7@latest login` or `CONTEXT7_API_KEY`. Package versions used by this probe are in the tracked lockfile.
