# Component paging cost

Both measured optimizations were rejected. Production
`MCPForUnity/Editor/Resources/Scene/GameObjectResource.cs` was restored byte for
byte to beta `a3efdeb8ae63ad5bf80ff63936f64398e17058d9`, SHA256
`1de68ed4a6b11ebcb0fdb99b4ba8c069334d82a9be7338eb959fdcd152da7198`.
This unit delivers stronger regression tests, an opt-in benchmark, and complete
evidence of the rejected experiments. Runtime paging remains the beta behavior.

The full candidate used one request-owned `List<Component>`, compacted missing
slots, indexed the selected page directly, and sized the response list to that
page. A narrowed candidate applied this only to `includeProperties=false` and
kept the entire original property-inspection body after one early metadata
guard. Both improved metadata cost, but repeated property-workload cost increases
failed the preservation gate. No further runtime variant was adopted.

The [portable experiment history](../benchmarks/component-paging-cost-20261025/README.md)
preserves both rejected sources, all eight complete arm-metric exports, source
pins, adverse cases, and concise native correctness receipts. The full and
narrowed results are separate experiments; neither is the restored runtime.

Both experiments preserved the cursor parser, full live component count, ID conversion, shared page
serialization budget, getter opt-in, and detached response ownership.
Missing slots are filtered before cursor clamping. Components destroyed by a
getter after that initial snapshot keep the existing serializer behavior.

Unity's [2021.3 GetComponents API](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/GameObject.GetComponents.html)
documents the generic list overload and its resizing/overwrite behavior. The
Context7 CLI resolved the Unity library but returned no matching documentation
for this overload; the official API page supplied the compatibility evidence.

## Native verification and measurement

The native paging fixture covers missing slots around page boundaries, ordered
IDs, complete live counts, reentrant getters, detached result mutation, cursor
saturation, aliases, and page bounds. Existing component budget tests cover a
shared enumeration budget across a page and recovery after an exceeded budget.
Reference compilation verifies C# compatibility; it does not execute Unity tests.

The opt-in [native benchmark helper](../benchmarks/PC03PairedNativeBenchmark.cs)
compares the candidate against the exact beta
resource class from `a3efdeb8ae63ad5bf80ff63936f64398e17058d9`. The baseline
[adapter](../benchmarks/PC03FrozenBaselineComponentsResource.cs) only renames
that class and removes its resource registration attribute.
Both handlers run on the same actual GameObjects. Their full response tokens,
component order/IDs, counts, properties, and detached result mutation are checked
before measurement. Parity covers actual component counts 1, 16, 256, and 2048,
page sizes 1, 25, and 100, both property modes, and first, middle, end, beyond-end,
complete paging, and mixed repeated reads.

The predeclared performance screen uses these `(count, page size, properties)`
cohorts: `(1, 1, false/true)`, `(16, 25, false/true)`,
`(256, 100, false/true)`, `(2048, 1, false)`, `(2048, 25, true)`, and
`(2048, 100, false/true)`. Each retains all six workloads. The large one-item
full-paging case remains intact. Each workload measures response construction
and construction plus complete JSON serialization separately, with an ABBA pair
and an unchanged baseline/baseline control. Single and mixed workloads use 24
repeats; full paging uses three complete traversals. Each phase emits 720 rows.

Timing and allocation runs are separate. Timing reports process CPU, page
median/p95/p99/max, and complete workload cycle latency. Allocation reports
current-thread allocated bytes only when the counter detects a known 64 KiB
allocation, GC collection deltas, sampled managed heap use, managed retention
after releasing results and forced collections, and process working-set samples.
Allocation instrumentation is excluded from allocated byte totals and is not
used to judge latency. Process lifetime peak working set is context rather than
a per-arm peak; managed and working-set samples can miss between-sample peaks.
Unity Editor background work and heap collection noise must be assessed using
the unchanged controls and an independent winning-case repeat.

## Decision and bounded results

Each candidate received a complete paired screen and an unchanged independent
confirmation. All 5,760 original arm rows are retained, including unfavorable
tails and heap observations.

| Candidate/workload | First screen | Independent confirmation | Decision |
| --- | --- | --- | --- |
| Full candidate, 256/page100/properties true/mixed/wire sampled managed-heap excursion | 7,270,400 to 7,563,264 bytes (+4.03%) | 7,231,488 to 7,974,912 bytes (+10.28%) | Reject |
| Narrowed candidate, 16/page25/properties true/mixed/wire timing wall per read | 262.780 to 287.579 us (+9.44%) | 257.045 to 277.525 us (+7.97%) | Reject |
| Same narrowed case, sampled managed-heap excursion | 6,225,920 to 6,955,008 bytes (+11.71%) | 6,426,624 to 7,043,072 bytes (+9.59%) | Reject |

