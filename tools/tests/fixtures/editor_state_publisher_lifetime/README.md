# Editor-state publisher completion lifetime

This .NET 9 console fixture compiles the complete current `EditorStatePublisher.cs`
with the actual immutable `EditorStateFrame` and the exact current
`TransportCommandDispatcher.RunOnMainThreadAsync<T>` implementation. It also extracts
the transport's connection capture, owner-token check, cancellation, registration,
and captured-loop drain methods verbatim from `WebSocketTransportClient.cs`.
It uses the repository's existing `.compile-refs/Newtonsoft.Json.dll` and installed
.NET SDK; it needs no optional Unity package or NuGet test package. Pass
`--json-assembly <absolute-path>` to use another existing Newtonsoft.Json DLL.

`Boundary.cs` replaces Unity observation/wakeup, logging, socket I/O, and unrelated
command/tool-settings loops. It does not reproduce the publisher or transport state machines.
The cache boundary preserves synchronous initial observation, static observer retention,
and idempotent subscription disposal. Its hooks place cancellation before the initial
observation and before `Subscribe` returns. Dispatcher fields and a minimal queue pump
are scaffolding; the real dispatcher methods run unchanged. A controlled socket boundary
records aborts and can release a held send, while another send deliberately ignores both
cancellation and abort until the test releases it. The host does not run the
Unity editor, native editor callbacks, domain reload, or a real WebSocket connection.

From the repository root:

```powershell
Server/.venv/Scripts/python.exe tools/tests/fixtures/editor_state_publisher_lifetime/run.py --output .tmp/publisher-lifetime
```

`--output` must resolve inside repository `.tmp`. The runner writes only its selected
output directory and performs no cleanup. `--baseline <git-ref>` reads the same three
production sources from Git without changing the checkout. Reproduce the missing
completion/transport ownership failures with `--baseline ba0940b2`; this baseline
intentionally exits 1. Older baselines must contain the transport methods named by the
runner. Each run prints normalized publisher, dispatcher, and transport source SHA256
values. The boundary also accepts the older JObject observation contract.

The twenty-four scenarios cover cancellation before/during/after synchronous creation,
caller ownership, duplicate disposal, pre-cancellation, subscription and send failures,
100 cancellation/dispose races, 100 lost-result registrations, latest-slot coalescing,
in-flight send cancellation, registration capture collection, and explicit disposal
before subscription adoption. Added completion checks cover idle disposal, held sends,
cancellation-aware send cleanup, in-progress subscription adoption, and cancellation
callbacks running outside the publisher gate. Transport checks cover pending-send
retention, captured-socket abort, preservation of replacement owners, a discarded
dispatcher result, and publisher adoption after connection-loop capture.
Repeated registration also retains the replaced publisher's completion until its held
send settles, without changing registration protocol behavior.
The 100-registration check observes actual live boundary
observers, rather than inferring leaks from an allocation counter. The weak-reference
check keeps the connection token source alive while verifying manual disposal releases
its callback capture.

Cancellation in the narrow interval between `Register` returning and registration-field
adoption is protected by the same gate and disposed-state check as subscription adoption.
The fixture places deterministic hooks at subscription boundaries and stresses concurrent
disposal; it does not claim deterministic coverage of every instruction-level interleaving.
An unsettled publisher send uses captured-socket abort instead of waiting for the later
graceful-close phase. The fixture verifies the ordering and owner identity, not platform
WebSocket cancellation behavior or native Unity scheduling.
