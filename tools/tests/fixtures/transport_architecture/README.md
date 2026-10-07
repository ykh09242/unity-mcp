# Transport architecture regression fixture

Compiles the production WebSocket client, dispatcher, command registry, response projection,
owned command scheduler and large-result writer with Unity's Mono framework assemblies;
runs the executable on the installed Unity Mono runtime. No Unity Editor process starts.
The fixture only replaces Unity APIs, service discovery, authentication lookup and the
editor-state publisher boundary. It opens an ephemeral loopback WebSocket peer.

```powershell
& tools/tests/fixtures/transport_architecture/RunTransportArchitecture.ps1 `
  -UnityData 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data' `
  -SdkPath 'C:/Program Files/dotnet/sdk/10.0.401'
```

The runner references existing `.compile-refs/Newtonsoft.Json.dll` and NUnit, records production
source SHA256 values, compiles into its own temporary directory, runs existing service NUnit
cases and new architecture cases, and removes only that verified temporary directory.

Real socket checks cover receiver responsiveness during an asynchronous command, main-thread
handler invocation, capacity/duplicate rejection, deadline measurement from receipt, expiration
before execution, FIFO preservation after response cancellation, stale sender ownership,
negotiated binary transfer with control-message interleaving, opaque legacy IDs and teardown
that retains the actual handler settlement barrier after disconnect. The benchmark compares the previous JSON text
round trip to the structured production projection using a 256KiB Unicode result and exact
output equality. Timing and allocation values are local measurements, not latency guarantees.

The real Unity test runner is still needed for host-specific Editor synchronization, actual
state observation and package assembly loading. Existing asynchronous Unity tools do not take
a cancellation token: response deadlines cancel the response, while FIFO waits for their actual
settlement; disconnect cancels response delivery and prevents stale sends, while asynchronous
teardown/reconnection still awaits actual handler settlement. ForceStop and Dispose cancel and
close synchronously without blocking Editor cleanup; a later same-client restart awaits the
retained settlement barrier. This fixture
cannot prove those existing tools abort their Unity side effects after disconnect.