The narrowed metadata path's 2048/page1/full construction wall improves 30.72%
then 29.97%, and construction plus wire improves 23.96% then 25.19%. Reported GC
falls from 33 to 11 and 35 to 13 per arm in both timing runs. Those benefits do
not waive the repeated ordinary property-workload increases. Its property body
was source-identical to beta after one guard, but source identity did not
demonstrate runtime cost preservation in the paired experiment.

The property wall adverse case's unchanged-control spread is 1.40% then 0.75%;
its sampled-heap control spread is 0% then 0.064%. Individual arms are asymmetric,
and runtime GC/heap state or background work may contribute. The decision does
not claim a retained-object leak or a universally causal slowdown. It records
that the required preservation was not demonstrated after the bounded repeat.
No additional variants or measurements were used to seek a favorable outcome.

## Opt-in reproduction

Both C# artifacts live under `docs/benchmarks/`, outside Unity's normal source
and test discovery. They are exact copies of the frozen native inputs; preserve
their bytes and class names when reproducing. Do not install them in a working
game project or register the baseline adapter as a resource.

| Input | SHA256 |
| --- | --- |
| `PC03PairedNativeBenchmark.cs` | `26c3c1dcc88ccf77cb0c441a20f120bc02adfe4082e52f11230d0b5794ca9c01` |
| `PC03FrozenBaselineComponentsResource.cs` | `fa82694268b0873928c1de33c89166933f82eaa55eb38c1c7bd57dd7a677ae9b` |
| Original beta `MCPForUnity/Editor/Resources/Scene/GameObjectResource.cs` | `1de68ed4a6b11ebcb0fdb99b4ba8c069334d82a9be7338eb959fdcd152da7198` |

The original beta file has Git blob
`af79af4a9f7711b614a765ef09052af68df1f7df` at the commit above. The adapter
extracts its original using directives and complete `GameObjectComponentsResource`
class into the original namespace, renames only the class declaration to
`PC03FrozenBaselineComponentsResource`, and removes only the resource attribute.
The handler and cursor parser bodies remain the original beta code. Comparison
uses the same current serializer and option parser in both arms, isolating the
component-list/page-construction change.

Use a caller-owned isolated clone of this repository, its `UnityMCPTests` project,
and its existing `MCPForUnityTests.EditMode` test assembly. The current checkout
contains restored beta runtime source. To reproduce an experiment, explicitly
apply its `.cs.txt` snapshot to that isolated clone's package source before
starting Unity. Choose the full or narrowed snapshot from the history directory;
their hashes are pinned in its manifest. That assembly identity supplies the
existing access to internal serializer/options APIs. Record the candidate Git
commit and source hashes, Unity version, platform, helper hashes, and output
paths with the run. Keep package versions and all other sources identical
between arms. The helper creates actual synthetic objects, including Transform
in its counts; it does not substitute a managed component array.

For example, in a Bash-compatible shell with an already installed supported
Editor, set the placeholders to fully qualified paths owned by the caller:

```bash
repo="/absolute/path/to/caller-owned/isolated-checkout"
project="$repo/TestProjects/UnityMCPTests"
evidence="/absolute/path/to/caller-owned/pc03-evidence"
editor="/absolute/path/to/Unity"
snapshot="narrowed-GameObjectResource.cs.txt"

# Explicit opt-in: replace only the isolated clone's package source.
# Use rejected-fullcandidate-GameObjectResource.cs.txt for the full experiment.
cp "$repo/docs/benchmarks/component-paging-cost-20261025/$snapshot" \
  "$repo/MCPForUnity/Editor/Resources/Scene/GameObjectResource.cs"

mkdir -p "$project/Assets/Tests/EditMode/Resources" "$evidence"
cp "$repo/docs/benchmarks/PC03PairedNativeBenchmark.cs" \
  "$project/Assets/Tests/EditMode/Resources/PC03PairedNativeBenchmark.cs"
cp "$repo/docs/benchmarks/PC03FrozenBaselineComponentsResource.cs" \
  "$project/Assets/Tests/EditMode/Resources/PC03FrozenBaselineComponentsResource.cs"

PC03_OUTPUT_DIR="$evidence" "$editor" -batchmode -nographics \
  -projectPath "$project" -runTests -testPlatform EditMode \
  -testFilter MCPForUnityTests.Editor.Resources.PC03PairedNativeBenchmark.VerifyNativeParity \
  -testResults "$evidence/parity.xml" -logFile "$evidence/parity.log"

PC03_OUTPUT_DIR="$evidence" "$editor" -batchmode -nographics \
  -projectPath "$project" -runTests -testPlatform EditMode \
  -testFilter MCPForUnityTests.Editor.Resources.PC03PairedNativeBenchmark.RunTimingMatrix \
  -testResults "$evidence/timing.xml" -logFile "$evidence/timing.log"

PC03_OUTPUT_DIR="$evidence" "$editor" -batchmode -nographics \
  -projectPath "$project" -runTests -testPlatform EditMode \
  -testFilter MCPForUnityTests.Editor.Resources.PC03PairedNativeBenchmark.RunAllocationMatrix \
  -testResults "$evidence/allocation.xml" -logFile "$evidence/allocation.log"
```

