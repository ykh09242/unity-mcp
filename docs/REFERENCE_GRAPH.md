# Automatic reference graph

`manage_reference_graph` combines Unity's direct asset dependencies, conservative
C# loader and wrapper analysis, serialized Addressables GUIDs, explicitly granted
JSON/CSV sources, and authored reference declarations. It uses Python's bundled
SQLite. Normal Unity MCP installation is sufficient: no database service, driver,
compiler, parser download, or extra developer setup is required.

Activate the `assets` tool group and select the intended Unity instance. Start with:

```json
{"action":"sync"}
```

The call schedules work and returns progress. Poll `status`; large projects do not
need to fit in one MCP request. While the server runs, an activated graph checks
the Editor journal automatically. Imports, AI script changes, moves, deletes, and
supported unsaved Addressables catalog changes update their evidence. Unchanged
scripts are not loaded or parsed again. External sources are checked for file
state and current access; changed files are reanalyzed. `sync` with
`{"force":true}` in `options` also rehashes external inputs whose timestamps were
preserved. There is no asset-count, file-size, graph-size, or whole-job deadline.
Page sizes and journal retention bound individual operations, not total work.

## Query and inspect uncertainty

```json
{"action":"query","options":{"node":"asset:0123456789abcdef0123456789abcdef","direction":"incoming","limit":100}}
```

Use the returned `next_cursor` to continue the same query. A graph change invalidates
the cursor; restart the page instead of combining different revisions. Outgoing
queries use the same node ID with `direction:"outgoing"`. Asset IDs are Unity GUIDs,
so renames preserve their identity. Deleted targets remain visible as missing.

`diagnostics` accepts `node`, `limit`, and `offset`. Supply the returned `revision`
for nonzero offsets. It reports unresolved keys, dynamic expressions, ambiguity,
binary serialization, and the coverage of each evidence provider. Every response
includes `graph_status` with progress, errors, queue state, and freshness. Importing,
compilation failure, journal gaps, unfinished work, or unavailable external inputs
must be resolved before treating a scan as current.

Observed Unity dependencies, possible static matches, and manually declared edges
remain distinct. C# analysis follows supported Resources/Addressables calls,
literal and constant composition, and wrapper argument forwarding across files.
It is not a full C# compiler or whole-program proof: dynamic keys, reflection,
unsupported overload/type flow, and generated runtime behavior can remain unknown.
An empty query is **never** proof that deleting an asset is safe.

## External configuration

External sources are read-only and opt-in. There are no application names,
resource-folder exceptions, or game-specific field heuristics in the defaults.
Each source needs an absolute root and explicit relative include patterns:

```json
{
  "action":"set_source",
  "options":{
    "id":"settings",
    "expected_version":0,
    "config":{
      "root":"/absolute/path/to/settings-repository",
      "include":["data/**/*.json","tables/*.csv"],
      "exclude":["data/private/**"],
      "mappings":[{"field":"/items/*/prefab","scheme":"resources"}]
    }
  }
}
```

On Windows use an absolute Windows path. Patterns use forward slashes and
case-sensitive `*`, `**`, and `?` on every OS. Hidden, linked/reparse, hard-linked,
escaping, and non-JSON/CSV inputs are refused. A JSON field is a pointer pattern;
a CSV field is a column name. Schemes are `resources`, `addressables`, `guid`, or
`path`; optional `prefix`, `suffix`, and `mode:"all"` support explicit conventions.
Without mappings, exact catalog strings are possible candidates. Numeric IDs are
not guessed. Inaccessible files cannot reveal cached external references.

Read `sources` before editing an existing source and supply its `version` as
`expected_version`. Disable it with `enabled:false` and that version. Revocation or
a changed grant removes old generated edges immediately. Configuration files are
never modified, executed, or copied into the Unity project.

For a remotely hosted MCP server, paths refer to **the server filesystem**. Its
operator must authorize roots per authenticated user using
`UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS`, a JSON object mapping user IDs to arrays
of absolute directories. A remote tool call cannot grant itself arbitrary server
filesystem access. Normal local stdio/local authenticated HTTP usage does not need
this operator setting. Credential and current-file access are checked again before
returning cached external evidence.

## Authored declarations, wrappers, and exclusions

`declare` writes an explicit reference without editing assets:

```json
{"action":"declare","options":{"id":"runtime-prefab","owner":"runtime:factory","edge":{"target":"asset:0123456789abcdef0123456789abcdef","kind":"runtime_load","evidence":"Selected by the runtime factory"},"expected_version":0}}
```

Use `declarations` to read versions and `remove_declaration` to delete by `id` and
`expected_version`. Deleted declaration versions remain tombstones, so a stale
writer cannot recreate a removed item accidentally.

Explicit wrapper rules supplement automatic analysis:

```json
{"action":"set_rule","options":{"id":"prefab-loader","expected_version":0,"config":{"call":"Game.Assets.LoadPrefab","argument":0,"scheme":"resources","prefix":"Prefabs/"}}}
```

Use `rules` for versions. Set a rule's config to `{"enabled":false}` to disable it.
Rules and changed wrapper definitions invalidate dependent analysis. `policy` and
`set_policy` manage optional `{"exclude":["Assets/Private/**"]}` patterns for this
graph. Policy changes restart generated collection without deleting declarations,
source grants, or wrapper rules. No named application folder is excluded by default.

## Ownership, lifecycle, and recovery

Every database, source grant, rule, declaration, job, and cursor is bound to the
authenticated user, actual Editor process identity, and project identity. Tool
parameters cannot override those identities. Two users or Editors do not consume
each other's change events. The native journal is nondestructive and contains no
user grants. Multiple MCP servers for the same exact scope coordinate through a
renewable SQLite worker lease; stale workers cannot overwrite new evidence.

Normal domain reload preserves the Editor identity and journal. A complete Editor
restart creates a **new isolated graph**, with a fresh census and no implicit copy
of another instance's grants or declarations. Read `sources`, `rules`, `policy`, and
`declarations` before closing an instance if you want to explicitly recreate its
authored setup in a new one. Old database files remain retained; there is no silent
cross-instance recovery or automatic deletion of authored state.

`watch` with `enabled:false` stops the current server's watcher. The next graph
operation can activate it again; this is not a project-wide disable switch.
Server shutdown joins background work and closes owned handles. A crashed server
can resume durable progress after its worker lease expires. Compilation/importing
pauses collection without forcing `AssetDatabase.Refresh`, asset loads for source
analysis, or project writes. Missing journal history triggers a complete census.
`rebuild` clears derived evidence while retaining authored setup.

State uses `%LOCALAPPDATA%/unity-mcp/reference-graphs` on Windows,
`~/Library/Application Support/unity-mcp/reference-graphs` on macOS, and
`$XDG_STATE_HOME/unity-mcp/reference-graphs` (default `~/.local/state`) on Linux.
Operators may set an absolute `UNITY_MCP_REFERENCE_GRAPH_DIR`. Hashed scope names
avoid raw user/project IDs in filenames; the stored scope is verified on open.
No graph files are generated inside project `Assets` folders.

See the measured [storage comparison](development/reference-graph-storage.md),
[analysis coverage and timings](development/reference-graph-analysis.md), and
[collector protocol](development/reference-graph-collector.md) for details.
