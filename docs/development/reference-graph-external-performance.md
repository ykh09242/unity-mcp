# External reference verification and scan costs

Measured on Windows with Python 3.14.6 against the previous beta analysis unit and
the provider-freshness implementation. Both use the existing stdlib SQLite driver,
WAL and `synchronous=FULL`. These are component measurements with owned synthetic
JSON files, not Unity end-to-end latency or a cross-platform timing guarantee.

Each workload contains one mapped Resources reference per file. After initial
ingestion, three unchanged scans and three query validations are measured. A query
validation includes both authorization/file-access fences. All target IDs, edge
counts and measured edge digests agree. No file access or operator check is cached
as a grant, and every returned external file is still stamped at both fences.

| Files / catalog assets | Unchanged scan, median before / after | Query validation, median before / after | Idle write transactions before / after | Query read connections before / after |
| --- | --- | --- | --- | --- |
| 100 / 2,500 | 0.811 / 0.124 s | 0.740 / 0.152 s | 102 / 5 | 204 / 4 |
| 1,000 / 25,000 | 8.575 / 0.811 s | 7.337 / 1.360 s | 1,002 / 8 | 2,004 / 4 |

The adopted changes batch unchanged seen-file bookkeeping in 256-row transactions, reuse a
catalog only at the same scoped catalog revision, and join query metadata once
per validation fence. A scan reuses one scoped read handle for grant-version
checks. Each check finishes its statement in autocommit mode, so another writer's
revocation is visible to the next file; it does not retain a read snapshot or a
permission result. Cancellation and exceptions close the handle. File identity,
credential and operator checks remain active. See SQLite's [transaction
semantics](https://sqlite.org/lang_transaction.html) and Python's [transaction
control](https://docs.python.org/3/library/sqlite3.html#transaction-control).

An intermediate candidate with batching alone reduced writes but had inconsistent
small-workload timings. It was not used as the final performance result. Scoped
handle reuse gave the repeatable improvement above. Read-connection counts are not
SQL-statement counts: source versions are still queried before every file.
New or changed files atomically persist their observation and tracking row before
publication, so interrupted enumeration cannot orphan their evidence. The table
includes this recovery fix. Checked queries additionally reauthorize every enabled
provider before delivery, including providers with no rows in the result; this
provider-wide fence is outside the component query-validation timings above.

Reproduce from an installed development environment:

```sh
python tools/experiments/reference_graph_external.py /absolute/path/to/unity-mcp /absolute/path/to/owned-output/results.json
```

Use the same helper and Python environment with each revision in separate
checkouts. The output directory must exist. The helper retains its synthetic
fixture directories for inspection and never reads a configured external source.
The workload sizes are experiment inputs, not production limits. No maximum
project size, scan duration, file count or total memory cap was introduced.

## Bounded sweeps and watch diagnostics

Full sweeps now resolve prior metadata for at most 256 enumerated paths at a time,
then keyset-page unseen paths. Final deletion also reads at most 256 owners at a
time inside one transaction: cancellation rolls back the entire final cleanup.
Already completed file publications survive a later enumeration failure. The
[bounded-work experiment](reference-graph-bounded-work.md) distinguishes metadata
memory from total scan memory and reports the remaining linear transaction cost.

External provider status includes `watch.active`, a bounded `watch.reason`, cumulative
`full_scans`, `delta_scans`, `fallback_full_scans`, `processed_files`, and the latest
`last_processed_files`, `last_full_scan_at`, and `last_delta_scan_at`. Scan counters
describe completed enumeration attempts, including attempts with per-file failures;
processed counts describe admitted input files visited, not directory entries or
removed files. An idle delta can process zero files. Interrupted enumeration retains
the previous completed totals and timestamps, including across restart.

Live watcher state is refreshed for delivery. Reasons include `active`,
`not_registered`, `provider_limit`, `directory_limit`, `population_limit`,
`notification_gap`, `watch_backend_failed`, `input_signature_changed`,
`directory_identity_changed`, `rule_evidence_changed`, and
`rule_evidence_unavailable`; a completed explicit full scan also records
`explicit_full_scan` in its durable scan diagnostic. These fields contain no event
names, excluded filenames, source roots, or backend exception messages. Fallback
continues to perform complete sweeps and never establishes an incremental checkpoint
from incomplete notification evidence.
