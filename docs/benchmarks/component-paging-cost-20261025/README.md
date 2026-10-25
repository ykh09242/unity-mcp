# Component paging experiment history

The full component-list optimization was **rejected**. Its paired measurements
showed lower average wall cost and fewer reported GC collections, but a
property-heavy workload had a repeated sampled managed-heap increase. The
metadata-only candidate was also rejected after its unchanged independent
confirmation repeated ordinary property-workload cost increases. Production
was restored to the exact beta source. This delivery contains tests, an opt-in
benchmark, and evidence; it ships no runtime optimization. The
[decision summary](decision.json) records both outcomes and the restored source pin.

The [manifest](manifest.json) pins the original beta, rejected full candidate,
current narrowed candidate, paging tests, and exact opt-in helper/comparator.
The [rejected source snapshot](rejected-fullcandidate-GameObjectResource.cs.txt)
preserves the full candidate that produced runs 426-429. Its SHA256 is
`5901e11b6376fb5cf445813dff9be17960b877640fc49057d001a09dcc19b123`.
The `.cs.txt` suffix keeps this evidence outside Unity compilation/discovery.

The [narrowed source proof](narrowed-source-proof.json) records that removing only
the private metadata helper and early guard from source
`a9696079b3e4520322700cc3d6226240c449f344135711997fa1a1f10e51ee7d` recovers
the entire original beta source. Property inspection retains the original
collection/projection, default list growth, budget creation order, loop, and
response envelope. This source proof established the intended boundary, while
the paired runtime observations still failed the cost-preservation gate.

## Complete rejected-candidate arm metrics

Each JSONL file contains every original arm row in its original order, one row
per line. Property names, metric values, zero-only process counter values, and
null allocation totals are preserved. Run metadata, source pins, original JSON
hashes, and compact export hashes are in the manifest. These files cover the
same predeclared ten performance cohorts, all six workloads, both output modes,
and six paired/control arms described in the [cost document](../../development/component-paging-cost.md).

| Run | Purpose | Rows | Complete arm metrics |
| --- | --- | --- | --- |
| 426 | First timing screen | 720 | [JSONL](rejected-full-426-timing.jsonl) |
| 427 | First allocation/heap screen | 720 | [JSONL](rejected-full-427-allocation.jsonl) |
| 428 | Independent full timing repeat | 720 | [JSONL](rejected-full-428-timing.jsonl) |
| 429 | Independent full allocation/heap repeat | 720 | [JSONL](rejected-full-429-allocation.jsonl) |

The narrowed candidate is a separate experiment, pinned to source `a969...`
above. Its complete first-screen rows and [source snapshot](narrowed-GameObjectResource.cs.txt)
are retained independently of the rejected full-candidate data:

| Run | Purpose | Rows | Complete arm metrics |
| --- | --- | --- | --- |
| 431 | Narrowed first timing screen | 720 | [JSONL](narrowed-431-timing.jsonl) |
| 432 | Narrowed first allocation/heap screen | 720 | [JSONL](narrowed-432-allocation.jsonl) |
| 433 | Narrowed final unchanged timing confirmation | 720 | [JSONL](narrowed-433-timing.jsonl) |
| 434 | Narrowed final unchanged allocation/heap confirmation | 720 | [JSONL](narrowed-434-allocation.jsonl) |

All 60 metadata-only timing group means improve in its first screen and 59 in
the final confirmation. The
2048/page1/full metadata wall ratios are 0.693 for construction and 0.760 for
construction plus wire; reported GC falls from 33 to 11 and 35 to 13 per arm.
This does not establish universal tail or memory preservation. The unchanged
property body still has arm-asymmetric sampled-heap increases in mixed/wire
cases: about 11.71% at 16 components, 3.14% at 256, and 7.55% at
2048/page25. In the final confirmation, the 16-component mixed/wire timing wall
mean is again higher by 7.97% after the first screen's 9.44%; its sampled heap
mean is higher by 9.59% after 11.71%. This repeated ordinary workload increase
is the narrowed candidate's rejection reason. Source-identical property
statements do not prove equal runtime cost in this experiment. All favorable
and adverse rows remain preserved, and no additional variant was attempted.

