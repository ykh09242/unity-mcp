# Focused Unity stdio regression fixture

Run from the repository root in PowerShell:

```powershell
& tools/tests/fixtures/phase12_stdio/RunStdioRegression.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401' `
  -EvidencePath reports/CS-20261006-mcp-usability/phase12/unity-stdio/green.json
```

Add `-HostSourcePath <snapshot.cs>` to run the identical fixture against an earlier host source. The retained Phase 12 baseline is `reports/CS-20261006-mcp-usability/phase12/unity-stdio/before-StdioBridgeHost.cs`.

The runner compiles the complete production `StdioBridgeHost.cs`, `Command.cs` and `TransportCommandResponse.cs` with Roslyn against Unity Mono reference assemblies. Only the timeout field initialization is substituted with 80 ms in an owned temporary compile copy. Both tested methods are unchanged. The runner prints and saves source SHA256 hashes and confirms inputs stayed unchanged during the run.

The token-observing NetworkStream subclass requires a real connected socket and therefore owns an ephemeral loopback pair. Its overrides hold header or payload writes deterministically and register the production token. Additional tests exercise the actual installed Mono NetworkStream with a pending send-buffer fill, silent/partial-payload reads, fragmented reads and successful stream reuse. A 2000 ms operation watchdog fails a timeout that never fires, then closes the exact owned stream and awaits both its frame operation and any prefill task in finally. Socket calls run behind Task.Run because some can block before returning their Task. A 45000 ms process watchdog operates against only the exact child object launched by the runner. These are correctness bounds, not latency benchmarks.

Normal writes verify exact length header/payload bytes. CTS lifetime is observed through its token's WaitHandle before settlement and disposal afterward. A cancellation-ignoring stream that reports success during disposal proves deadline cleanup cannot become successful partial delivery. Throwing disposal callbacks and already closed streams cover timer safety.

Queue checks exercise the complete production editor-update method using in-memory Unity/service boundaries, real JSON response/model types, a held dispatcher settlement task and source guard ordering. Heartbeat file I/O exits via the existing owned-endpoint guard. No real Editor, preferences, discovery service or credentials are touched. All sockets, token registrations and temporary compile files are disposed; the runner verifies its absolute temp-root/prefix before removal.

Scope: 25 focused checks, Unity Mono's byte-array ReadAsync/WriteAsync branch. The modern Memory branch and full live Editor integration are not executed here. The reviewer inspected installed Mono IL and found both array and Memory paths use the same token-precheck-only socket operations. Empty-queue guard ordering is structural evidence; allocation/throughput timing requires the parent's exclusive CPU slot. Mono emitted two abort_threads shutdown diagnostics on an earlier green run; raw evidence preserves them and does not claim quiet native-runtime teardown.

`RunIdleQueueBenchmark.ps1` is separately prepared and compiler-checked, with no timing execution. Run only in a parent-granted exclusive CPU slot. It compiles complete host source unchanged and outputs raw total allocated bytes/time for delegate/reflection stdio empty ticks plus matching control rows. `-CompileOnly` checks its build without measuring. Use `-HostSourcePath` for the baseline, then default host source for the candidate; freeze the same benchmark/stubs across both captures.
