# Service optimization decisions

The changes below reduce repeated work while preserving supported workload sizes,
whole-request deadlines, mutation order, cancellation and complete final evidence.
Measurements are local Windows evidence, not universal speedup promises. See the
[reproducible probes and raw samples](../../tools/experiments/service_optimizations/README.md).

## Implemented behavior

| Item | Change | Compatibility and lifetime |
| --- | --- | --- |
| OPT01 | Suite coordination reads small running/finalizing observations and obtains a full report at finalization. | Public status still returns complete evidence; terminal success remains hidden until cleanup and persistence finish. |
| OPT02 | Immutable scenario definitions are separated from mutable recovery checkpoints; unchanged state is not serialized again. | Two slots preserve the previous input identity if replacement fails. Legacy checkpoints remain readable; global JSON settings cannot alter recovery input. |
| OPT03 | Scenario target IDs use Unity's native component-type lookup, filtered to the exact loaded scene. | Inactive and hidden duplicates remain ambiguous. No managed cache retains live scene objects. Hierarchy paths keep their existing behavior. |
| OPT04 | Optional captured reference pages read owned temporary evidence without rescanning a changed scene. | Live scans remain available. Captured freshness, scope and expiration are explicit. Storage failure preserves the successful live first page. |
| OPT05 | Large artifact validation/copy preparation runs on owned background workers, followed by guarded main-thread publication/import. | Existing limits and no-overwrite/path/identity checks remain. Native import and final saved-file integrity checks still run synchronously. |
| OPT06 | Command batches yield at completed child/command boundaries after approximately four milliseconds of work. | Running Unity operations are not interrupted; order, cancellation and whole-request deadlines are preserved. This is a scheduling slice, not a shorter command timeout. |
| OPT07 | Hierarchy scratch releases object references immediately but retains large backing arrays across bursts. | Idle owners trim after 30 seconds and explicit release drops capacity. Twenty queries over 5,000 roots changed from 300 allocation events to zero. |
| OPT10 | Concurrent state/test-result readers share an immutable internal container tree and receive detached mutable containers. | Enabled at 16 concurrent waiters. Single/sparse reads, custom models, alias graphs and unsupported objects keep deepcopy. Temporary admission failure falls back; independent ownership and credential checks remain. |
| OPT11 | The publication stage carries a digest already computed while downloading exclusively owned bytes. | Download readers cannot mutate those bytes or close the owner. Mutable buffers and external/final files still require hashing. No new content cache is installed. |
| OPT12 | A fixed two-worker preparation queue prefers shorter pending files until the oldest waiter has aged two seconds. | Aging then gives the oldest work priority. No preemption, smaller size limit, new timeout, adaptive rejection or starvation policy is introduced. |

The two-second scheduling age does not promise completion within two seconds:
already-running large operations continue to completion. The 256 KiB staging
threshold selects a background path; it is not an admission limit.

## Captured reference pages

Use `scene_reference_diagnostics` with `action="validate_references"`, the desired
scope and budgets, and `reference_snapshot=true`. The first response includes
`data.referenceCursor` and `freshness="captured"`. For subsequent pages, pass that
cursor as `reference_cursor`, keep the same scope/path/budgets, and change
`page_number`. Omit `reference_snapshot` on cursor reads. Do not combine a cursor
with `reference_revision`: the latter validates a live observation.

Snapshots contain value evidence, not live Unity objects. Up to 64 recent snapshots
and 8 GiB of temporary evidence are retained for ten minutes of inactivity. Reload,
quit, expiration and eviction close their owned DeleteOnClose handles. An expired
cursor requires a new capture; these retention limits do not lower scan budgets.

Asset reference reads and move/delete previews also accept `reference_snapshot`
and `reference_cursor`. Keep the source, operation, destination, scan root,
recursive option and budgets unchanged. Responses include `nextPageNumber` and
`snapshotExpiresAtUtc`; each successful cursor read renews the idle retention.
The first scan writes ordered scalar rows once, so subsequent pages do not
rehash the source or enumerate the reference graph. Imported asset changes,
deletions and moves invalidate asset captures; existing captured scene evidence
keeps its frozen-observation behavior. Unimported changes are not claimed as
current graph evidence. A cursor cannot replace `reference_revision` on a live
recheck or guarded mutation. Storage failure falls back to the complete live
page, and cancellation before publication closes the temporary snapshot.

## Asynchronous asset reference hashes

On Windows, dispatched asset reference reads and move/delete previews hash the
complete source and metadata on at most two owned file workers. The Editor
continues updating while they run. Read leases remain open through the final
Editor-thread identity/revision check and graph observation. Cancellation, quit
and assembly reload settle actual file work before releasing its handles.
`AssetDatabase` queries remain on the Editor thread. Guarded mutations still
perform their synchronous final revalidation immediately before the write.
Direct synchronous callers and other Editor platforms retain the existing path;
file sharing outside Windows needs separate native verification before enabling
this asynchronous handoff. The existing 8 GiB source and 16 MiB metadata budgets
are unchanged.