The helper and comparator remain byte-identical to the executed opt-in files:
[`PC03PairedNativeBenchmark.cs`](../PC03PairedNativeBenchmark.cs) and
[`PC03FrozenBaselineComponentsResource.cs`](../PC03FrozenBaselineComponentsResource.cs).
Their full filters, workload matrix, timeout/output setup, and reproduction
procedure are documented in the cost document. Retain all rows, including
adverse measurements and unchanged controls, when reproducing the comparison.

## Why the broader candidate was rejected

The 256-component/page100/properties true/mixed/wire workload increased sampled
managed-heap excursion from 7,270,400 to 7,563,264 bytes in run 427 (+4.03%), then
from 7,231,488 to 7,974,912 in the independent run 429 (+10.28%). Both paired
candidate directions were higher in each run. In the independent run, both
candidate arms exceeded every baseline/control arm; unchanged-control spread
was about 0.11%. GC count was five in every arm of this case in both runs.
This failed the memory-preservation gate despite favorable screen averages.
It is a sampled-heap regression observation, not a retained-object leak claim.

Tail negatives are also retained. The 2048/page100/properties false/mixed/wire
first p99 mean ratio was 2.635, while its independent ratio was 0.931. Conversely,
2048/page100/properties true/full/wire independently had p95 ratio 1.125, with
both candidate p95 values above all baseline/control p95 values, although its
first window improved. The 16/page25/properties true/mixed/wire wall means were
higher in both timing runs. These observations prevent a universal speedup or
latency non-regression statement. All first and repeat rows remain available.

## Correctness and counter scope

The [portable functional summary](functional-summary.json) records:

- Run 424: 28/36 passed. Six existing scalar-coercion expectations were stale,
  and two new missing-slot fixture setups were rejected by Unity before calling
  production. The repair changed test expectations/setup only.
- Run 425: all 34 focused paging/parity cases passed, including actual missing
  script slots, strict invalid-parameter errors, detached outputs, and getter
  reentrancy.
- Run 430: the narrowed candidate passed all 42 checks: 33 paging, seven budget,
  one exact EditMode material/mesh preservation check, and full native parity.
- Run 435: after rejecting both candidates and restoring exact beta production,
  all 42 checks passed again with zero skips. The original 2002-input native
  fixture, runner/launcher, and empty filters were restored; six temporary
  additions were removed, evidence/caches were retained, and no owned process
  survived. This closure establishes the restored runtime's correctness.

The parity helper covers 24 cohorts, broader than the ten-cohort performance
screen. Functional success establishes compatibility, not a performance win.
Portable source hashes and test-class counts identify these receipts; per-machine
input paths, host identity, and Editor logs are excluded from this public record.

Allocation calibration was false with delta zero throughout these eight measured
runs; allocated-byte totals are null and do not support byte/event-saving claims.
In-process working set and lifetime peak were zero-only and unavailable. Their
raw zeros are preserved for provenance, never interpreted as zero memory,
equality, or an improvement. External process observations apply to whole runs
only. Process CPU is coarse and often zero per arm, so zero-bearing ratios are
not evidence of speed. Managed heap samples can miss peaks and are separate
from native/process memory and allocated bytes. GC generation indices report
identical runtime counts and must not be added together.

Public packaging was prepared during an exclusive native measurement window
without changing source/helper/test inputs or running native/build/format checks.
After that window, all 5,760 exported rows were checked against the original
JSON values and ordering; metadata, original/export hashes, source snapshots,
and executed helper/comparator identities matched. Portable-content checks
passed. The manifest records these packaging checks separately from runtime
correctness and performance. Both runtime candidates remain rejected; the
separate restored-runtime correctness result is 42/42 passed.
