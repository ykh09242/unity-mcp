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
The managed harness also runs 50,000 deterministic mutations and checks callback
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
`ObjectIdCacheBenchmarkBaseline` freezes the bounded beta algorithm at `dae3f01d`;
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

## Measured follow-up

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