The environment assignment is scoped to each child process. The measurement
methods reject missing, blank, or relative `PC03_OUTPUT_DIR` values; the caller
must ensure that the destination is an owned private evidence directory. Each
method has a 900000 ms test timeout. A custom runner or launcher must allow that
duration: use at least 960 seconds for its inner wait and 1020 seconds for an
outer watchdog. The two measurement methods are explicit tests and must be
selected by their exact fully qualified method filters. Do not run the full
fixture accidentally alongside ordinary tests or use a default 180-second test
timeout. Warm the project and finish import/compilation before timing. Run CPU
and memory measurements on an otherwise idle host with exclusive access to the
Editor/project; an independent repeat uses the same frozen inputs.

Check the existing paging fixture, component budget fixture, and the exact
EditMode read-only material/mesh preservation test before measurement:

```text
MCPForUnityTests.Editor.Resources.GameObjectComponentsPagingTests
MCPForUnityTests.Editor.Tools.ComponentSerializationBudgetTests
MCPForUnityTests.Editor.Resources.GameObjectSerializationReadOnlyTests.EditModeComponentReads_PreserveAssignedMaterialAndMeshReferences
```

The paging fixture creates any missing-script prefab in its uniquely named
synthetic asset folder, imports that exact path, and deletes that folder and its
owned objects in `finally`. It does not refresh the whole project or alter real
assets/preferences. Preserve standard missing-script warnings and fail unrelated
errors. The earlier broader candidate's focused native paging/parity run passed
all 34 cases; the unchanged budget and exact read-only checks passed separately.
The narrowed candidate subsequently passed all 42 native paging, budget,
read-only, and parity checks. Correctness passed while its paired cost gate
failed; the runtime was restored to beta. Final restored-runtime correctness
passed all 42 checks with zero skips. The original native fixture inputs,
runner/launcher, and empty filters were restored after that final run. This
closure is separate from rejected-candidate performance evidence.

Parity covers all 24 count/page-size/property cohorts. The timed/allocation
screen uses only the ten cohorts listed above, with all six workloads retained
in each cohort. Each workload/output mode warms both implementations once, then
measures baseline, candidate, candidate, baseline, and two unchanged baseline controls. Single and
mixed workloads use 24 cycles; a mixed cycle has 16 reads in
first/end/middle/beyond order repeated four times. Full paging measures three
complete traversals. Construction and construction plus full JSON wire output
are separate modes. Each measurement phase must emit 720 rows and a successful
NUnit result; the helper rejects an incomplete row matrix.

Successful measurement logs include `PC03_RESULT=<absolute JSON path>`. Preserve
the timestamped `pc03-timing-*.json` and `pc03-allocation-*.json` files alongside
the NUnit XML/logs. Keep unfavorable and unchanged-control results. The output
includes the stopwatch frequency, Unity version, operation counts, calibration
delta, and arm labels needed to interpret the measurements. Current-thread
allocation bytes are not native allocation bytes, whole-Editor totals, or
allocation-event counts. Null byte totals mean that calibration was unavailable
or failed, never zero allocation. Forced collections, sampled peaks, retained
heap values, working set, and the lifetime process peak have different scopes;
small deltas and low-sample tails require the unchanged controls and repeat.

After recording evidence, remove only the two copied helper sources and their
generated metadata from that isolated project, or retain the entire caller-owned
fixture for the next repeat. Preserve the evidence independently. Do not change
the workload, count, timeout, response budget, or counter policy to turn an
unfavorable result into a winner. Both recorded runtime candidates remain rejected.
