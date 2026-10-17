# Asset search metadata and reusable pages

Existing `manage_asset(action="search")` calls retain their current behavior. They return live
instance IDs by loading objects on the requested page, even when `generate_preview=false`.
Previews remain optional. Existing page limits, ordering, date filtering and CLI defaults are unchanged.

Opt into metadata and reusable pages with `metadata_only=true`:

```python
manage_asset(action="search", path="Assets/Characters", filter_type="Prefab",
             metadata_only=True, page_size=25, page_number=1)
```

The first response includes `data.searchSnapshot`, `snapshotContractVersion=1`, `expiresAtUtc`
and `hasMore`. To request another page, repeat the same effective query, folder scope, type filter,
UTC date cutoff and page size, then pass that token as `search_snapshot` with a new `page_number`.
Repeating a page returns the same captured rows. A continuation does not repeat `FindAssets` or
the file-date scan. It is a captured view, rather than a live asset listing.

Metadata rows include the normal path, GUID, main asset type, names, folder flag and UTC write time.
They explicitly report `metadataOnly=true`, `instanceIDAvailable=false` and `instanceID=0`, with empty
preview fields. This path makes no explicit object-load or AssetPreview calls. Unity's type-query
API does not promise that the engine performs no internal loading or object instantiation. Preview
generation cannot be combined with metadata search.

The CLI provides the same opt-in:

```sh
unity-mcp asset search "t:Prefab" --path Assets/Characters --metadata-only --limit 25
unity-mcp asset search "t:Prefab" --path Assets/Characters --metadata-only --limit 25 --page 2 --search-snapshot TOKEN
```

The native action is versioned as `search_metadata_v1` on `manage_asset`. An older Editor plugin
refuses this action; the server and CLI return that refusal without falling back to loading search.

Snapshots live only in the current Editor domain: at most four entries, 4,096 matching rows per
entry and 8 MiB of aggregate accounted UTF8 row/key payload, including the snapshot under
construction. Individual strings are limited to 4,096 characters and serialized rows to 64 KiB.
Entries expire after a fixed 60 monotonic seconds from search start; reads do not extend expiry.
Least recently used entries can be evicted earlier. These are retained-data and construction
limits, not a process-heap ceiling: Unity's initial full `FindAssets` GUID array, its internal
work, and transient response copies/serialization are outside the bound. Narrow folder/query
scope for large projects.

Observed imports, reimports, moves, deletes and domain reload discard snapshots. Relevant
`manage_asset` mutation attempts also invalidate them before execution. Post-import callbacks
run after imports complete, so external filesystem changes are not promised immediate notification
before Unity refreshes/imports them. Existing server preflight remains enabled.

Expired, evicted, invalidated or other-session tokens return `stale_search_snapshot`; start an
explicit new metadata search. Changed query semantics return `search_snapshot_query_mismatch`.
Malformed tokens return `invalid_search_snapshot`. `search_snapshot_limit_exceeded` asks you to
narrow the query; results are never silently truncated. Invalid continuations do not rebuild a
snapshot or fall back to legacy search. No performance improvement or initial-query heap bound
is advertised without measurement.

The API qualification follows Unity's [2021.3 type query](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/AssetDatabase.GetMainAssetTypeAtPath.html),
[6000.0 type query](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/AssetDatabase.GetMainAssetTypeAtPath.html),
[post-import callback](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/AssetPostprocessor.OnPostprocessAllAssets.html)
and [FindAssets array result](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/AssetDatabase.FindAssets.html) documentation.
