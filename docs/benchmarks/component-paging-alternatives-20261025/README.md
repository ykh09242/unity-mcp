# Component paging alternatives

The array-compaction candidate was rejected at its first complete PC05 screen.
Production is restored to the exact baseline; no paging optimization is shipped.
The [final control and rejection report](control-and-rejection-report.md) records
all results. The sections below retain the bounded measurement protocol. The earlier
[PC03 rejected experiments](../component-paging-cost-20261025/README.md) remain
unchanged and are not superseded by a new measurement context.

## Candidate 1: retain and compact the native array

The candidate retains `GetComponents<Component>()`, filters live references
into the front of that request-owned array, counts every live component before
clamping the cursor, and indexes the selected range directly. It removes the
filtered full list and the `Skip`/`Take` page list. The output `List<object>`
still uses default growth, and the shared serialization budget, getter opt-in,
serializer calls, error handling and response envelope remain unchanged.

Filtering finishes before any getter runs. A getter that destroys a later
selected component therefore retains the existing serializer's null result;
newly added components appear only on a later request. There is no shared
scratch buffer, Unity-object cache, new dependency, lower cap, or collection
forced by production code. Existing missing-script, cursor, order, reentrancy,
budget and detached-response checks remain required. The added mutation test
covers initial count/order, destruction of a later selected entry, and additions
that become visible on the next read.

`baseline-GameObjectResource.cs.txt` preserves the exact restored beta source.
`candidate1-GameObjectResource.cs.txt` pins the candidate that QA must measure.
These snapshots stay outside normal Unity compilation.

## Same-assembly comparison control

