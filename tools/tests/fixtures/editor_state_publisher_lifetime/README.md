# Editor-state publisher cancellation lifetime

This .NET 9 console fixture compiles the complete current `EditorStatePublisher.cs`
and the exact current `TransportCommandDispatcher.RunOnMainThreadAsync<T>` method.
It uses the repository's existing `.compile-refs/Newtonsoft.Json.dll` and installed
.NET SDK; it needs no optional Unity package or NuGet test package.

`Boundary.cs` replaces only `EditorStateCache`, `EditorApplication`, and logging.
The cache boundary preserves synchronous initial observation, static observer retention,
and idempotent subscription disposal. Its hooks place cancellation before the initial
observation and before `Subscribe` returns. Dispatcher fields and a minimal queue pump
are scaffolding; the real dispatcher method runs unchanged. The host does not run the
Unity editor, native editor callbacks, domain reload, or a real WebSocket connection.

From the repository root:

```powershell
Server/.venv/Scripts/python.exe tools/tests/fixtures/editor_state_publisher_lifetime/run.py --output .tmp/publisher-lifetime
```

`--output` must resolve inside repository `.tmp`. The runner writes only its selected
output directory and performs no cleanup. `--baseline <git-ref>` reads the same two
production sources from Git without changing the checkout. Reproduce the original
failure with `--baseline e8249f2e`; the original source intentionally exits 1.
Each run prints normalized source SHA256 values.

The fourteen scenarios cover cancellation before/during/after synchronous creation,
caller ownership, duplicate disposal, pre-cancellation, subscription and send failures,
100 cancellation/dispose races, 100 lost-result registrations, latest-slot coalescing,
in-flight send cancellation, registration capture collection, and explicit disposal
before subscription adoption. The 100-registration check observes actual live boundary
observers, rather than inferring leaks from an allocation counter. The weak-reference
check keeps the connection token source alive while verifying manual disposal releases
its callback capture.

Cancellation in the narrow interval between `Register` returning and registration-field
adoption is protected by the same gate and disposed-state check as subscription adoption.
The fixture places deterministic hooks at subscription boundaries and stresses concurrent
disposal; it does not claim deterministic coverage of every instruction-level interleaving.
