# Retained build report storage

New completed reports publish a compact version-2 summary at the existing report
identity, plus owned detail shards in a generation directory. Publication writes
and hashes the shards and their indexes first, then atomically replaces the
summary. The summary revision covers the detail index digests. Old version-1
artifacts remain readable without an automatic migration or build replay.

History and persisted status read only summaries. Buffers match bounded actual
file lengths; small files no longer allocate 512 KiB each. New summary revisions
are checked against the stored payload text, avoiding canonical serialization of
all detail rows on every read. A report page reads the section index and only its
intersecting 256-row shards, verifies their hashes, then rechecks the published
summary revision. It does not parse earlier pages. Corrupt requested evidence,
expiry, revision conflicts and eviction retain explicit errors. Summary/history
responses say `details_integrity: not_checked`; a detail page verifies only its
requested scope, not every retained shard.

Ordinary capture keeps the existing 512-step / 2048-packed-row bounds. All rows
within those bounds remain pageable even when their combined bytes exceed the
old single-file budget. Requesting the existing build option `detailed_report`
now requests complete scalar-row capture from Unity's native report API. Rows are
written incrementally rather than retained in one large JSON graph. Unity's
native `steps`, `packedAssets` and per-container `contents` arrays are themselves
indivisible; this cannot make Unity produce unavailable appendices or avoid its
own array allocation. No native BuildReport is retained by the store.

Optional detail storage allows 256 MiB of shard data per report and 4096 shards
per section. Indexes and summaries remain individually bounded at 512 KiB.
Completeness flags and notes expose any storage/capture limitation. The original
64-report / 30-day retention remains. Summary publication falls back when optional
complete capture fails; storage failure never turns a successful build into a
failed build. Stale-generation cleanup handles only owned filenames under checked
paths and does not follow links or recursively delete unknown content.

Comparison keeps its existing 512-step / 2048-packed-row working window. Results
include total retained counts and explicit scope/completeness so partial comparison
cannot be mistaken for full large-report comparison. Use report pages for complete
retained detail. No build limit, deadline, scene limit or target setting changes.

Windows stream paths use the [extended-length form](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)
after containment/link checks; this keeps nested project locations readable on
Unity Mono. Cleanup requires a matching generation owner marker and refuses
unknown entries before deleting any files.
