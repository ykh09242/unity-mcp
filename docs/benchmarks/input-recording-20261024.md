# Input recording packet allocations

Measured on Windows with Unity 6000.0.69f1 and Input System 1.20.1. The candidate
removes the mouse handler's two temporary arrays and reuses a session-owned touch
identity set. Nested synchronous observations receive a separate set, preserving
the outer packet's deduplication. Disposal clears session scratch state.

The native allocation recorder observed exactly 32 known array allocations in
both runs. The managed byte counter failed its known-allocation calibration on
this Mono runtime, so byte measurements are `null`. The numbers below count
allocation events, not bytes. JSON output still allocates during motion.

| Workload | Packets/s | Allocation events/packet, baseline → candidate | Mean µs/packet, baseline → candidate | Batch-p95 µs/packet, baseline → candidate |
| --- | ---: | ---: | ---: | ---: |
| Mouse idle | 125 | 2 → 0 | 2.417 → 2.105 | 3.082 → 2.398 |
| Mouse motion | 125 | 32.008 → 30.008 | 15.323 → 12.495 | 35.918 → 34.678 |
| Five-touch idle | 125 | 5 → 0 | 11.337 → 7.381 | 15.926 → 8.301 |
| Five-touch motion | 125 | 155.04 → 150.04 | 75.445 → 57.059 | 136.148 → 91.382 |
| Mouse idle | 1,000 | 2 → 0 | 2.964 → 2.113 | 5.352 → 2.560 |
| Mouse motion | 1,000 | 31.76 → 29.76 | 14.498 → 13.839 | 23.861 → 21.502 |
| Five-touch idle | 1,000 | 5 → 0 | 10.471 → 7.363 | 13.776 → 8.412 |
| Five-touch motion | 1,000 | 153.8 → 148.8 | 70.821 → 58.039 | 94.083 → 67.763 |

All eight workloads emitted identical event counts and position checksums. The
candidate passed all 22 ordinary packet tests and the explicit benchmark. The
unchanged console revision passed all 28 console cases in the baseline run.
Coverage includes invalid terminal positions, unknown presses, slot reuse,
three-button order, duplicate primary touches, independent sessions, nested
observation and disposal during a callback.

## Reproduction

Use the same fixture in both runs:
`MCPForUnityTests.EditMode.Tools.Input.InputRecordingPacketTests.ProfilePacketsAt125And1000Hz`.
Select the explicit method in the Unity Test Runner; selecting only the class
intentionally skips the benchmark. Run in the same warm owned Windows Editor
project, keeping the Unity and package versions unchanged.

Keep the fixture from the allocation change when comparing with the correctness
baseline at `f717c5d822d150cdffdc1c19d03b9f790bfd17dd`. Overlay only
`InputSystemScenarioRecordingBackend.cs` from that baseline, then restore the
candidate backend for the second run. Source hashes, raw measurements,
calibration and parity comparisons are recorded in
[input-recording-20261024.json](input-recording-20261024.json).

The fixture uses isolated Input System devices and reusable native `StateEvent`
buffers, invoking the production packet handlers directly. Each batch models one
second of packets distributed across 60 game updates. Batches are unpaced and do
not wait for wall-clock seconds. Two batches warm the handler before 31 measured
batches. Position writes and handler execution are timed; session construction,
buffer creation, transition-budget resets and bookkeeping are outside the interval.
Allocation event counts are collected in a separate untimed workload pass to avoid
profiler instrumentation skewing the timing comparison. Invalid or full allocation
captures fail the test rather than reporting an incomplete count.

Batch-p95 is the p95 of the 31 batch means divided by their packet counts. It is
not an individual-packet percentile, frame-time percentile or GC-pause measurement.
This paired run supports retaining the allocation reduction; its timings do not
establish physical-device polling performance or results on other platforms and
Unity versions.
