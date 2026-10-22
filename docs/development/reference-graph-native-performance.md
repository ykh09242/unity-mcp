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

The last candidate is faster than replaying history but is **not equivalent**:
publishing a fresh single inventory token invalidates an existing reader. The
measurement test demonstrates that failure and restores the original checkpoint.
A versioned, reader-preserving inventory could be investigated separately; these
numbers do not establish its memory cost or benefit.

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

## Reproduction

Use a closed, disposable Unity project with the package and EditMode test sources
already staged. Prepare the source files before starting Unity so compilation
does not overlap measurement:

```sh
python tools/experiments/reference_graph_native_fixtures.py --isolated-project /absolute/isolated/project
```

The helper refuses existing fixture paths and linked project/Assets ancestors. It
creates only `Assets/__McpReferenceMeasureFixtures_e11` and its metadata. Keep the
same fixtures and Library for both variants. Run the two explicit tests in
`ReferenceGraphNativeMeasurementTests` through the existing controller-owned
native QA runner with `UNITY_MCP_NATIVE_QA_NONCE` set to its ownership nonce.
Extract the `REFERENCE_NATIVE_MEASUREMENT` JSON records from test output. For a
baseline comparison use the same benchmark source and fixture bytes, replacing
only the two production files with the baseline revision.

`ReferenceGraphReloadTests.ActualReloadRetainsJournalAndEditorIdentity` separately
exercises a real domain reload. Tests restore altered source bytes and saved
journal state. Remove only the owned measurement fixture folder and its metadata
when all comparisons finish; retain the reusable project and Library.