The immutable PC03 comparator compiled in the test assembly while production
compiled in the Editor assembly. This is a potential measurement-context
difference, not an established explanation for the previous adverse results.
C# creates the same anonymous type for matching property names, types and order
within the same program. See the official [anonymous-types guide](https://learn.microsoft.com/en-us/dotnet/csharp/programming-guide/classes-and-structs/anonymous-types)
and [language specification](https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/language-specification/expressions#128173-anonymous-object-creation-expressions).

The new [frozen comparator](../PC04FrozenBaselineComponentsResource.cs) is the
PC03 baseline adapter with only its class name changed. Install it temporarily
beside the package resource in the same Editor assembly. It has no resource
registration attribute. Install the [new benchmark](../PC04SameAssemblyPagingBenchmark.cs)
in the existing EditMode test assembly. Before measuring, it asserts equality
of the actual resource/comparator Assembly objects and of the response envelope,
response data and metadata-row CLR Type objects. It also checks token parity.
The emitted result records assembly names and successful identity verification.

First install the exact baseline production snapshot and run the full A/A
timing and allocation matrices using the new comparator/helper. All arms then
execute unchanged baseline behavior through their actual full handlers. Keep
the results even if they show an adverse difference. Only after reviewing this
control should QA install the frozen candidate and run its matrices. A context
change does not retrospectively invalidate any previous measurement.

## PC04 control workload and decision gate

PC04 retains PC03's ten performance cohorts, six workloads (first, middle, end,
beyond, full and mixed), both property modes, construct and complete JSON-wire
outputs, 24 single/mixed cycles and three full traversals. Every phase must emit
720 rows. Parity retains all 24 count/page-size/property cohorts. The large
2048-component/page1/full case remains included. No unfavorable row is dropped.

PC04 proposed three independent candidate blocks with alternating `ABBA`,
`BAAB`, `ABBA` orders. Its initial unchanged-production control instead showed
material asymmetry in ordinary property cases, so no candidate was installed
under that comparison protocol. All 1440 control rows remain evidence. PC05
below changes the comparison to the same production method across source
states; it does not invalidate or explain away PC03 or PC04 observations.
Timing and allocation phases stay separate. Compare ordinary and large workloads,
latency distributions and complete-cycle cost, sampled managed-heap excursion,
post-release retained managed heap, and individual GC generation deltas.
Investigate a repeated increase above 3%, or a single increase above 5%, against
the same-context A/A noise. In particular, retain explicit scrutiny of the
16/page25/properties/mixed/wire and 256/page100/properties/mixed/wire cases that
were adverse previously. A gain is eligible only when ordinary and heavy cases
demonstrate preservation within that control noise across independent blocks.
Source identity alone does not satisfy preservation.

Use `PC04_OUTPUT_DIR` with an absolute owned evidence directory,
`PC04_EXPERIMENT=baseline-control` or `candidate1-array-compaction`, and
`PC04_ORDER=ABBA` or `BAAB`. Select these exact methods individually:

```text
MCPForUnityTests.Editor.Resources.PC04SameAssemblyPagingBenchmark.VerifyNativeParity
MCPForUnityTests.Editor.Resources.PC04SameAssemblyPagingBenchmark.RunTimingMatrix
MCPForUnityTests.Editor.Resources.PC04SameAssemblyPagingBenchmark.RunAllocationMatrix
```

Before measurement run `GameObjectComponentsPagingTests`,
`ComponentSerializationBudgetTests`, the exact
`GameObjectSerializationReadOnlyTests.EditModeComponentReads_PreserveAssignedMaterialAndMeshReferences`
check, and PC04 parity. The benchmark timeout remains 900 seconds; use the
existing 960-second inner and 1020-second outer native runner limits. QA owns
the isolated warm project and exact helper/test additions and later restores
its original input hashes, launcher, runner and empty filters. A host owner
serializes measurement windows; no concurrent test/build/format work is allowed.

## Counter limits

The current-thread byte counter is used only if it detects a known 64 KiB
allocation. Failed calibration means unavailable/null, never zero allocation.
Zero-only working-set/lifetime-peak counters are unavailable. Sampled managed
heap can miss peaks and is distinct from allocation bytes, native memory and
whole-process memory. Coarse zero CPU values do not support ratios. Identical
GC generation counts must not be summed. Forced collections occur only in the
opt-in measurement helper, outside the measured production call path.

## PC05 production-method source crossover

The [PC05 helper](../PC05ProductionMethodPagingBenchmark.cs) uses the actual
`GameObjectComponentsResource.HandleCommand` for its first four arms in both
baseline-source and candidate-source sessions. Labels `production-a` through
`production-d` denote within-session repeats, not different implementations or
independent experiments. The delegate is selected outside the clocked loop;
the call, output serialization, result release and counters retain the PC04
measurement body. The final `control-a` and `control-b` arms call the unchanged
frozen comparator as drift markers only. Do not subtract their offset, normalize
away an adverse result, or expand an acceptance threshold using their variance.

Each source session retains all 120 cases and 720 rows, the same workload,
component marker type, helper bytes, comparator bytes, tests, package sources
apart from the pinned resource, and existing native runner limits. Runtime
guards hash the staged production source before timing, verify its exact frozen
stage hash, and record the loaded production assembly MVID and method IL hash.
The functional log emits `PC05_PROVENANCE`; result JSON retains these identities.
A file hash alone does not prove the loaded method was compiled from that file:
QA also retains the sealed input manifest and explicit Unity recompile evidence.
The candidate method IL is expected to differ from baseline. These provenance
identities are not performance metrics.

The first full baseline/candidate pair is the screen. Stop if it is clearly
adverse. If it is promising, complete three independent matched source blocks
in these orders, retaining the screen as block 1 only if its frozen inputs are
unchanged:

| Block | First source session | Second source session |
| --- | --- | --- |
| 1 | Baseline | Candidate 1 |
| 2 | Candidate 1 | Baseline |
| 3 | Baseline | Candidate 1 |

Timing and allocation remain separate in every source session. Compare the four
actual-production repeats across the matched source states for every case.
Report each source block, median ratios and worst/noisy cases, sampled heap and
the released/retained managed state. Pooled summaries cannot erase a per-case
regression. Investigate repeated increases above 3% or a single increase above
5%, including ordinary property workloads and large complete paging. Ship only
demonstrated wins with no material regression; drift controls are diagnostic.

Set `PC05_SOURCE_STAGE` to `baseline` or `candidate1-array-compaction`,
`PC05_BLOCK` to 1, 2 or 3, `PC05_SESSION_POSITION` to 1 or 2, and
`PC05_PRODUCTION_SOURCE` to the absolute staged package resource source path.
The helper rejects a source/order mismatch. Use an absolute owned
`PC05_OUTPUT_DIR` for measurement output. Install the PC05 helper and its marker
only in the existing test assembly; the reused PC04 comparator remains beside
the package resource. Never add NUnit or other test dependencies to production.
Use these exact methods individually:

```text
MCPForUnityTests.Editor.Resources.PC05ProductionMethodPagingBenchmark.VerifyNativeParity
MCPForUnityTests.Editor.Resources.PC05ProductionMethodPagingBenchmark.RunTimingMatrix
MCPForUnityTests.Editor.Resources.PC05ProductionMethodPagingBenchmark.RunAllocationMatrix
```

The focused functional suite remains 43 cases, with PC05 parity replacing PC04
parity. Before the first candidate measurement, execute it with the candidate
source and record actual assembly/method provenance. Counter limits above apply
unchanged. The [final report](control-and-rejection-report.md) records the failed
screen. The final direct-page candidate was also rejected, and the
[restored-baseline closure](../component-paging-direct-page-20261025/final-restoration-summary.json)
confirms 43/43 passing checks and restoration of the original native fixture.
