# Editor object ID cache maintenance

Unity 6.6 reverse object lookup needs the full native EntityId cached when an
integer handle is returned. A collected managed wrapper does not establish that
its native object died. Maintenance resolves the native ID before removing an
entry and never evicts a live handle to enforce a size cap.

Maintenance starts ten seconds after the first entry and resolves at most 128
entries per Editor update. The next interval starts when a sweep completes.
Play mode transitions request a sweep through the same bounded path. An individual
lookup removes a dead handle immediately. Assembly reload, Editor exit and
disposal clear owned state and callbacks.

## Storage and retirement

The dictionary key is the lower 32 bits of the full ID. Its value stores the upper
32 bits and circular previous/next handle links. Reconstruction converts the
signed handle to an unsigned 32-bit value before combining the halves. The
internal insertion API takes the full ID and derives its key, enforcing this
invariant. Repeated registration of the same full ID skips the dictionary write.

Maintenance reads each entry once for resolution. Consecutive dead entries are
unlinked as a run within the current bounded update, reducing repeated neighbor
writes. Links remain intact during native lookups; a `finally` block retires an
earlier dead run if a later lookup throws. A whole remaining ring is retired as
soon as it is known dead, including when individual lookups removed most of the
original sweep between updates. No pending run survives an update.

For an isolated dead entry between live entries, maintenance reuses the neighbors
already read by that update. This avoids two repeated dictionary reads in the
common alternating pattern. A leading dead entry can still need its predecessor
read from the dictionary. If both neighbors are the same sole survivor, both
links are repaired in one value before writing it back. Cached neighbors are
local values, discarded at each update. Longer runs and trailing/exception cleanup
retain the existing path.

All-live traversal uses a small scan loop. On the first dead entry it hands the
unused part of the same 128-lookup budget to run retirement, once per update.
Predecessor caching activates only after an isolated deletion and is invalidated
after a longer run. An all-live update needs no predecessor entry copies.

