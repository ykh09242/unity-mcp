# UI asset inventory pages

`manage_ui(action="list")` returns complete scalar UI asset observations for its
folder and optional type filter. It does not load asset objects or request a
refresh/import. UXML, USS and PanelSettings groups are ordered in that order, then
by ordinal GUID within each group. The main asset type determines membership;
embedded UXML style subassets do not duplicate the UXML file as a USS asset.

The response retains `total`, `pageSize`, `pageNumber`, `maxPageSize` and `assets`.
When retained, `inventory_revision` identifies the inventory. Pass it on later
pages with the same folder and type selection to require that observation:

```json
{"action":"list","path":"Assets/UI","page_size":50,"page_number":2,"inventory_revision":"mcp-ui-v1_0123456789abcdef0123456789abcdef"}
```

Use the actual revision returned by the first page. Filter aliases such as
`uxml` and `VisualTreeAsset` select the same inventory; page size may change.
Omitting the revision uses the current cached inventory or discovers a new one.
Every returned row is detached. The observation is not a persistent asset handle.

Imports, deletions, moves, domain reload and editor shutdown invalidate retained
inventories. An invalidated, expired or evicted revision returns
`stale_ui_inventory`; a retained revision with different scope/type returns
`ui_inventory_query_mismatch`. Restart discovery without the revision after
either response. No mutation is started by listing or retrying discovery.

The cache retains up to four inventories for 30 minutes, with a shared 64 MiB
conservative accounting budget for scalar rows and strings. It releases expired
rows while idle and removes its update subscription when empty. These are cache
admission and retention policies, not limits on project size or query duration.
An inventory that cannot fit still returns its complete count and requested
page, with `inventory_retained=false`, `inventory_revision=null` and an explicit
note that uncached offset pages are not bound across asset changes. Results are
never truncated to fit the cache. `inventory_complete` refers to enumeration of
the selected indexed asset types, not unimported files or asset contents.