In four alternating complete-file comparisons on Windows Unity 6000.0.69f1,
17/64/128 MiB requests produced identical results and strong revisions. Median
maximum Editor update intervals changed from 897/3374/6680 ms to 4.2/3.5/4.1 ms;
median asynchronous dispatch was approximately 1 ms. Total completion time stayed
within about 1.4% of the synchronous path. These are observed Editor callback
intervals under the isolated test runner, not rendered-frame guarantees.

## Measured choices

Unity 6000.0.69f1, Direct3D11 / GTX 1660 SUPER, Python 3.14, existing warm Library
and Python environment. These are medians unless another statistic is named.

| Experiment | Before | Candidate | Decision |
| --- | --- | --- | --- |
| OPT08, eight 2048-square captures including PNG encoding | 810.7 ms | 809.2 ms | Do not adopt: throughput difference is negligible; the two-slot design requires more retained pixel storage. |
| OPT09, 4,000-row `JToken` response, main-thread preparation | 13.8 ms | 37.9 ms | Do not adopt the general prepared representation: total serialization improves, but common-token main-thread work becomes about 2.7 times longer. |
| OPT10, 16 ASCII readers, actual shared-read path | 188.0 ms | 137.4 ms | Adopt concurrent immutable trees; approximately 27% less elapsed time. |
| OPT10, 16 Unicode readers | 178.1 ms | 121.5 ms | Approximately 32% less elapsed time; traced peak increases from 18.59 MB to 19.22 MB (about 3.4%). |
| OPT11, 16 MiB GLB with small metadata, validation versus ideal content-cache hash hit | 0.21 ms | 824 ms | Reject a new external content cache; keying costs more than the existing metadata-only validation. |
| OPT11, actual 16 MiB owned-download preparation | 900.7 ms | 50.0 ms | Reuse the existing digest; this is a preparation-stage measurement, not whole download/import latency. |
| OPT12, mixed burst small-job median completion | 2,781 ms | 1,849 ms | Adopt bounded size/age scheduling with the existing two workers. |
| OPT12, mixed burst small-job observed p95 completion | 3,741 ms | 1,896 ms | All large jobs also finish; large-job median is 2,707 versus 2,717 ms and total burst time is 3,702 ms for both. |

OPT09 reduces allocation events and total serialization time for object responses,
so a narrower future design may be useful. It is not enabled without a solution to
the main-thread tradeoff. The rejected encoded-JSON fan-out candidate for OPT10
also improved concurrent CPU time, but increased traced peak memory by more than
two times and slowed single readers. Adaptive worker counts remain disabled: the
measured useful change is queue ordering, not evidence for a feedback controller.

## Verification scope

Causal regressions cover complete finalization, unchanged checkpoints, inactive
target duplicates, large hierarchy bursts, stable captured pages, cooperative
command ordering, late worker/cancellation cleanup, publication ownership, readonly
download digests, queue aging and independent mutable response consumers. Grouped
native runs reuse the isolated project and Library. Windows native execution does
not establish Linux/macOS Player behavior. Compile/CI evidence and native evidence
are reported separately in the delivery PR.

## Reverse-reference inventory limits

`max_assets` accepts 1-250,000 (default 1,024). Reverse scans count distinct
eligible candidate files, excluding folders and duplicate paths; forward scans
count visited assets including the source. Responses report `maximumMaxAssets`,
`maxAssetsAppliesTo` and the reverse `inventoryGuidCount`. A refused reverse
inventory explains the requested and supported limits instead of silently
truncating its candidates. Increase the explicit budget or narrow `scan_root`.

Unity `FindAssets` returns a complete recursive GUID array in one indivisible
native call. The former 65,536-GUID check ran after this allocation and disagreed
with the advertised file budget. Partitioning a parent plus its children repeats
recursive work; it is not a streaming native enumeration. Cancellation is checked
during candidate conversion and node/edge processing. Native calls themselves
remain indivisible. A captured observation provides complete result continuation
without repeating inventory; it does not bypass scan budgets. Packages and
built-in references retain the documented exclusion semantics.

A Windows Unity 6000.0.69f1 explicit native fixture completed 65,537 candidate
files and the exact 100-reference union across 15 pages. Native enumeration took
467 ms; the complete admitted scan plus page requests took 57.15 s. Initial fixture
creation/import took 720.92 s and is excluded from query timing. This demonstrates
capacity and complete output, not a claim that a full large scan is inexpensive
or that its Unity API traversal is asynchronous. Captured-cursor behavior is
verified separately by the reference snapshot and small native inventory tests.
