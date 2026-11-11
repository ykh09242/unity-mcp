# Reference graph exploration

The `manage_reference_graph` tool exposes explainable graph exploration within the
authenticated user's selected Editor/project scope. SQLite stores traversal queues
and snapshots in that same private graph database. No additional package is needed.

| Action | Options | Result |
| --- | --- | --- |
| `explain` | `id`, `consistency?` | One recorded edge with provider, source path/line, reason, confidence, target identity and available generation-matched provenance. |
| `impact` | `node`, `direction?`, `limit?`, `cursor?`, `max_depth?`, `consistency?` | Reachable incoming or outgoing evidence. Each node expands once; distinct edges and multiple routes remain visible. `via` identifies the first discovery parent edge. |
| `path` | `node`, `target`, `direction?`, `limit?`, `cursor?`, `consistency?` | A shortest path in edge count using breadth-first search. `path_found` is null while searching, true when found, or false when exhausted. |
| `snapshot` | `name?`, `limit?`, `cursor?`, `consistency?` | Copy one graph revision into a named snapshot. Continue until `snapshot.state` is `ready`; use its `id` for comparison. |
| `diff` | `before`, `after`, `limit?`, `cursor?` | Added, removed and modified evidence between two ready snapshot IDs, including old and new provenance. |
| `release_query` | `id` | Release a `query_id` and its continuation data; discard an incomplete snapshot. Ready snapshots are preserved. |
| `delete_snapshot` | `id` | Delete that scope's captured snapshot and associated query data. Authored declarations and automatic graph inputs are preserved. |

These operations require read permission. They modify only private derived query
state, not Unity assets, authored declarations or external files. An ID or cursor
from another user, Editor or project cannot select that scope.

## Continuation and large workloads

Live queries and status responses include `data.graph_status.hint` while collection is
pending, in progress, waiting for the Editor, or reporting an error. A page can already
contain references, or be empty, before indexing finishes. Run a fresh query after
collection completes. Completed collection omits the hint even when the result has
another page or an external source uses cached verification.

During collection, the first page of a cached outgoing query can bring an
admitted, pending asset forward. The response contains the available evidence
while the worker processes the request. Query the node again to see newly
collected direct dependencies. Repeated requests for one GUID share a place in the bounded
queue, and the collector continues its ordinary scan between requested assets.
Incoming queries can gain references as other owners are collected. An older
Editor continues its ordinary inventory scan. If several MCP servers use the
same graph database, upgrade them together so each server coordinates policy
changes with a requested asset read on the others.

Page size defaults to 100 and accepts 1 through 1,000. This is a response/work page,
not a total graph, result, memory or elapsed-time limit. Omit `max_depth` for all
reachable nodes. Follow `next_cursor` until null even when a page has no edges or
changes: a traversal can be processing leaves, a path can still be searching, and
a diff can be comparing unchanged rows. `diff.compared` counts candidates inspected
on the current page; unchanged large snapshots do not require a whole-graph scan
for the first page.

Direct queries, traversal adjacency pages and snapshot captures budget raw edge
candidates before source/admission/exclusion filtering. An excluded manual edge
therefore consumes work without appearing in the response. Such a page can be empty
and still have `next_cursor`; continue until it is null. Rejected candidates advance
the same revision-bound anchor as admitted evidence. Oversized admitted evidence
still fails explicitly, while excluded evidence payloads are not materialized into
Python. See the [bounded-work measurements](reference-graph-bounded-work.md).

Direct `query` cursors are at most 1024 characters, including when authored edge
IDs are long. They retain a compact physical-row anchor and a persisted reclamation
generation instead of copying an arbitrarily long ordering key. When cleanup
physically removes retired edges, it atomically advances that generation. A later
page can therefore return `reference_graph_changed` and require restarting from
the first page even though the semantic graph revision is unchanged. Reopening
the server preserves this check. Restarting returns the complete currently visible
result; callers must discard the earlier partial accumulation rather than append
the restarted result. This is explicit invalidation, not transparent continuation.
Legacy stable-key cursors remain accepted, and legacy physical-row cursors retain
their row/digest checks. Snapshot/traversal ordering keys and immutable snapshot
comparison cursors retain their existing behavior.

Hidden retired rows consume candidate work but no visible response-byte allowance.
SQL still suppresses genuinely oversized projections before they enter Python,
and those rows fail explicitly rather than being mistaken for hidden evidence.

Cancellation signals the producer's SQLite progress callback and drains its physical
thread before releasing ownership. The callback composes with storage accounting;
rollback and commit recovery retain their existing transaction guarantees. SQLite
lock acquisition can still wait for its configured busy timeout.

Repeat the original options on continuation, except use `consistency='checked'`
only on the first page and then omit it. Traversal and snapshot cursors require the
same graph revision; changes cause an explicit restart error rather than a mixed
snapshot. They survive server restart. Each job retains its latest page to replay
a lost-response retry; an older cursor cannot rewind an advanced job. Continue one
cursor sequentially. After completion, large traversal queues and visited rows are
released automatically. Release the remaining job receipt explicitly when done.
There is no automatic expiration that abandons an active large traversal.

Path query schema version 6 maintains proof count and last sequence in the same
transaction as proof insertion and page progress. Its one-time migration backfills
existing jobs from retained proof rows; rollback preserves the previous schema and
job state. Duplicate evidence does not increment the counters. These counters only
avoid repeated SQL aggregation: every proof batch still rechecks current scope,
revision, source and permission boundaries, including during replay.

Shortest path edges are returned in **target-to-start** order to avoid materializing
an arbitrarily deep path in memory. Reverse the accumulated path, or order its
edges by ascending `depth`, for a start-to-target display. A tie chooses the first
edge in deterministic ID order. This is an asset/evidence path through the recorded
graph; wrapper call labels are retained as evidence, not invented call-stack nodes.

## Freshness and authorization

`consistency='checked'` synchronizes native evidence and rehashes approved external
inputs before starting. Cached exploration exposes the normal provider freshness
status and may contain stale or incomplete static evidence. A ready snapshot means
its database revision was copied completely; it does not establish whole-program
coverage or independently prove filesystem freshness. An unreachable target means
no path in that captured graph, never proof that an asset can safely be deleted.

External source version and operator root authorization are checked for every
participating provider, including zero-result pages. Every historical comparison
candidate, including unchanged rows, requires current read access to its original file path. A
revoked or changed grant, inaccessible/deleted original file, or replaced scope
fails closed. File content may legitimately differ from the captured version;
historical comparisons do not reread it or pretend the historical hash is current.
The response explicitly reports `historical=true` and `graph_status.current=false`.

Snapshots preserve stable authored edge IDs so edits appear as `modified`.
Generated identity includes its evidence payload, so an altered generated relation
can appear as a removal and addition. Provenance or node-state changes with the
same identity appear as modifications. Code analysis now publishes a compact build
context and analysis fingerprint atomically with its edge generation. Older
evidence without that metadata remains explicitly absent until its next analysis;
the tool does not attach today's build context to an older edge. Published source
paths remain attached to their evidence generation. For stale legacy evidence
without a captured path, `source_path` is empty and `source_path_origin` reports
`unavailable`; `current_source_path` separately reports the owner's current location.

## Reproducible scale check

`tools/experiments/reference_graph_queries.py --scratch <owned-directory> --count 50000`
creates synthetic scope-owned data, captures two snapshots, measures the first
unchanged comparison page, and traverses every fanout edge through continuation.
It checks complete counts and disposal of finished traversal rows. The reported
Python traced peak excludes SQLite native memory; SQLite VM instruction counts
measure bounded query work, not physical disk latency.
