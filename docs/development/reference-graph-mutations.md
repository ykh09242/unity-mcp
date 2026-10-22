# Mutation receipts and import verification

Reference graph receipts connect AI script and asset writes to later imported
evidence. A successful tool response and a synchronized graph are separate facts.
A receipt does not provide filesystem transactions, exactly-once dispatch, or
proof that every difference was caused by that one tool call.

## Automatic tracking

An already active graph tracks supported script mutations, asset mutations, and
script change-set apply/recovery for its authenticated user and selected Editor.
Inactive graphs perform no additional native or database calls. Preview/read
operations do not create receipts. Existing developer installation is sufficient.

The server captures incident evidence before dispatch, invokes the native write
once, and appends `data.reference_graph` to a compatible response. The reference
contains `id`, `scope`, `state`, and `outcome`. It preserves the native tool result.
If graph preparation fails, the write remains available and the response explicitly
reports unavailable or incomplete tracking. Inspect a partial preparation receipt
before releasing it; it does not represent the entire attempted batch.

The outcome is `accepted`, `failed`, or `unknown`. Disconnects, timeouts, cancelled
dispatch, and read-back recovery retain `unknown` rather than proving causation
from an observed file. Native writes are never replayed to repair graph tracking.
Failed operations may have partial effects and can still be synchronized.

## Explicit batches

Use the following `manage_reference_graph` actions to bracket an external AI
workflow or combine more targets. Complete all target preparation before writes.

| Action | Options | Result |
| --- | --- | --- |
| `begin_mutation` | `paths`, or `guids` from this graph's inventory | Durable receipt and before evidence |
| `add_mutation_targets` | `id`, `paths` or `guids` | Another target page at the same native/graph checkpoint |
| `finish_mutation` | `id`, `outcome` | Seal a caller-reported outcome; default is `unknown` |
| `await_mutation` | `id`, optional `wait_seconds` | Synchronization state and a pending reason |
| `mutation_diff` | `id`, optional `limit`, `cursor` | Historical direct incident changes |
| `release_mutation` | `id` | Release this receipt and its private snapshots |

Paths must be admitted `Assets/` or `Packages/` paths. Each target page contains
1-1000 entries; additional pages have no total target-count cap. A GUID is resolved
from the scoped inventory and checked against the native probe. Use paths for
new assets not yet imported. A changed preparation checkpoint requires restarting
the receipt, so a before snapshot cannot silently mix unrelated graph revisions.

```json
{"action":"begin_mutation","options":{"paths":["Assets/Scripts/Loader.cs","Assets/Prefabs/Enemy.prefab"]}}
```

After the writes, seal the returned ID, then call `await_mutation`. A zero-second
wait advances a verification page and returns. A positive wait continues while its
request remains active. There is no receipt age or whole-job timeout. Cancellation
does not cancel the graph watcher, delete snapshots, or lose target verification
progress. Another request can release a receipt while a caller is waiting.
After a server restart, use the same Editor/user scope and receipt ID. An open
receipt whose response was lost can be sealed with `unknown`.

## Evidence boundary

The native probe is read-only. It reports the physical file stamp, GUID, folder
state and AssetDatabase dependency hash without importing or loading asset objects.
Changed files need a corresponding import-journal record whose observed file stamp
matches the probe. An earlier import of different bytes at the same path is
insufficient. Graph-collected versions must match too. Legacy databases backfill
only requested targets; unrelated assets are not re-read merely to enable receipts.

An unchanged file needs no extra import or refresh. Folder mutations require a
matching folder/descendant import event; a folder's metadata alone cannot prove a
whole-tree no-op. Compilation failure, deferred import, incomplete collection,
exclusions and changed checkpoints remain visible rather than being treated as
completion. A replaced journal epoch requires a complete fresh census before
verification resumes. Imported state is the authority; receipt verification is
not an atomic snapshot of concurrent, unimported filesystem writes.

Verification processes 128 target rows or 1000 journal rows at a time. Its durable
cursor is tied to both native and graph revisions and resets when either changes.
These are page sizes, not workload limits. Database rows and incident edges are
retained on disk; no whole traversal is held by a request or background task.

## Comparing results and scope ownership

Once `state` is `synchronized`, `checked` identifies the observed native epoch,
journal revision, graph revision and verification time. This is a historical
checkpoint, not a promise that the Editor remains unchanged. `mutation_confirmed`
requires an accepted tool response; caller assertions and unknown dispatch remain
distinct even after graph synchronization.

`mutation_diff` compares incident edges owned by or pointing to the touched GUIDs,
including old/new path descendants and authored declarations. It does not infer
exclusive causation, and a partially indexed before graph can produce incomplete
before evidence. Inspect graph status before preparing a complete comparison.
Use `impact` or `path` for transitive exploration. Empty diff pages can still have
a continuation cursor; continue until it is null.

Receipts, snapshots, progress and cursors use the existing authenticated
user/actual Editor/project database. A different Editor process or user cannot
select another scope's receipt. External roots and historical file paths are
reauthorized at delivery, including unchanged/empty diff pages. No receipt can
replace automatic evidence with AI-declared edges or bypass declaration versions.
Release removes derived receipt data and preserves authored declarations.

Checks cover deferred imports, no-ops, compilation failure, create/delete, unknown
outcomes, cancellation, release during wait, legacy migration, restarted progress,
GUID/path changes and large target pages. Windows native checks exercise actual
import/file stamps, move/delete and domain reload; other OS Python checks exercise
the scoped state machine without claiming native Unity execution on those hosts.
