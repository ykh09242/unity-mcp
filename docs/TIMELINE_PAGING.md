# Large Timeline observation

`manage_timeline` reads saved, contained `Assets/*.playable` assets without a
track, hierarchy-depth, clip, subasset or serialized-file-size admission limit.
Every response remains bounded. Observation supports all track kinds; authoring
and manual preview retain their existing supported-type restrictions.

Start with `action: "read"`, `path` and optional `page_size` (1-32, default 16).
Use the returned `next_offset` as the next request's `offset`, carrying the same
`expected_revision`. A page can contain fewer tracks than requested to stay
within the response budget. A null `next_offset` completes track enumeration.

Each track row contains `clip_count`, `clip_offset`, `clips` and
`next_clip_offset`. Read the remaining clips with `action: "read"`, the same
`path` and `expected_revision`, the exact row's `track_id`, and `clip_offset`
from `next_clip_offset`. Omit the track `offset`. `clip_page_size` accepts 1-32
and defaults to 16. A null `next_clip_offset` completes that track's clips.
Clip pages may also be shorter than requested to fit the byte budget.

Track and clip labels are limited to 1024 UTF-16 code units without splitting a
surrogate pair. `name_truncated` and `display_name_truncated` identify shortened
labels. Exact persistent IDs remain the selectors; shortened labels are not
authoring selectors. Individual track clip pages stay within 64 KiB of clip
data, and the complete graph response remains below 256 KiB.

`read_director` similarly returns `binding_count` and `next_offset`. Continue
with `offset`, `page_size` and `expected_director_revision` from its first page.
Director revisions and preview binding guards cover all bindings, including
bindings outside the returned page.

Revisions cover saved asset and metadata bytes, dependency state, all owned
serialized subassets, and live serialized clip sources. File bytes are hashed
in 64 KiB chunks; native serialized-property hashes avoid graph-sized JSON.
Editors without property hashes use leaf traversal. Leaf object-reference digests
retain the native 32-bit instance ID below Unity 6000.4 and the full 64-bit
EntityId from Unity 6000.4, formatted
with invariant culture. Read again after an Editor upgrade.
Every page revalidates the whole revision, so total validation work scales with
asset size, while response
materialization stays bounded. Changing saved or unsaved state invalidates
continuations and stale writes. Restart from the first page after drift.

Traversal uses an explicit iterator stack and inspects live serialized child
and parent edges before dependency hashing, serialization, or native duration
queries. Timeline's public child enumerator caches a copied list, so using that
cache alone can miss a newly introduced malformed edge. The preflight rejects
repeated/cyclic tracks, mismatched parents, missing tracks, and tracks or clip
playables owned by another asset. It then compares an iteratively validated
native cached hierarchy with the serialized hierarchy, refusing stale caches
before native recursion. Already materialized flattened and output caches are
checked for foreign, duplicate or detached tracks; flattened membership is
order independent because Unity's flattened order differs from the traversal.
Native duration is normalized before the page revision
is captured, including serialized clip extrapolation fields. Request
cancellation is checked during traversal, hashing and source resolution, before
authoring begins. Final director guards and preview preparation retain the
request token, and writes recheck cancellation after native Undo preparation.
Later preview seeks use their own request token through duration validation and
evaluation; stopping an owned graph completes its conditional cleanup.
Native subasset enumeration still returns a Unity-owned array;
the tool does not duplicate its serialized payload or impose a subasset cap.

## Native regression and benchmark fixture

Run the existing EditMode fixture `MCPForUnityTests.Editor.Tools.TimelineAuthoringTests`
in the repository's isolated native test project with the Timeline package
already enabled. The fixture creates and deletes only its unique owned Assets
folder and scene. Do not install a package merely to run it.

The `LargeGraph_` tests cover 80 tracks with revision-bound pages and Undo,
600 clips on one track with more than 512 owned subassets, depth 16, an asset
larger than 8 MiB, unsaved drift, integer overflow rejection, cycles, foreign
ownership, stale native child caches, cancellation, director pages and long Unicode labels.
`PreviewSeek_ParentCycleStopsOwnedGraphBeforeNativeDuration` verifies that a
cycle introduced after preview startup stops the owned graph safely. Separate
fixtures cover cancellation for assign/bind/preview startup
after the final director guard and for cancellation during seek duration checks.
The 80-track case emits `TimelineLargeGraphBenchmark` with elapsed milliseconds and managed
retained-memory delta. Retained memory is not an allocation measurement, and
reference compilation is not native execution evidence. Preserve the native
runner's actual result XML and benchmark output separately from source.
