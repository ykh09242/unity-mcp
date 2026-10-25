# Final component paging alternative: direct page access

The final bounded algorithm was rejected at its first complete PC06 screen.
Production is restored to the exact baseline; no runtime paging optimization is
shipped. The [results report](results-report.md) records all observations and the
[closure receipt](final-restoration-summary.json) confirms final correctness and
fixture restoration. The rejected candidate preserves the complete baseline
`GetComponents<Component>().Where(c => c != null).ToList()` collection and
`allComponents.Count`, then replaces only the `Skip`/`Take` page list with a
clamped page count and indexed loop. Default response-list growth, budget
creation, serializer calls, getter opt-in, error handling and response envelope
remain unchanged. No cache, native collection API change, dependency, smaller
limit or production instrumentation is introduced.

The cursor is already clamped to `[0, total]`, so
`Math.Min(pageSize, total - cursor)` cannot be negative and `cursor + pageCount`
cannot exceed `total`. Missing slots are filtered before cursor clamping.
Filtering still completes before getters run. Reentrancy, later destruction,
newly added components and detached responses retain baseline snapshot behavior.
The existing 43-case focused native suite is sufficient; no loop-mirroring test
was added for this final delta.

## Bounded experiment sequence

The earlier [PC03 experiments](../component-paging-cost-20261025/README.md)
remain rejected and unchanged. PC04's unchanged same-assembly baseline control
still showed ordinary property-workload asymmetry, so the follow-up moved to
the same actual production method across frozen source states. That control
does not establish the cause of earlier results.

The [PC05 source-crossover protocol and array candidate](../component-paging-alternatives-20261025/README.md)
are frozen. Its first full matched source block rejected array compaction:
256 components/page100/properties/mixed/wire sampled managed-heap excursion
increased from 7322624 to 7920640 bytes (+8.17% mean, +7.82% median). All four
within-session candidate repeats exceeded the maximum baseline repeat.
Favorable metadata/large-page cost and adverse ordinary tails remain evidence.
This was one independent source block, not four independent experiments, and
does not prove universal causality. No additional confirmation blocks were
run for that rejected algorithm.

The final candidate is labelled `candidate2-direct-page`: it is the second
measured algorithm in this follow-up, corresponding to the narrower direct-page
approach in the initial shortlist. It retains the full filtered list instead
of compacting Unity's native array. If its first screen is adverse or ambiguous,
stop without trying another algorithm. Only a promising screen can proceed to
the three matched source blocks required for adoption.

## Fresh PC06 production-method crossover

The [PC06 helper](../PC06ProductionMethodPagingBenchmark.cs) derives from PC05,
changing fixture/provenance names and the candidate source pin. Its measured
body is identical after dispatch selection. Because the helper/marker identity
changed, run a fresh PC06 baseline; PC05 baseline performance is not reused.
The [frozen comparator](../PC04FrozenBaselineComponentsResource.cs) remains
byte-identical and has no resource registration or NUnit dependency.

Install the comparator beside the package resource in `MCPForUnity.Editor`.
Install PC06, its marker and the paging tests only in the existing
`MCPForUnityTests.EditMode` test assembly. No asmdef or package dependency changes
are needed. Before measurement run the 34 paging cases, seven budget cases,
the exact EditMode material/mesh preservation case and PC06 parity: 43 total.

Each timing/allocation phase retains all 120 cases and 720 rows: actual counts
1/16/256/2048, the predeclared ten count/page/property cohorts, first/middle/end/
beyond/full/mixed reads, and construction plus complete JSON wire output.
Parity retains all 24 cohorts. Single/mixed reads use 24 cycles; full paging
uses three complete traversals. The 2048-component/page1/full workload remains
intact. Four `production-a` through `production-d` arms call the actual
`GameObjectComponentsResource.HandleCommand` in both source states. These are
within-session repeats. Two final frozen-comparator arms are drift markers,
never an offset to subtract or variance with which to widen acceptance.

The first full baseline/candidate source pair is the screen. If promising,
complete three independent matched source blocks in these orders:

| Block | First source | Second source |
| --- | --- | --- |
| 1 | Baseline | Candidate 2 |
| 2 | Candidate 2 | Baseline |
| 3 | Baseline | Candidate 2 |

Retain block 1 only if its exact helper and inputs remain unchanged. Timing and
allocation stay separate. Report each block, every case, median ratios, worst
and noisy cases, sampled heap, and released/retained managed state. Investigate
a repeated increase above 3% or one above 5%. Gains cannot waive a material
ordinary or heavy-workload regression. No adoption is inferred from source
identity, pooled medians or four repeats within a single source session.

## Exact setup and provenance

Set `PC06_SOURCE_STAGE` to `baseline` or `candidate2-direct-page`,
`PC06_BLOCK` to 1/2/3, `PC06_SESSION_POSITION` to 1/2, and
`PC06_PRODUCTION_SOURCE` to the absolute staged package resource path.
The helper rejects a stage, order or frozen source-hash mismatch. It records
the staged source SHA256, loaded production assembly MVID and actual method IL
SHA256 before timing, plus actual response CLR type/assembly and token parity.
QA must also retain sealed input hashes and explicit Unity recompilation
evidence; a source file hash alone does not prove the loaded method's source.

Use an absolute owned `PC06_OUTPUT_DIR`, and select these exact methods
individually with the existing 900-second test, 960-second inner and 1020-second
outer native runner limits:

```text
MCPForUnityTests.Editor.Resources.PC06ProductionMethodPagingBenchmark.VerifyNativeParity
MCPForUnityTests.Editor.Resources.PC06ProductionMethodPagingBenchmark.RunTimingMatrix
MCPForUnityTests.Editor.Resources.PC06ProductionMethodPagingBenchmark.RunAllocationMatrix
```

QA owns the isolated warm project, exact additions and generated metadata, then
restores its original input hashes, runner, launcher and empty filters. A host
owner serializes measurements; no concurrent test/build/format/native work is
allowed during a measurement lease.

## Counter scope

Use current-thread allocation bytes only if a known 64 KiB allocation calibrates
the counter. Failed calibration means null/unavailable. Zero-only working-set
and lifetime-peak counters are unavailable. Sampled managed heap can miss peaks
and is distinct from allocated bytes, native memory and process memory.
Post-release signed deltas and 4096-byte quantization require their absolute
values, not misleading ratios near zero. Zero CPU samples do not support
ratios. GC generation deltas are reported separately, never summed. Forced
collections exist only in the opt-in helper outside the measured call path.

The input manifest pins the executed source/helper inputs. The results manifest
records rejection; the closure receipt records the restored baseline. Historical
measured source/helper files, source snapshots and raw arm values remain exact.
