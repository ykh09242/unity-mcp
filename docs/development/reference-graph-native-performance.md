# Native reference graph measurements

The source collector now reuses a cleared 64 KiB byte buffer, avoids unrelated
asset catalog descriptions for source reads, and obtains per-source compilation
metadata only on the first page. Every page still checks the file fingerprint,
physical path, source format and compilation revision. Journal persistence writes
the existing change array directly through `JsonTextWriter`, avoiding a deep copy.
No job, source size, history or response limits were reduced.

## Method and results

Measurements used Windows, Unity 6000.0.69f1 and Addressables 2.7.6 in one isolated
project with its existing Library. The baseline was commit
`97a5ef078b8bad80bc20e93bbe603b5ccfa2fb2a`. Each variant ran twice, four samples per
scenario per run, in baseline/candidate/candidate/baseline order. The final control
replaced only the collector and journal with their immutable baseline sources.
All samples, including first-sample initialization costs, are retained in the
[machine-readable results](reference-graph-native-performance-results.json).

| Scenario | Baseline median | Candidate median | Decision |
| --- | ---: | ---: | --- |
| Complete C# source, 8 MiB, 129 pages | 680.33 ms | 614.98 ms | Adopt; 9.6% faster |
| Complete Unity YAML, 8 MiB, 129 pages | 656.05 ms | 612.86 ms | Adopt; 6.6% faster |
| Save 65,000 journal entries | 675.80 ms | 174.25 ms | Adopt; 74.2% faster |
| Restore 65,000 entries | 938.67 ms | 959.16 ms | Unchanged implementation; no improvement claim |
| Cached census plus 50,000 later history rows | 345.95 ms | 344.70 ms | Unchanged implementation |
| Fresh enumeration/sort of 12,244 GUIDs | 21.99 ms | 22.43 ms | Do not adopt as checkpoint replacement |

The last row measures enumeration and sorting only. With a single cached version,
publishing the fresh census invalidates an earlier reader. The history measurement
uses an explicit snapshot to resume the original checkpoint.

Source output SHA-256, byte count, all 129 pages and mutation rejection were
checked for every variant. Serialized journal JSON is byte-identical. Collector
regressions passed alongside the candidate, and a separate actual domain reload
preserved the 65,000-entry journal and accepted the following imported change.

The runtime allocation counter failed its calibration, so `allocated_bytes` is
null. Managed heap deltas and collection counts are observational values affected
by GC, not total allocation measurements. Timings include the test harness's
request construction, base64 decoding and digest processing. They describe this
warm project, not a cross-OS throughput guarantee or a whole-project indexing
benchmark. The corpus uses synthetic large sources and synthetic history; it does
not claim 65,000 real assets were imported.

## Interleaved inventory readers

A tokenless request after an import starts a census at the new journal revision.
The collector keeps the current census and up to one earlier census in the same
epoch. Reader A can finish paging its original sorted GUIDs after B starts. Its
response carries A's original token, epoch and revision. Each page describes the
assets that still exist when it is read.

The current census has no GUID limit. An earlier version may retain up to 65,536
GUIDs. Publishing a third live version evicts the oldest, and each version expires
after 600 idle seconds. A journal epoch change invalidates both. An evicted token
receives `reference_inventory_snapshot_invalid` and its reader can restart.

The standalone comparison compiled the production cache in PowerShell 7.6.9 on
.NET 10.0.14 x64 with 12,244 separately allocated 32-character GUID strings per
version. Both variants generated and sorted the same two censuses. After warming
both, the test ran eight rounds in ABBA/BAAB order, changing only the number of
GUIDs permitted in the prior version.

| Metric | One version | Up to two versions |
| --- | ---: | ---: |
| A restarts after B publishes | 4 of 4 | 0 of 4 |
| A first census, median | 2.52 ms | 2.55 ms |
| B first census, median | 2.57 ms | 2.77 ms |
| Peak retained GUID slots | 12,244 | 24,488 |
| Peak sampled live-heap delta, median | 1,175,536 bytes | 2,347,880 bytes |

The live-heap samples use full collections after publication and include strings
and array storage. They omit transient sorting and collection peaks. The standalone
times cover GUID generation, sorting and publication. Editor first-page timing
also includes AssetDatabase enumeration, sliced preparation and row description.

Reproduce the standalone run from the repository root in a fresh PowerShell process:

```powershell
$policy = Get-Content -Raw MCPForUnity/Editor/Tools/ReferenceGraphInventories.cs
$fixture = Get-Content -Raw tools/experiments/reference_graph_inventory_retention.cs
Add-Type -TypeDefinition ($fixture + [Environment]::NewLine + $policy)
[ReferenceGraphInventoryRetentionExperiment]::Run()
```

For the native comparison, run the explicit EditMode test
`ReferenceGraphNativeMeasurementTests.InterleavedInventoryVersionsMeasureRestartsLatencyAndRetention`
in the controller-owned disposable project. It writes and synchronously reimports
one fixture before B, checks both readers' journal revisions, and emits eight
`REFERENCE_NATIVE_MEASUREMENT` JSON records. GUID-slot counts measure the arrays
held by the collector; multiplying by pointer size gives array-reference storage
excluding headers, GUID strings, and temporary preparation buffers.

In Unity 6000.0.69f1 on Windows, this EditMode test passed with 2,455 GUIDs in
each census. All eight rounds from one Editor run are included. The two policies
used the same project, Library, import operation, and page size of one. The test
started each round with an empty inventory cache and alternated policies in
ABBA/BAAB order. Each time below is the median of four samples per policy.

| Native metric | One version | Up to two versions |
| --- | ---: | ---: |
| A restarts after B publishes | 4 of 4 | 0 of 4 |
| A first page | 59.29 ms | 59.16 ms |
| B first page after import | 31.55 ms | 34.18 ms |
| A continuation, including any restart | 7.72 ms | 3.26 ms |
| Peak retained GUID slots | 2,455 | 4,910 |
| Peak retained array-reference bytes | 19,640 | 39,280 |

The B timer starts after the synchronous import returns. When A restarts, the
timer includes the rejected continuation, reopening B's current census, and
reading the next page. The retained version avoided that restart in all four
rounds and added 19,640 bytes of array-reference storage for this project.

## Reproduction

Use a closed, disposable Unity project with the package and EditMode test sources
already staged. Prepare the source files before starting Unity so compilation
does not overlap measurement:

```sh
python tools/experiments/reference_graph_native_fixtures.py --isolated-project /absolute/isolated/project
```

The helper refuses existing fixture paths and linked project/Assets ancestors. It
creates only `Assets/__McpReferenceMeasureFixtures_e11` and its metadata. Keep the
same fixtures and Library for both variants. Run the explicit source and history tests in
`ReferenceGraphNativeMeasurementTests` through the existing controller-owned
native QA runner with `UNITY_MCP_NATIVE_QA_NONCE` set to its ownership nonce.
Extract the `REFERENCE_NATIVE_MEASUREMENT` JSON records from test output. For a
baseline comparison use the same benchmark source and fixture bytes, replacing
only the two production files with the baseline revision.

`ReferenceGraphReloadTests.ActualReloadRetainsJournalAndEditorIdentity` separately
exercises a real domain reload. Tests restore altered source bytes and saved
journal state. Remove only the owned measurement fixture folder and its metadata
when all comparisons finish; retain the reusable project and Library.
