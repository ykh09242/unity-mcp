# Reference evidence provenance

External JSON/CSV inputs use a built-in versioned adapter. No code is imported
from the external repository and no driver, Git executable or additional package
is needed. Existing source configurations default to `json-csv` version 1.
Unknown adapter names/versions are rejected rather than silently interpreted.

An explicit source configuration can include:

```json
{
  "root": "/absolute/authorized/settings-repository",
  "include": ["data/**/*.json", "tables/*.csv"],
  "exclude": ["data/private/**"],
  "adapter": {"name": "json-csv", "version": 1},
  "git_revision": true,
  "mappings": [
    {"field": "/items/*/resourceId", "scheme": "resources",
     "values": {"42": "UI/Icon"}},
    {"field": "prefab", "scheme": "addressables"}
  ]
}
```

The root must already be authorized for the authenticated user and resolved
Editor/project scope. Windows absolute roots are supported too. The example
paths and IDs are illustrative, not built-in project conventions. Numeric JSON
values contribute only through explicit `values` mappings; unmapped values and
non-string fields retain diagnostics. An adapter never guesses a game-specific
ID convention. Label mappings support `addressables_label` with `mode: "all"`.

## Locations and publication

An edge retains its existing whole-asset GUID identity. `source_pointer` identifies
the exact JSON pointer or a CSV data-row pointer, such as `/rows/0/prefab`.
JSON pointer escaping follows `~0`/`~1`. CSV row ordinals are independent of
physical line numbers, so quoted multiline cells remain distinguishable. Existing
`line` and `evidence` fields remain available.

The observation stores provenance once per source file/provider, not once per
edge. Query pages join it without a query for each edge:

- Adapter name/version and provenance schema version.
- Relative file path and SHA-256 of the bytes actually analyzed.
- Optional observed repository HEAD, or an explicit unavailable/disabled state.

Pending provenance becomes visible atomically with its matching generation's
edges. A stale edge continues to show the previous published file hash while a
new generation is pending or has failed. Schema 1 stores migrate without deleting
authored declarations or existing evidence. Older rows without recorded provenance
return an empty provenance object until reanalysis.

## Optional Git identity

`git_revision` defaults to false. When enabled, the adapter reads only `HEAD`, its
validated loose ref, or `packed-refs` inside the granted root's `.git` directory.
It does not execute Git, inspect repository configuration/remotes, traverse parent
repositories, follow symlinks/hardlinks, or follow a `.git` file to another root.
An external Git worktree whose metadata resides outside the grant reports
`unavailable`; source-file reference analysis still works.

`observed_head` records repository identity at the scan, **not proof that the
working-tree file's bytes belong to that commit**. The separate file hash is the
content identity. Repository changes are checked across a scan and again at
query delivery. A HEAD-only change refreshes metadata in batches without parsing
unchanged JSON/CSV files. Access grants and filesystem permissions are still
checked per request; a successful metadata read is never cached authorization.

## Optional asset identity

Additional edge fields refine evidence without changing whole-asset IDs:

- `target_file_id` from a serialized `{fileID, guid}` mapping.
- `subobject` and `subobject_guid` from the same serialized AssetReference mapping.
- `subobject` from an Addressables `address[subObject]` key.
- `target_type` retains the generic loader type spelling, including through
  supported wrappers. It is a requested type, not a verified runtime type.

YAML is scanned as tokens, without constructing tagged objects or expanding
aliases. Identity fields in text scalars or adjacent mappings cannot be combined.
Duplicate identity fields fail explicitly. Lexical serialized evidence remains
`possible`, since a custom field can use the same serialized name.

The native inventory reports `main_type` through
`AssetDatabase.GetMainAssetTypeAtPath` and `identity_coverage: "main_type_only"`.
It does not eagerly load every subasset. A Sprite request is therefore not rejected
merely because the containing file's main type is Texture2D. Subobject existence,
assignability and runtime catalog locations remain unverified without additional
runtime/detail evidence. These fields are not safe-delete or complete-usage proof.

## Verification

Tests cover adapter versions, explicit numeric mappings, JSON/CSV locations,
in-root loose/packed Git refs, inaccessible metadata, generation publication,
schema migration, HEAD-only updates without source reads, serialized subobjects,
typed Resources requests and Addressables subobject keys. Native tests use the
existing isolated project and Library. Python CI runs on Windows, Linux and
macOS; actual native execution is Windows-only.

Primary contracts: [Git repository layout](https://git-scm.com/docs/gitrepository-layout),
[main asset type](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/AssetDatabase.GetMainAssetTypeAtPath.html),
and [Addressables subobjects](https://docs.unity.cn/Packages/com.unity.addressables%401.17/manual/AddressableAssetsGettingStarted.html).
