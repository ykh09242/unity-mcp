# Transport architecture regression fixture

Compiles the production WebSocket client, dispatcher, command registry, response projection,
owned command scheduler and large-result writer with Unity's Mono framework assemblies;
runs the executable on the installed Unity Mono runtime. No Unity Editor process starts.
The fixture only replaces Unity APIs, service discovery, authentication lookup and the
editor-state publisher boundary. It opens an ephemeral loopback WebSocket peer.

```powershell
& tools/tests/fixtures/transport_architecture/RunTransportArchitecture.ps1 `
  -UnityData $unityData -SdkPath $sdkPath
```

Set `$unityData` to an installed Editor's `Editor/Data` path and `$sdkPath` to the
compiler's .NET SDK directory. The runner shares the Phase12 WebSocket source map,
uses the SDK's Newtonsoft.Json and Unity's bundled NUnit, records source SHA256
values, and removes its owned temporary compile directory.

Real socket checks cover receiver responsiveness during an asynchronous command, main-thread
handler invocation, capacity/duplicate rejection, deadline measurement from receipt, expiration
before execution, FIFO preservation after response cancellation, stale sender ownership,
negotiated binary transfer with control-message interleaving, opaque legacy IDs and teardown
that retains the actual handler settlement barrier after disconnect. The benchmark compares the previous JSON text
round trip to the structured production projection using a 256KiB Unicode result and exact
output equality. Timing and allocation values are local measurements, not latency guarantees.

Run the NUnit suites through Unity EditMode for host synchronization, state
observation and assembly loading. This includes `TransportArchitectureTests`,
`CooperativeCancellationTests`, `CooperativeHandlerCancellationTests`,
`BatchExecuteCancellationTests`, `TransportCommandDispatcherTests` and
`WebSocketTransportClientTests`. The explicit real-SDK and native-consent cases
require their own loopback server or admitted test project. The Windows
package-shapes smoke step runs the standalone socket, cancellation and projection
assertions before license activation.
