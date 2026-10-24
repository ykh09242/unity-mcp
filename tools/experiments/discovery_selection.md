# Discovery selection measurements

Run `discovery_selection.py` with the existing Server Python environment and
`PYTHONPATH=Server/src`. It asserts that the imported discovery module belongs to
the same checkout and that the reviewed SDK is installed. Capture JSON separately
for the baseline and candidate using the same benchmark source in each checkout.
The baseline for the figures below is commit `1891addb`.

The fixture uses FastMCP 4.1.3, the production built-in registry, SessionStateStore
serialization, Visibility transforms, PluginRegistry snapshots and the production
Unity availability filter. Full inventories contain 122 tools, including two
benchmark helpers; the large fixture adds 2,000 custom descriptors. No Unity or
network connection is created. Routing identities are synthetic server-side test
inputs; real HTTP principal authentication and isolation have separate wire tests.

Each operation warms five batches, times 31 without tracing, then observes five
with tracemalloc. The update batch contains 200 no-op modern selections. Concurrent
listing batches exercise three owner/project combinations. Assertions cover exact
descriptor digests, scope separation, catalog revision/removal/restoration, idle
expiry and shutdown cleanup. Trace peaks measure transient high-water bytes,
not cumulative allocated bytes or process RSS; retained observations follow GC.

## Adopted change

Unchanged modern selections now share the existing read/legacy hourly renewal
policy. Changes write immediately, hourly renewal still uses TTL 86,400 seconds,
and reset still removes the record. No authorization or catalog cache was added.

Windows / Python 3.14.6 observations:

| Operation | Baseline p50 / p95 | Candidate p50 / p95 | Observation |
|---|---|---|---|
| No-op update, full registry | 85.75 / 138.03 us | 67.41 / 78.87 us | 8,200 redundant writes become zero |
| No-op update, 2,000 custom tools | 84.92 / 113.25 us | 62.44 / 93.57 us | Same write reduction |
| Empty tools/list | 0.55 / 1.15 ms | 0.81 / 1.15 ms | Control; no listing change |
| Full tools/list | 58.22 / 135.06 ms | 81.91 / 183.29 ms | Control; no listing change |
| Large tools/list | 332.84 / 555.22 ms | 409.61 / 542.93 ms | Control; no listing change |
| Project listing, full registry | 1.70 / 2.32 ms | 2.33 / 3.23 ms | Current availability filter retained |
| Concurrent listings, full registry | 5.96 / 7.66 ms | 6.96 / 8.11 ms | Owner/project parity retained |
| Project listing, large catalog | 9.14 / 9.94 ms | 11.81 / 15.33 ms | Current availability filter retained |
| Concurrent listings, large catalog | 25.53 / 32.24 ms | 32.54 / 44.08 ms | Owner/project parity retained |

Separate-run listing controls show scheduling noise; no listing improvement is
claimed or adopted. A further same-process comparison alternated baseline and
candidate updates in 40 pairs of 200 operations, reversing order every pair.
This isolated the changed method in an actual SDK request context: p50 **89.98 to
38.01 us**, p95 **121.99 to 56.94 us**. Six separately traced alternating pairs
reduced peak temporary traced bytes **27,987 to 19,394**, and bytes remaining
after GC **3,703 to 212**. These are local method measurements, not HTTP latency.

In the full/large catalog runs, update-batch traced peaks fell from
100,452/100,889 to 93,725/93,662 bytes. Selection storage remained one record
(326 bytes in these runs) in both arms; the change does not claim a reduced
durable selection footprint or fix a memory leak. Identical full descriptor and
scoped-name digests plus revision/revocation/expiry assertions passed in both arms.
