# Focused Unity stdio regression fixture

Run from the repository root with an installed Unity Editor's `Editor/Data` path in
`$unityData` and the compiler's .NET SDK directory in `$sdkPath`.

```powershell
& tools/tests/fixtures/phase12_stdio/RunStdioRegression.ps1 `
  -UnityData $unityData -SdkPath $sdkPath -EvidencePath .tmp/stdio-regression.json
& tools/tests/fixtures/phase12_stdio/RunIdleQueueBenchmark.ps1 `
  -UnityData $unityData -SdkPath $sdkPath -Label current -EvidencePath .tmp/stdio-idle.json
```

Add `-HostSourcePath <snapshot.cs>` to compile a host snapshot compatible with the
current fixture's production dependencies and host contracts.

Both runners share one dependency list and compile the complete host with Roslyn
against Unity Mono assemblies. The regression runner substitutes just the frame
write timeout initialization with 80 ms in an owned compile copy. The frame reader
uses a real client, and the harness disposes the returned frame lease after reading
its payload. The runners print source SHA256 hashes and recheck input freshness.

The token-observing NetworkStream subclass requires a real connected socket and therefore owns an ephemeral loopback pair. Its overrides hold header or payload writes deterministically and register the production token. Additional tests exercise the actual installed Mono NetworkStream with a pending send-buffer fill, silent/partial-payload reads, fragmented reads and successful stream reuse. A 2000 ms operation watchdog fails a timeout that never fires, then closes the exact owned stream and awaits both its frame operation and any prefill task in finally. Socket calls run behind Task.Run because some can block before returning their Task. A 45000 ms process watchdog operates against only the exact child object launched by the runner. These are correctness bounds, not latency benchmarks.

Normal writes verify exact length header/payload bytes. CTS lifetime is observed through its token's WaitHandle before settlement and disposal afterward. A cancellation-ignoring stream that reports success during disposal proves deadline cleanup cannot become successful partial delivery. Throwing disposal callbacks and already closed streams cover timer safety.

Queue checks exercise the complete production editor-update method using in-memory Unity/service boundaries, real JSON response/model types, a held dispatcher settlement task and source guard ordering. Heartbeat file I/O exits via the existing owned-endpoint guard. No real Editor, preferences, discovery service or credentials are touched. All sockets, token registrations and temporary compile files are disposed; the runner verifies its absolute temp-root/prefix before removal.

The 25 checks exercise Unity Mono's byte-array `ReadAsync`/`WriteAsync` branch.
`StdioFrameBudgetTests`, `StdioBridgeMultiClientTests` and `StdioQueueAllocationTests`
exercise the corresponding behavior in Unity EditMode.

`RunIdleQueueBenchmark.ps1` compiles the complete host unchanged. It emits raw allocated
bytes and elapsed time for delegate and reflection empty ticks and matching controls.
`-CompileOnly` checks its build. Use identical fixtures and an isolated CPU slot for
timing comparisons. The Windows package-shapes job checks that each row executes
without applying a timing threshold.