Native resolution can [load an asset from disk](https://docs.unity3d.com/6000.6/Documentation/ScriptReference/EditorUtility.EntityIdToObject.html),
which can invoke user [OnEnable code](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/ScriptableObject.OnEnable.html).
One fixed 32-bit mutation counter detects reentrant registration, replacement,
removal or clearing. Before any cached-entry write and during exception cleanup,
maintenance checks that counter. On a change it discards the stale pending run
and schedules a full current ring from the preserved cursor. It does not restart
the visited prefix on every load. Individual lookup refetches current links after
a mutation, removing the same dead identity immediately while preserving a
replacement identity. No per-entry storage, scratch array or native retry is added.

Removing the final entries clears their occupied dictionary slots and stops
maintenance. It does not scan and zero the whole retained capacity on that last
update. Explicit `Clear` still clears the dictionary. Capacity is reused, so
removal does not promise to return all managed backing storage to the OS.

Additions, overwrites and removals preserve the cursor. New entries participate
by the next sweep at the latest. Large caches take more updates to maintain;
expiry remains eventual and depends on Editor updates continuing. A sweep of
131,072 handles takes 1,024 active updates, with no new timeout or live-entry cap.

The public integer wire format is unchanged. Different full IDs with identical
lower bits retain the existing latest-value overwrite behavior; this change does
not resolve that pre-existing identity collision possibility.

## Verification and reproduction

`ObjectIdLifetimeTests` and `ObjectIdCacheMaintenanceTests` cover expiry, live
preservation, the lookup budget, mutation during a sweep, signed/zero handles,
full-width identity, overwrite, exception cleanup and refill after emptying.
They also cover a pending sweep shrinking to two entries, singleton survivors,
and alternating deletions across an update boundary followed by a native lookup
failure, plus reentrant insertion/replacement/removal/clear and throwing loaders.
A 1,024-entry loader simulation appends another 1,024 entries while checking the
lookup budget and progress without repeated prefix sweeps. These deterministic
callback cases inject the documented loader behavior; they do not measure disk
loading. The managed harness exhausts all live/dead patterns in rings of two
through eight entries, runs 50,000 deterministic mutations, and checks callback
ownership on reload, exit and disposal:

```powershell
tools/tests/fixtures/object_id_cache/RunCacheHarness.ps1 -UnityData <Editor/Data> -SdkPath <dotnet/sdk/version>
```

Its boundary stubs exercise production maintenance, not native Unity identity
resolution. Native measurements are separate. Run the exact opt-in EditMode filter:

```text
MCPForUnityTests.Editor.Tools.ObjectIdCacheCostTests.BenchmarkPairedMaintenanceCost
```

Four cases use 32,768 or 131,072 temporary hidden ScriptableObjects, with alternating
or clustered destruction. Each case warms both strategies, then takes seven
alternating-order paired samples for all-live, half-dead and all-dead states.
`ObjectIdCacheBenchmarkBaseline` freezes the bounded beta algorithm at `e0808a32`;
the candidate is the actual production class. Both use the same real native IDs
and handle keys. Every sample checks exact live counts and the 128-call budget.
Eight additional cycles add/destroy/remove 4,096 objects against retained live
caches. All owned objects are destroyed in `finally`; no persistent assets,
scenes or global preferences are changed.

The older full-snapshot comparator remains available separately:

```text
MCPForUnityTests.Editor.Tools.ObjectIdCacheMaintenanceTests.BenchmarkPairedNativeLargeCache
```

## Measurement definitions and limits

- `total_ms` sums active maintenance-update elapsed times; p95/p99/max describe
  those updates, not whole-Editor frame rates. Tables use medians of seven samples
  unless explicitly labeled worst observed. They are not latency guarantees.
- On Windows, `cpu_cycles` uses the current thread's user/kernel CPU cycles around
  the complete instrumented sweep. It includes the same timing/counter/loop
  bookkeeping for each strategy, but excludes fill, logging and assertions.
  Compare within the paired run; do not convert cycles to milliseconds or compare
  different machines. Other OSes report this counter as unavailable. See
  [Microsoft's counter contract](https://learn.microsoft.com/en-us/windows/win32/api/realtimeapiset/nf-realtimeapiset-querythreadcycletime).
- `backing_payload` measures the actual bucket and entry array lengths and pinned
  element strides. It excludes object/array headers, delegates and native objects;
  it is not an exact total-heap measurement. Pins are released in `finally`.
  The measurement checks known integer/ulong array payloads first. See the
  [pinned-array API](https://learn.microsoft.com/en-us/dotnet/api/system.runtime.interopservices.marshal.unsafeaddrofpinnedarrayelement).
- Allocation events are counted in a separate identical pass after calibrating
  `ManagedAllocation` against 32 known arrays. Thread allocation bytes are also
  calibrated; a zero reading for a known 65,536-byte allocation means unavailable,
  not zero allocations. The legacy comparator's raw whole-Editor heap deltas are
  noisy and are not used as retained-cache memory evidence.

The reference environment is Windows, Unity 6000.0.69f1, with the existing isolated
project and Library/package caches reused. Actual InstanceID-based native lookups
are injected into the version-independent maintenance class. Native Unity 6.6
EntityId lookup and macOS/Linux execution were not performed; version-reference
compilation and full-width behavior checks are separate evidence. No additional
developer installation is required by production or by the Windows counter.

## First follow-up: compact storage and run retirement

The historical measurements in this section compare `dae3f01d` with `e0808a32`.
Their raw CSVs are retained unchanged. To reproduce that comparison, use the
benchmark sources at `e0808a32`; the current benchmark freezes the newer baseline
for the alternating-deletion follow-up below.

The earlier bounded design reduced update p99 to 0.063-0.110 ms but increased
complete-sweep elapsed time by 6-94% relative to a single full-snapshot update in
its original run. Those were elapsed-time measurements, not direct thread CPU
measurements. This follow-up compares against that bounded beta design and
retains its scheduling policy.

The final paired run passed all 11 requested native cases, with no skipped or
failed case. Across all 12 size/pattern/phase cohorts, median instrumented-sweep
CPU cycles decreased **5.5-44.5%**. Median update p99 decreased in every cohort.
The following table includes all phases of the alternating cohort and the
half-dead clustered cohort. The duplicate all-live/all-dead clustered cohorts
and every sample are included in the [paired CSV](benchmarks/object-id-cache-20261024.csv).

| Initial entries | State | CPU cycles, millions: beta / candidate | Sweep elapsed ms: beta / candidate | Update p99 ms: beta / candidate |
| --- | --- | --- | --- | --- |
| 32,768 | All live | 31.98 / 26.92 | 8.729 / 7.396 | 0.049 / 0.042 |
| 32,768 | Half dead, alternating | 53.46 / 46.67 | 14.539 / 12.735 | 0.084 / 0.064 |
| 32,768 | Half dead, clustered | 47.31 / 31.81 | 12.933 / 8.704 | 0.084 / 0.051 |
| 32,768 | All dead | 56.64 / 31.41 | 15.418 / 8.822 | 0.082 / 0.061 |
| 131,072 | All live | 135.84 / 120.55 | 36.892 / 33.062 | 0.050 / 0.045 |
| 131,072 | Half dead, alternating | 206.11 / 194.79 | 55.802 / 52.998 | 0.079 / 0.077 |
| 131,072 | Half dead, clustered | 191.81 / 123.92 | 52.292 / 33.619 | 0.084 / 0.056 |
| 131,072 | All dead | 215.38 / 119.53 | 58.297 / 32.332 | 0.076 / 0.049 |

The worst observed individual update across all paired samples was
0.5041 ms for beta and 0.3305 ms for the candidate.
Some individual cohorts had a higher candidate maximum despite lower p99 and
CPU cycles. Scheduling outliers remain possible; the measured maxima are not
hard guarantees. The structural 128-native-lookup limit is unchanged.

| Initial entries | Beta array payload (bytes) | Candidate array payload (bytes) | Reduction |
| --- | --- | --- | --- |
| 32,768 | 1,308,708 | 1,017,884 | 22.2% |
| 131,072 | 5,631,732 | 4,380,236 | 22.2% |

The measured dictionary entry-array stride shrank from 32 to 24 bytes; bucket
storage is unchanged. Both caches retain their capacity after sweeping, but
the candidate retains the smaller arrays. No capacity growth accumulated over
the eight 4,096-object churn cycles for either cache size; live counts remained
exact. See the [churn CSV](benchmarks/object-id-cache-churn-20261024.csv).

Allocation-event counts remain 0 for all-live and half-dead maintenance and 3
for the final all-dead scheduling cleanup in both strategies. Allocation bytes
were unavailable (calibration returned 0 for a known 65,536-byte allocation);
the independent event recorder observed all 32 calibration allocations.
This change reduces backing memory and sweep work, not every allocation event.

| Initial entries | Cold fill ms: beta / candidate | Re-register same IDs ms: beta / candidate | Live lookup ms: beta / candidate |
| --- | --- | --- | --- |
| 32,768 | 7.709 / 7.818 | 2.674 / 1.344 | 8.147 / 7.802 |
| 131,072 | 37.790 / 36.637 | 10.764 / 5.430 | 33.412 / 33.701 |

These hot-path medians combine the two all-live cohorts (14 samples each).
The optimization is aimed at maintenance, retained storage and repeated ID
registration; it does not establish a universal speedup for cold insertion or
individual lookup. Raw fill samples include occasional allocation/GC outliers.

A preliminary compact-storage/single-read candidate reduced elapsed sweep time
by 8-21% but slowed identical overwrites. The adopted candidate adds the measured
no-op-write fast path and bounded run retirement. Clustered/all-dead workloads
benefit most; alternating single deletions benefit less. No custom hash table,
tombstone queue, entry cap or additional persistent scratch buffer was introduced.

### Comparison with the original single-update snapshot sweep

The separate five-sample 32,768-object comparator also ran in the final native
batch, using the same actual handle keys in both strategies. Its current
elapsed results were:

| Live entries | Snapshot sweep ms | Current bounded sweep ms | Current update p99 ms |
| --- | --- | --- | --- |
| 32,768 | 8.818 | 8.769 | 0.052 |
| 16,384 | 9.417 | 13.718 | 0.089 |
| 0 | 8.386 | 9.202 | 0.051 |

Bounded maintenance can still cost more total elapsed time than one snapshot
sweep, particularly with alternating dead entries. The benefit is preserving
short updates while reducing the previous bounded implementation's CPU and
storage costs. The snapshot allocates two objects per sweep; the candidate
allocates none during nonempty maintenance and three events at final retirement.

## Second follow-up: alternating deletion

This comparison freezes `e0808a32` and measures the production singleton
retirement path against it. Both strategies use the packed storage and the
same bounded scheduling. The isolated Windows Unity project, Library and
package caches were reused. All 38 requested native cases passed with no skips.
The [paired CSV](benchmarks/object-id-cache-alternating-20261024.csv) contains
all 168 measured samples, including the duplicate all-live/all-dead trials.

The adopted split scan reduces alternating-deletion thread CPU cycles by
**14.6% at 32,768 entries and 19.9% at 131,072 entries**, with lower median update
p99 in both cohorts. All-live CPU differences are +0.5% to +1.9%. This is a
targeted improvement: other deletion cohorts range from -0.4% to +6.1% CPU, and
some p99/max observations increase. The unchanged lookup budget preserves bounded
work; it does not guarantee every update will be faster. Array payload and
allocation-event counts are unchanged; one fixed mutation counter is added.

Successor-only reuse and a helper called for every deletion did not provide the
best measured tradeoff. Copying predecessor entries on every live visit also
increased live-scan cost. The selected design keeps live scanning separate and
enters retirement only once per update. Unguarded cached-value reuse was rejected
because asset loading can reenter the cache; its apparent timing benefit is not
an acceptable replacement for the mutation-safe implementation.

| Initial entries | State / destruction trial | CPU cycles, millions: beta / candidate | Sweep elapsed ms: beta / candidate | Update p99 ms: beta / candidate |
| --- | --- | --- | --- | --- |
| 32,768 | All live / alternating | 26.71 / 27.21 | 7.224 / 7.358 | 0.037 / 0.038 |
| 32,768 | Half dead / alternating | 45.46 / 38.83 | 12.302 / 10.508 | 0.057 / 0.046 |
| 32,768 | All dead / alternating | 35.59 / 35.55 | 9.628 / 9.621 | 0.048 / 0.047 |
| 32,768 | All live / clustered | 25.77 / 26.07 | 6.973 / 7.045 | 0.032 / 0.034 |
| 32,768 | Half dead / clustered | 28.14 / 29.70 | 7.606 / 8.034 | 0.036 / 0.043 |
| 32,768 | All dead / clustered | 30.48 / 32.34 | 8.253 / 8.745 | 0.045 / 0.042 |
| 131,072 | All live / alternating | 110.60 / 111.92 | 29.912 / 30.273 | 0.041 / 0.040 |
| 131,072 | Half dead / alternating | 208.69 / 167.22 | 56.500 / 45.251 | 0.075 / 0.067 |
| 131,072 | All dead / alternating | 124.29 / 130.74 | 33.683 / 35.384 | 0.047 / 0.052 |
| 131,072 | All live / clustered | 110.08 / 110.60 | 29.772 / 29.912 | 0.040 / 0.041 |
| 131,072 | Half dead / clustered | 127.52 / 127.05 | 34.491 / 34.363 | 0.057 / 0.056 |
| 131,072 | All dead / clustered | 121.80 / 126.17 | 32.945 / 34.134 | 0.044 / 0.048 |

Median p99 and worst observed updates vary between cohorts. The worst observed
individual update was 0.3095 ms for beta and
0.4432 ms for the candidate. These are observations, not hard latency guarantees;
the 128-native-lookup limit remains unchanged.

Both implementations retain exactly the same measured array payload:
1,017,884 bytes at 32,768 entries and 4,380,236 bytes at 131,072 entries.
This preserves the earlier 22.2% reduction. Eight 4,096-object churn cycles
did not accumulate capacity or lose live entries; see the
[churn CSV](benchmarks/object-id-cache-alternating-churn-20261024.csv).
Allocation events remain zero for live/half-dead maintenance and three at
final empty-cache cleanup. The event recorder calibration succeeded; the
thread byte counter remains unavailable. No heap scratch buffer was added.

| Initial entries | Cold fill ms: beta / candidate | Re-register same IDs ms: beta / candidate | Live lookup ms: beta / candidate |
| --- | --- | --- | --- |
| 32,768 | 8.615 / 7.966 | 1.389 / 1.493 | 7.594 / 7.709 |
| 131,072 | 36.287 / 37.939 | 5.525 / 5.934 | 33.467 / 31.817 |

Hot-path medians combine the two all-live trials (14 samples per strategy).
Registration and public lookup now track reentrant mutations; identical-ID
registration still skips the write. These timings include the safety bookkeeping
and do not establish a separate hot-path optimization.

### Original snapshot comparison in the same run

The unchanged original snapshot comparator also ran with five paired samples
per phase. Its [raw CSV](benchmarks/object-id-cache-alternating-snapshot-20261024.csv)
uses the same actual native handle keys for both strategies.

| Live entries | Snapshot sweep ms | Current bounded sweep ms | Current update p99 ms |
| --- | --- | --- | --- |
| 32,768 | 8.699 | 8.703 | 0.048 |
| 16,384 | 9.541 | 12.350 | 0.062 |
| 0 | 8.475 | 9.433 | 0.049 |

The original snapshot allocates two objects per sweep and performs all native
resolutions in one update. The bounded design retains its short updates; this
comparison does not make the snapshot an acceptable latency replacement.
Native Unity 6.6 and other-OS timing remain unmeasured. No live-entry cap,
deadline or maintenance interval was tightened.

In this run, alternating deletion takes 12.350 ms versus the snapshot's 9.541 ms,
or **29.4% more elapsed time**. The previous follow-up observed 45.7% overhead in
a separate run. The direct paired comparison against frozen `e0808a32` above is
the evidence for the new implementation's CPU gain; comparisons between runs
alone are not used to establish it. Current alternating update p99 is 0.0625 ms
in the snapshot-comparison fixture, while the snapshot does all work in one
9.541 ms update.
