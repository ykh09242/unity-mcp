# Service optimization experiments

These optional probes support [the implementation decisions](../../../docs/development/service-optimizations.md).
`results.json` preserves individual samples rather than only rounded speedup claims.
They are synthetic workloads on one Windows workstation, not production latency guarantees.

## Python

Run `Server/.venv/Scripts/python.exe tools/experiments/service_optimizations/shared_read.py`
from the repository root using the existing environment. It exercises the actual
`SharedToolReads` owner/admission/fetch/copy/cleanup path with one upstream call and
1, 4 and 16 concurrent consumers. Six samples per arm use repeated AABB ordering,
including same-arm controls. Timing excludes forced GC, result verification and
the separate tracemalloc pass. Memory is Python traced peak allocation, not RSS.
The output is `.work/service-optimization-probes/shared-read.json`.

The final candidate is enabled only for 16 or more concurrent waiters. A and B
therefore use the same copying behavior at 1 and 4 readers. The earlier encoded
JSON candidate is retained as negative evidence in `results.json`; it increased
peak memory substantially and is not used by production. Data includes distinct
ASCII and Unicode strings, large integers, nested containers and mutable consumers.

## Unity

Stage `ServiceOptimizationProbes.cs` in the existing test assembly of an owned
isolated Unity project with this package, Newtonsoft and Unity Test Framework.
Reuse its Library. Select `MCPForUnityTests.Editor.Performance.ServiceOptimizationProbes`
explicitly; the GPU case requires a graphics-enabled Editor. Do not include these
measurements in ordinary CI fixture selection or run them in a user's active project.
`UNITY_MCP_OPTIMIZATION_OUTPUT` optionally selects the report directory; otherwise
reports go under the isolated project's temporary cache. Each file-work probe owns
and removes a randomly named temporary directory. The GPU probe releases its own
textures and persistent readback buffers after all requests settle.

- OPT08 compares synchronous readback/PNG encoding to two owned readback slots,
  encoding at most one completed frame per update. Exact encoded output checks
  cover distinct frame patterns, orientation, color and alpha outside timing.
  Pixel storage is an explicit estimate excluding shared fixture/driver memory,
  not a native-memory measurement. `maxSliceMs` is the measured work section, not
  a profiler measurement of the whole Editor frame. Four samples per arm.
- OPT09 compares existing bounded projection plus serialization with a one-pass
  prototype using the actual bounded writer via reflection. Preparation is measured
  separately because it occurs on the Editor thread. Six samples per arm. Native
  `ProfilerRecorder` allocation events are collected in a separate untimed pass;
  legacy sampledBytes values were marker durations, not bytes, and are invalid
  as allocation-size evidence. New captures keep byte counts null and retain events. Mono's `GC.GetAllocatedBytesForCurrentThread`
  returned zero on this host and is deliberately excluded from the evidence.
- OPT11 compares GLB validation to an ideal content-cache hit that still hashes
  mutable external bytes. This omits cache lookup/copy costs, favoring the candidate.
  Same-length binary mutation invalidates its digest. A separate actual preparation
  probe measures carrying the digest of an exclusively owned, immutable download.
  Its four warm samples before and after are separate runs at `dac4b9db^` and
  `dac4b9db` behavior, not interleaved A/B. Download/hash creation is outside this
  timed phase; final saved-file verification is unchanged and excluded.
- OPT12 runs the same real artifact staging implementation behind the preceding
  two-worker FIFO wrapper and the production two-worker size/age queue. Submission
  order is identical; running work is never preempted. Four samples per arm use
  four 32 MiB and eight 512 KiB immutable input buffers, with no full payload copy
  for queue admission. All jobs finish and staging files are removed. This is a
  finite mixed burst; sustained starvation prevention is covered by the fake-clock
  causal regression test, not claimed from this finite throughput probe.

Keep raw samples and source identities when repeating an experiment. Do not
interpret tiny differences between identical controls as useful speedups. There
is no evidence here for increasing concurrency adaptively, for another GPU backend,
or for removing external-file/final-publication integrity checks.

## Shared regression cohorts

The [large-workload cohort guide](../../../docs/development/large-workload-cohorts.md) adds source-bound raw captures and environment-matched advisory comparisons around these probes. `shared_read.py` also writes `concurrent-readers.json`; the strengthened OPT12 probe verifies each output digest and writes `mixed-artifacts.json` alongside its legacy sample file. Follow the guide's explicit output/input identity settings when using the common capture adapter.

## Serialization-only delivery experiment

`serialization_delivery.py` evaluates a last-writer optimization boundary using
SDK `CallToolResult`/`JSONRPCResponse` encoding and the actual settled binary stdio
writer. Set `PYTHONPATH` to the repository root and `Server/src`, then run:

```sh
python tools/experiments/service_optimizations/serialization_delivery.py --output <owned-output-file.json>
```

The output parent must exist. The experiment records Python/package versions and
hashes of its serializer, immutable copier and writer before and after execution.
`serialization-delivery-results.json` preserves all measured rounds and frame
digests. Six alternating rounds per timing/memory series give three samples per
arm; tracing runs separately. Inputs are 8,000 rows/16 readers, 25,000/4 and
50,000/1, each with ASCII and Unicode strings. All frames, typed request IDs,
newline bytes and flush counts must match the SDK baseline.

This is an intentionally narrow lower-bound experiment: minimal structured
results have empty content blocks; authority, response reservations, middleware,
transport queues, network transfer and client parsing are excluded. It is not a
`SessionMessage` replacement or full HTTP measurement. The prepared arm encodes
one immutable result and makes request-specific frames; unsupported Python models
or shapes fall back to the baseline. Regression tests retain arbitrary integers,
Unicode and float bits, reject aliases/cycles from sharing, preserve mutation
isolation and wait for actual blocked flush completion on cancellation.

The candidate is **not adopted**. Multiple readers improve in this final writer
slice, but one 50,000-row reader takes 17-21% longer. More importantly, the existing
shared-read API returns independently mutable values before FastMCP builds each
caller's result. Skipping that contract or the response owner's reservation and
release boundaries is not justified by last-writer timings. Keep the measured
production copy path; a future opt-in immutable SDK contract needs full delivery,
ownership and middleware evidence before replacing it.
