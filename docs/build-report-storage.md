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

On the next write after an Editor domain change, valid prior-session active
markers move into a compact interrupted-observation archive (latest 64, 30 days).
They remain visible in `active_observations` as `interrupted`, never as completed
or resumed builds. Archive publication precedes removal of the original markers.
Current-session markers keep their existing capacity independently of this history.

Optional recovery passes reclaim abandoned owned generations and session-tagged
temporary files in bounded batches, at most once per five seconds per store/session.
Published revisions, in-flight publishers, current-session files, unknown content,
invalid owner markers and linked paths remain protected. Legacy temporary filenames
without session ownership remain untouched, and unrelated files no longer consume
the report inventory budget. Recovery does not cancel, restart or time out work.

Recovery carries scalar scan positions across passes, including preserved entries,
and wraps at the end of each inventory; it retains no directory handles between
writes. Candidate validation and deletion are bounded, while resuming a position
may enumerate earlier filenames without opening them. Corrupt interrupted archives
are preserved under a unique `interrupted-*.corrupt.json` name before a fresh
archive is published. History keeps an explicit corrupt-observation indication;
these preserved files are not automatically deleted.

An exact persisted job-status lookup opens only the summary at the deterministic
report identity for that job, then validates its embedded job ID, integrity and
expiry. It does not scan unrelated completed reports. An unspecified/latest
status query retains the existing inventory selection. Returned summaries remain
detached values; unavailable, corrupt or expired evidence still cannot become a
successful status observation.

### Detail page validation costs

Each requested detail page still rereads and verifies its index and overlapping
shards. Shard validation shares the row contract with legacy reports, without
cloning the enclosing summary or attaching the rows to a temporary record. Hashes
use the original bytes after strict UTF-8 decoding, avoiding a second encoding.
No parsed-page cache is retained; corruption and replacement between requests
remain observable, and response rows remain detached from later requests.

### Build size attribution

`manage_build(action="compare_reports", report_section="packed_assets", ...)` returns
`size_comparability` with the differing or missing fields. New native summaries retain
their target, numeric BuildOptions mask and Editor version. A successful pair with
matching values returns `matched_report_settings`. Project settings outside this
native report scope require separate evidence. Historical summaries lacking these
fields return `unknown` unless a known difference already establishes incompatibility.

For a matched pair with complete captured inventories, `named_packed_delta_bytes`
sums the changes to identifiable source paths across the entire comparison window.
`unattributed_delta_bytes` subtracts that number from the change in total build size.
It includes anonymous packed entries and differences such as compression or file
overhead, so it can be negative. A missing total, incompatible settings or truncated
detail leaves the corresponding attribution unknown. Existing raw differences and
per-asset comparisons stay available for inspection.

The explicit native fixture `BuildReportActualBuildTests.Phase1BuildOneOwnedSceneAndPersistNativeReport`
accepts `MCP_BUILD_REPORT_SIZE_PAIR=1` alongside its normal exact disposable project
and nonce. It builds the same scene twice with DetailedBuildReport, adding one
deterministic 4 MiB resource for the second run. The owned report directory retains
`size-pair.json` with both summaries, report revisions and comparison. In the Windows
Unity 6000.0.69f1 run, the 4,194,304-byte source produced 4,194,324 packed bytes,
4,194,416 additional total bytes and a 92-byte remainder. This single pair measures
the fixture with those build settings.

### Editor and Player build conditions

Request `manage_build(action="build", options='["detailed_report"]', ...)`, then read
`manage_build(action="report", report_id=...)` after completion. The summary's
`native_build_guid` can be matched to `Application.buildGUID` in a Player witness.
The sibling `build_conditions` object contains observations from that invocation.
Older archives and builds without the callbacks return null. Reading a report
after switching targets continues to show the recorded targets.

| Field | Evidence |
| --- | --- |
| `editor_active_target`, `editor_active_defines` | Active Editor target and compiler directives during build preparation. |
| `player_target`, `player_extra_defines` | Target and additional define inputs from the actual `BuildPlayerContext.BuildPlayerOptions`. |
| `player_settings_defines` | PlayerSettings symbols observed for the selected named target/subtarget during preparation. Built-in and assembly-specific defines require additional evidence. |
| `player_filter_assemblies` | DLL names observed at Unity's post-compilation assembly filter, before subsequent stripping and packaging. |
| `editor_compilation_only_assemblies`, `player_filter_only_assemblies` | Differences between the Editor script compiler inventory and Player filter inventory. Precompiled plug-ins can appear in the latter. |

Each list returns up to 256 sorted identifier names. Treat absence as unknown
when `complete` is false or the list is missing. A runtime branch, gameplay
outcome or stripped type requires a witness from the built Player. See Unity's
[build context](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/build/buildplayercontext/buildplayeroptions)
and [assembly filter](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Build.IFilterBuildAssemblies.OnFilterAssemblies.html).

For the reproducible reward regression, use a disposable copy of
`TestProjects/UnityMCPTests` with a licensed Windows Editor, its Windows
Standalone build module, and Windows64 already active. Set
`MCP_DEV06_PROJECT_ROOT` to its absolute path, `MCP_DEV06_NONCE` to a fresh
32-digit lowercase hex GUID and `MCP_BUILD_CONDITION_PAIR=1`. Invoke EditMode
test `MCPForUnityTests.EditMode.Tools.BuildReportActualBuildTests.Phase1BuildOneOwnedSceneAndPersistNativeReport`
by its full name. It executes the Editor reward transition, then builds and
launches separate broken and repaired Players. The broken build receives
`MCP_FIXTURE_REWARD_DISABLED` and reports zero coins and exit 42. Removing that
build-only symbol restores one coin and exit zero. Both outcomes must carry
their own native build GUID. The test requires the Editor-only package assembly
in the Editor difference and the gameplay fixture assembly in the Player filter.

Copy `.work/Dev06Reports_<nonce>/build-condition-pair.json` from the disposable
project before the cleanup phase. Validate that archived receipt on any Python
host with `python tools/check_build_condition_pair.py <receipt>`. The offline
contract tests use synthetic records to exercise rejection of mismatched
process and build identities and incomplete define evidence.
