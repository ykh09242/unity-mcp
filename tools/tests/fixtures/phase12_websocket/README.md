# Phase12 WebSocket settlement regression

Run from PowerShell with an installed Unity Editor's `Editor/Data` path in
`$unityData` and the compiler's .NET SDK directory in `$sdkPath`.

```powershell
& tools/tests/fixtures/phase12_websocket/RunPhase12WebSocket.ps1 `
  -UnityData $unityData -SdkPath $sdkPath -Evidence .tmp/websocket-check -Case all
```

The runner compiles the WebSocket transport, dispatcher, registry, command
ownership, response projection and large-result writer with the installed Unity Mono reference
assemblies and runs on Unity Mono. It starts no Unity Editor, touches no saved preferences or
credentials, and opens only ephemeral loopback WebSocket peers. It uses the .NET SDK's
Newtonsoft.Json and Unity's bundled NUnit. `-Case reconnect`, `force`,
`dispose` or `bytes` selects a narrower check. `-Case architecture` runs only the existing
source-linked architecture harness's real-socket scenario, including teardown. The
transport architecture runner uses the same source map for its additional cases.
`-Case inputs` selects the direct prepared-input boundary case: default and null inputs are
rejected synchronously with zero frames, while initialized empty text emits one empty frame.

Six settlement cases hold the real registered legacy handler or cooperative handler cleanup
behind a completion gate. The original generation uses a real socket and production receive
loop. Automatic closure runs the production closure/reconnect path using a second
ephemeral endpoint. Forced restart
calls the same client's public `ForceStop` twice and public `StartAsync`. Disposal runs on the
pumped main thread. A gate watchdog detects simple blocking disposal; the runner's separate
30 second process timeout terminates the owned fixture if a regressed Dispose prevents the
main thread from executing cleanup continuations. The negative observation window is bounded at 150 ms; actual
handler entry, cleanup completion and following mutation use explicit gates and counters.

Before gate release, owned drain and replacement connection must stay pending. After release,
the new peer sends B and event order must be A settlement followed by B invocation. On the
unfixed source, an early-completed drain additionally drives B to demonstrate that the
replacement handler actually overtakes held A, rather than merely inferring the race.

The byte cases compare reconstructed bytes against `Encoding.UTF8.GetBytes`, including a valid
surrogate pair spanning the staging boundary, isolated surrogates, CJK text, legacy text,
negotiated chunks and gzip. Both public and prepared paths must reject a Unicode payload whose
byte length exceeds the writer's cap while its character count is smaller. The actual transport
fallback envelope is checked through a real socket. PreparedJson is discovered reflectively
so the same fixture can validate original public-path byte behavior before the optimization.
The additional input case directly exercises the current internal readonly struct contract.
The Windows package-shapes smoke step runs `all` and `architecture`. For tests inside
an Editor, run `WebSocketTransportClientTests` and `TransportCommandDispatcherTests` in EditMode.

This does not validate real Editor frame scheduling, assembly reload, package loading or
cross-instance coordination. A legacy handler that never settles intentionally prevents new
mutation execution; synchronous ForceStop/Dispose still return. Disposal is terminal; only
ForceStop is followed by same-client restart.
