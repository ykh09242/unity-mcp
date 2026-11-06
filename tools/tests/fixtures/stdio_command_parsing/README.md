# Stdio command preparation fixture

This fixture compiles complete production parsing, timeout, queue admission, dispatch,
cancellation and input-release methods extracted from `StdioBridgeHost.cs`. It also
compiles the actual command model, transport JSON reader, response envelope and scalar
helpers. Unity's heartbeat/wakeup, dispatcher execution and journal boundaries are
controlled substitutes. It does not launch Unity or exercise a network connection.

```powershell
Server/.venv/Scripts/python.exe tools/tests/fixtures/stdio_command_parsing/run.py `
  --baseline <baseline-commit> --output .tmp/stdio-parsing-baseline
Server/.venv/Scripts/python.exe tools/tests/fixtures/stdio_command_parsing/run.py `
  --output .tmp/stdio-parsing-candidate --require-reuse
```

Use an installed .NET SDK with the .NET 9 targeting/runtime packs and the existing
`.compile-refs/Newtonsoft.Json.dll`. No NuGet package dependencies are added. Production
source hashes identify the inputs. The baseline adapter calls the original ingress
helpers in their original order; the candidate uses the prepared-input path. Both run
the unchanged extracted queue/dispatch methods, rather than copies of their logic.
`--require-reuse` fails against the baseline because the Editor dispatch deserializes
the input again. Compatibility cases pass on both versions.

Coverage includes malformed/scalar/null/array input, typed parameter errors (including
whitespace-sensitive error locations), duplicate and case-interleaved properties,
date-like strings, progress prelude rejection, ordinary duplicate acceptance, timeout
and arbitration binding, cancellation, release and prepared-command identity. Native
`StdioBrokerResendTests` add real dispatcher checks and retain the resume-deadline and
ticket-ownership coverage. Other socket tests cover authenticated ingress and retry
ownership.

Allocation measurement uses `GC.GetAllocatedBytesForCurrentThread` after warmup, on one
thread, with a 16 KiB string parameter and 300 commands. It separately measures Editor
admission/dispatch for one prepared command. These are managed allocations for the
fixture boundaries, not native Unity latency or wall-time measurements.

Preparation intentionally retains one typed command graph while it waits in the bounded
queue; the raw frame remains available for exact retry comparisons. The temporary DOM
used for prelude/timeout classification is discarded before asynchronous admission.
Before typed deserialization, its token tree estimates wire bytes, UTF-16 raw text,
512 bytes per token/container/property, and UTF-16 property/value strings. The existing
frame lease grows to this estimate only when it exceeds the original five-times-wire
charge. Growth is nonblocking within the same lane and aggregate capacity; insufficient
capacity receives the existing queue-capacity error before journal or queue admission.
This estimate does not bound transient DOM parsing, custom converter output, handler
results or process RSS. Growth/dispose races, dense-input refusal, sparse string acceptance,
and cancellation releasing the entire adjusted lease are exercised.
The retention probe holds 128 commands sharing one raw payload string and reports the
managed heap difference before/after releasing their inputs. It isolates the added graph
retention; real independent frames also retain their own raw strings. Count and input-frame
reservation capacity limits are unchanged. Both cancellation and completed execution clear the
prepared input with its raw input lease.
