# Cache arithmetic and traversal follow-up

Both experiments were rejected. Production remains byte-for-byte equivalent
to beta `356e2462e7d19d4712686a395bf4e8646bfc664f`: no extra state, new limits,
loading-policy change or speculative performance change was adopted. This
preserves the existing indexed storage and bounded update behavior.

The test-only `ObjectIdCacheBenchmarkBaseline` now freezes that exact beta
implementation, with only namespace/class names changed. Its previous
`996daf65` dictionary comparator remains in Git history at `5bdd0516`.
Historical CSVs and the separate original snapshot comparator are unchanged.

## Candidates and decision

- **Reciprocal:** replace signed-mask remainder with cached reciprocal
  multiplication on 64-bit runtimes, retaining remainder on 32-bit runtimes.
  The formula follows the official
  [.NET HashHelpers implementation](https://github.com/dotnet/runtime/blob/v9.0.0/src/libraries/System.Private.CoreLib/src/System/Collections/HashHelpers.cs).
  It adds eight scalar payload bytes per cache without changing array sizes.
  Several maintenance cohorts and hot paths regressed; reject.
- **Scalar loads:** read the handle, upper ID and next slot individually in
  `Update`, instead of copying the 20-byte entry value. Storage, hashing,
  removal and callback ownership are unchanged. Two independent native runs
  did not establish a reproducible benefit across large workloads; reject.

These are measured observations, not proof that each difference was caused
by the candidate. Unchanged hot paths also moved, and paired samples vary
with execution order, native state and runtime scheduling. No small isolated
win overrides a large-workload regression or an unstable overall result.

## Native method

Windows Unity 6000.0.69f1 reused the same isolated project, Library and package
caches. No concurrent task build or benchmark ran during measurement. Each
run passed 35 ordinary maintenance cases and all four explicitly selected
paired benchmark cases. The older explicit snapshot benchmark was the one
intentional skip: **39 passed, 0 failed, 1 skipped** per run.

Each benchmark creates 32,768 or 131,072 actual transient native objects,
uses the same IDs for both strategies, and measures all-live, half-dead and
all-dead states under alternating and clustered destruction. One warm-up
pair precedes seven measured pairs with alternating execution order. Each
sample checks live counts and the unchanged 128-native-lookup update budget.
An eight-cycle churn case adds and removes 4,096 transient objects; cycle 0
is the initial growth cycle at 32K, and cycles 1-7 reuse capacity.

The second scalar run uses exactly the same source and fixture hashes as the
first, in a fresh Editor process with the same warm caches. All 2,002 guarded
input hashes stayed unchanged during each run. Cleanup left no owned process
and preserved protected inputs.

## Complete cohort results

CPU changes below are **ratios of per-strategy medians**, not medians of
paired percentages. Update p99 cells are medians of the seven per-sample
p99 values. Raw rows retain p95, p99, maximum update, elapsed totals, CPU,
registration/lookup times, allocation counters and payload.

| Initial entries | State / destruction | CPU change: reciprocal | CPU change: scalar first / repeat | Update p99 ms: beta / scalar first | Update p99 ms: beta / scalar repeat |
| --- | --- | --- | --- | --- | --- |
| 32,768 | All live / alternating | -1.80% | -4.08% / -1.39% | 0.0380 / 0.0364 | 0.0249 / 0.0239 |
| 32,768 | Half dead / alternating | +6.50% | -2.22% / -5.26% | 0.0360 / 0.0390 | 0.0364 / 0.0299 |
| 32,768 | All dead / alternating | +13.38% | -0.10% / -1.69% | 0.0250 / 0.0257 | 0.0364 / 0.0255 |
| 32,768 | All live / clustered | +3.57% | -6.51% / -1.62% | 0.0298 / 0.0291 | 0.0251 / 0.0240 |
| 32,768 | Half dead / clustered | -1.16% | -11.52% / -4.47% | 0.0374 / 0.0319 | 0.0283 / 0.0236 |
| 32,768 | All dead / clustered | -3.65% | -4.33% / +4.37% | 0.0288 / 0.0317 | 0.0240 / 0.0263 |
| 131,072 | All live / alternating | +0.33% | +1.45% / +0.08% | 0.0389 / 0.0392 | 0.0398 / 0.0392 |
| 131,072 | Half dead / alternating | -0.86% | +7.98% / +5.49% | 0.0513 / 0.0550 | 0.0509 / 0.0488 |
| 131,072 | All dead / alternating | +8.23% | +2.07% / -1.80% | 0.0376 / 0.0387 | 0.0345 / 0.0351 |
| 131,072 | All live / clustered | +1.44% | -2.70% / -6.57% | 0.0340 / 0.0340 | 0.0352 / 0.0342 |
| 131,072 | Half dead / clustered | +4.94% | -0.42% / -0.29% | 0.0445 / 0.0445 | 0.0373 / 0.0371 |
| 131,072 | All dead / clustered | +9.71% | +0.51% / +0.52% | 0.0340 / 0.0361 | 0.0307 / 0.0335 |

### Churn and memory

Churn changes use ratios of medians across reuse cycles 1-7; the raw CSV also
contains cycle 0 and individual ordering. Timed regions include insertion
and dead lookup/removal, excluding native object creation/destruction.

| Initial live entries | CPU change: reciprocal | CPU change: scalar first | CPU change: scalar repeat |
| --- | --- | --- | --- |
| 32,768 | +46.36% | +9.74% | +21.74% |
| 131,072 | +8.55% | +0.32% | +28.73% |

All experiments retain the same measured array payload: **872,472 bytes** at
32K and **3,754,488 bytes** at 131K. Churn grows 32K storage to 1,810,344 bytes
and then reuses it. Payload excludes headers, delegates and native memory;
the rejected reciprocal candidate additionally needs the eight scalar bytes
described above. No process-memory or peak-memory improvement is claimed.

Allocation-event counts match in every phase: zero for live/half-dead sweeps,
three for final cleanup. Calibration detects all 32 deliberate allocations.
The allocation-byte counter reports zero even for calibration, so byte
measurements are **unavailable**, not evidence of zero allocated bytes.

## Raw evidence and reproduction

- [Maintenance and hot paths](object-id-cache-followup-maintenance-20261025.csv): 528 rows, including 24 warm-up rows (`sample=-1`).
- [Churn](object-id-cache-followup-churn-20261025.csv): 96 rows, including initial growth cycles.
- [Allocation calibration](object-id-cache-followup-calibration-20261025.csv): 12 rows.
- [Rejected reciprocal patch](object-id-cache-reciprocal-20261025.patch).
- [Rejected scalar patch](object-id-cache-scalar-20261025.patch).

For reproduction, use a disposable checkout of this report commit and apply
one candidate patch with `git apply`; never apply both together. The patches
modify only the production cache for the experiment. Run this opt-in filter
against the unchanged frozen comparator in an isolated Unity project:

```text
MCPForUnityTests.Editor.Tools.ObjectIdCacheCostTests.BenchmarkPairedMaintenanceCost
```

Production source SHA-256 values below normalize line endings to LF:

| Variant | SHA-256 |
| --- | --- |
| reciprocal | `f116fb94a6240ecaa611d546b59dfc65974551471dfba3852067fc7d6902404d` |
| scalar | `6989c80dd0be7aa7fbcbab29743195bee1dbb9a5ae80985e527f33f29eefe2f4` |
| baseline | `203bba2e960c16748e8fb9c4b35d07d7c6b55fdfd71db5e73e2d115f963bf296` |

The reciprocal arithmetic was also checked against remainder for 22
capacities and signed/extreme/random handles on a 64-bit managed runtime.
Both candidates passed the existing managed model, collision, free-slot,
full-ID, growth, reentrant-callback and ownership checks on 32-bit Mono and
64-bit .NET. These checks establish correctness coverage, not native speed.

Native tests inject the supported InstanceID resolver into the version-
independent cache. They do not execute the Unity 6.6 EntityId facade or prove
performance on other operating systems, Editor versions or machines. CPU
cycles and elapsed time are distinct metrics; timings are not guarantees.
