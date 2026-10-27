# Reference graph operations

Open the **Unity MCP** window, then **References**. The panel uses the configured
HTTP connection and its existing local launch token or remote API key. It never
selects a user or database directly. A remote account sees only its authenticated
scope; the server also verifies the actual Editor process and project identity
before creating or opening a graph. Stdio users can use the same operations through
`manage_reference_graph`; the Editor panel requires an HTTP connection.

**Refresh status** shows the opaque account fingerprint, Editor/project identity, native
progress, individual provider freshness/errors, retained job counts and SQLite
allocated/reusable bytes. The storage figure excludes WAL sidecars. Memory reports
whether rebuildable caches are present; it is not a heap-byte measurement. Cached
external evidence is never presented as a freshly verified scan.

**Retry collection** clears collection backoff and wakes the existing worker.
Large graph jobs have no new total deadline. Refresh is explicit: the panel does
not repeatedly count the database or start a second indexing loop. Hiding or
disposing it cancels its HTTP wait and discards late results; server work or a
configuration change may already have completed. Refresh before retrying a write.
Connection/account changes invalidate displayed state and transfer fields.

## Configuration transfer

**Collection policy** applies `.gitignore` by default and accepts additional
graph-only exclusions. See [Reference graph exclusions](reference-graph-exclusions.md)
for matching rules, external-source boundaries and existing-data behavior.

Select a kind (`rule`, `source` or `policy`), read the first page and continue with
**Next page**. **Copy page** copies only documented configuration fields,
item IDs and versions, without credentials or operator grants. Pages are per-item
observations, not an atomic configuration snapshot. Configuration values and
approved folder paths are still private project information.

Select an item to populate its config and destination version, or paste a config
and supply an explicit ID. Import updates one item using compare-and-swap: read
the destination version first; version 0 means create. An exported source version
does not authorize overwriting a destination. A stale version returns a conflict.

Sources require the **Approve this source's read access for this account** checkbox. Enable
the source only when its root/include/exclude/mappings are appropriate for the
destination. Remote paths belong to the server, and enabling them additionally
requires its operator's current per-user external-root grant. Import never copies
another account's authorization. Existing authored declarations remain managed
through the declaration actions; transfer does not replace or delete them.

The equivalent MCP actions are:

```json
{"action":"export_config","options":{"kind":"rule","limit":100}}
{"action":"import_config","options":{"kind":"rule","id":"loader","expected_version":0,"config":{"call":"Loader.Load","argument":0,"scheme":"resources"}}}
```

Continue exports with `next_after`. There is no total item count limit. The same
version checks and source authorization apply after a server restart.

## Maintenance and retention

**Clear eligible scratch** explicitly cleans completed query scratch older than
one day and abandoned staging. Each click has a bounded row budget; a large
completed traversal is removed over multiple calls. Repeat while the response's
`more` value is true. MCP callers may choose `before` (Unix time) and a `limit` of
1–1000 per batch. These are work-per-call bounds, not total workload limits.

Unfinished queries, active worker staging, snapshots, mutation receipts, runtime
captures, graph evidence, rules, grants, policies and declarations are preserved.
Memory caches are cleared only while their reconciler is idle. SQLite pages become
reusable; cleanup does not run a blocking VACUUM or promise an immediate smaller
file. Historical snapshots/captures/receipts retain their explicit release actions.

The authenticated `/api/reference-graph` facade exposes only these panel actions.
It uses the existing local/remote authentication middleware, operation permissions
and credential lifetime, rejects browser-origin local requests, and rejects extra
identity fields. The Editor client refuses redirects and unsafe configured URLs;
credentials are never included in graph configuration or displayed errors.
