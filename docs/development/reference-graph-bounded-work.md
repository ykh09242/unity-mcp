# Reference graph bounded-work measurements

These Windows measurements use synthetic private SQLite scopes and synthetic JSON
files. No configured source, project graph, or Unity instance is read. Timings are
single samples and are not cross-platform throughput guarantees. Python traced peak
excludes SQLite native memory and filesystem caches.

## Metadata and final cleanup

Before this change, 1k/4k/16k prior rows used 0.71/2.82/11.28 MB of Python traced
peak; final deletion used 0.27/0.97/3.79 MB. The new implementation processes prior
metadata and final-deletion owner lists in pages of at most 256 rows.

| Synthetic prior rows | Metadata peak | Cleanup peak | Final cleanup transaction |
| --- | ---: | ---: | ---: |
| 1,000 | 375,878 B | 142,034 B | 0.071 s |
| 10,000 | 376,307 B | 151,738 B | 0.458 s |
| 100,000 | 376,692 B | 151,743 B | 5.267 s |

Final cleanup remains one atomic transaction with linear total work. Normal
reconciliation checks cancellation between 256-owner pages; guarded producers also
check within SQLite work. Interruption rolls back all removals. Regression fixtures
compare persisted edge, observation, and metadata
digests after cancellation, reopening, and completion against uninterrupted cleanup.
They also retain authored edges owned by removed files and incoming manual references
whose target becomes absent.

Actual unchanged full reconciliation of 128/512/2,048 synthetic JSON files took
0.624/1.501/4.503 seconds after initial ingestion. Metadata pages held at most
128/256/256 rows. Reopened immutable evidence digests matched at every size. Total
Python peak was 0.30/0.80/1.47 MB, so these measurements establish bounded metadata
and deletion lists, not constant total scanner memory. Source traversal, rule state,
and other collection work retain their existing lifetime and budgets. Actual 100k
file ingestion was not run.

## Filtered queries and cancellation

With `limit=1`, 1k/10k/100k excluded manual edges plus a final valid edge required
3,500 SQLite VM instructions for the new first page at every size (about 1.7-1.9 ms).
The original filter-before-limit seam on the identical rows required
26k/260k/2.6 million instructions. That comparison projects only candidate IDs for
the original seam, rather than claiming an end-to-end old-response timing.

The first page is empty with a continuation. The terminal boundary still returns
the same valid edge and replays identically. Smaller regression fixtures exhaust
all intermediate query, impact, and snapshot pages and verify latest-page replay.
Single-row pages intentionally trade more continuations for bounded request work.

On identical synthetic million-row recursive SQL, physical cancellation drain took
0.220 seconds without the cooperative signal and 0.00104 seconds with it. This is
a component cancellation measurement; it excludes native calls and SQLite lock
acquisition. Tests additionally verify writer rollback, storage-meter composition,
nested read-snapshot guard restoration, and physical thread completion before
returning cancellation.

## Reproduction

Use the existing Server development environment from the repository root:

```sh
Server/.venv/Scripts/python.exe tools/experiments/reference_graph_bounded_work.py --output /owned/output/results.json --counts 1000,10000,100000 --query-limit 1
Server/.venv/Scripts/python.exe tools/experiments/reference_graph_bounded_work.py --output /owned/output/full-sweeps.json --counts 1 --sweep-counts 128,512,2048
```

The output parent must exist. The helper writes only the explicit JSON destination;
each temporary synthetic scope is removed after its measurement. Counts are
configurable experiment inputs, not production limits. The metadata measurement
also works with a pre-change checkout of the service by falling back to its
all-row API; use the same interpreter and fixture helper for comparisons.
