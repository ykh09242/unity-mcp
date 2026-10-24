# Editor object ID cache maintenance

Unity 6.6 reverse object lookup uses the full native EntityId cached when an
integer handle is returned. A managed wrapper being collected does not establish
that the native object has died. Maintenance therefore resolves the native ID
before removing an entry, and never evicts a live handle to enforce a size cap.

Maintenance starts ten seconds after the first entry, then visits at most 128
entries per Editor update. The next interval starts when a sweep completes.
Play mode transitions request a sweep through the same bounded update path.
Resolving an individual dead handle removes it immediately. Assembly reload,
Editor exit and disposal clear owned state and callbacks.

The cache stores the full ID and circular previous/next handle links in each
dictionary value. Additions, overwrites and removals preserve the sweep cursor.
Entries added during a sweep participate by the next sweep at the latest.
Large caches take more updates to maintain; expiry is eventual and depends on
Editor updates continuing. Dictionary capacity is reused after removal/clear,
as before; removing native-dead entries is not a promise of immediately returning
all managed dictionary capacity to the operating system.

The integer wire handle remains the truncated EntityId on versions that use it.
Different 64-bit values could theoretically have the same lower 32 bits. This
maintenance change neither reproduces nor resolves that pre-existing possibility;
it does not change public handles or invent a new identity format.

## Verification

`ObjectIdLifetimeTests` and `ObjectIdCacheMaintenanceTests` cover live-handle
retention, dead-handle expiry, the update lookup budget, additions/removals and
overwrite/clear behavior. The managed harness additionally exercises reload,
exit and disposal callback ownership using explicit Unity boundary stubs:

```powershell
tools/tests/fixtures/object_id_cache/RunCacheHarness.ps1 -UnityData <Editor/Data> -SdkPath <dotnet/sdk/version>
```

The stubs exercise the production maintenance code; they do not establish
native Unity lookup behavior. Native validation is separate.

Run the opt-in native benchmark using the exact EditMode test filter
`MCPForUnityTests.Editor.Tools.ObjectIdCacheMaintenanceTests.BenchmarkPairedNativeLargeCache`.
It creates 32,768 temporary hidden ScriptableObjects, measures the old complete
key-snapshot sweep and production maintenance against the same actual native
IDs, and destroys every owned object in `finally`. It creates no persistent
assets and does not edit scenes or global preferences.

Five samples alternate strategy order for each of three phases: all live,
half destroyed, and all destroyed. Each log row records active maintenance tick
p95/p99/max latency, total sweep time, update count, calibrated thread allocation
bytes, allocation event counts, raw managed heap deltas, and live/dead entry counts. The baseline has one
active tick per sweep; its p95/p99 therefore describe that single full-sweep
tick. The candidate spreads a sweep over 256 ticks. At 60 Editor updates per second,
that adds roughly 4.3 seconds to completing a sweep after its ten-second idle
interval; this is an illustration, not a guaranteed Editor update rate. These are main-thread
maintenance timings, not whole-Editor frame-rate or rendering measurements.
The byte counter is calibrated against a known allocation before use; Unity Mono
can return zero for known allocations. An unavailable counter is reported as
`unavailable`, never as measured zero. A separate identical maintenance pass uses
the existing `ManagedAllocation` recorder to count GC.Alloc events after calibration
against 32 known array allocations. These are allocation event counts, not bytes
derived from profiler durations.

Raw heap deltas use `GC.GetTotalMemory(true)` and include unrelated Editor activity;
they are not cache-retained-memory measurements. Each cached entry now stores two
additional integer links, increasing per-entry retention. Native live/dead object
counts are checked independently from these noisy managed heap values.

The native reference run used Windows and Unity 6000.0.69f1, injecting actual
InstanceID-based native lookups into the version-independent cache. Production
full EntityId caching activates on Unity 6000.6 and later; that version's native
EntityId boundary was not physically executed in this run. Reference compilation
for other versions is separate evidence and does not establish native timing.

## Reference measurements

The Windows reference run passed all four cache tests, including the existing
lifetime regression and the calibrated paired benchmark. Median results from
five samples per phase were:

| Live entries after sweep | Baseline full-sweep tick (ms) | Candidate tick p99 (ms) | Candidate complete sweep (ms) | Allocation events: baseline / candidate |
| --- | --- | --- | --- | --- |
| 32,768 | 9.725 | 0.063 | 10.309 | 2 / 0 |
| 16,384 | 10.460 | 0.110 | 15.789 | 2 / 0 |
| 0 | 8.548 | 0.099 | 16.604 | 2 / 3 |

Every strategy began with 32,768 handles and retained exactly the live entries.
The baseline models key-snapshot/native-resolution/removal maintenance; the
candidate also performs its owned final-entry scheduling cleanup. Its all-dead
allocation events include that cleanup. Allocation bytes were unavailable: the
runtime counter returned zero for a known 65,536-byte allocation. The independent
event recorder correctly observed all 32 calibration allocations.

The adopted benefit is substantially lower maintenance work in each Editor
update. Complete-sweep CPU increased by approximately 6%, 51% and 94% in these
phases. The additional per-entry links and later dead-entry expiry are explicit
costs. These results do not establish lower total CPU, exact retained heap bytes,
or allocation-free final cleanup.
