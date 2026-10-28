# Automatic reference graph

`manage_reference_graph` combines Unity's direct asset dependencies, conservative
C# loader and wrapper analysis, possible serialized GUID fields, explicitly granted
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
preserved. Collection uses ample operator-configured work budgets and durable,
paged progress. Large jobs can resume across attempts; page sizes and journal
retention are separate from collection and storage admission. See
[operator budgets](#operator-budgets) before provisioning unusually large projects.

## Query and inspect uncertainty

```json
{"action":"query","options":{"node":"asset:0123456789abcdef0123456789abcdef","direction":"incoming","limit":100}}
```

Use the returned `next_cursor` to continue the same query. A graph change invalidates
the cursor; restart the page instead of combining different revisions. Outgoing
queries use the same node ID with `direction:"outgoing"`. Asset IDs are Unity GUIDs,
so renames preserve their identity. Deleted targets remain visible as missing.
Replacing an asset's GUID at the same path retires the former catalog identity
and generated source facts in the same transaction. Key-based references are
reanalyzed against the replacement; explicit GUID references and manual
declarations retain their original identities and can remain dangling.

`diagnostics` accepts `node`, `limit`, and `offset`. Supply the returned `revision`
for nonzero offsets. It reports unresolved keys, dynamic expressions, ambiguity,
binary serialization, and the coverage of each evidence provider. Every response
includes `graph_status` with progress, errors, queue state, and freshness. Importing,
compilation failure, Addressables event-observer errors, journal gaps, unfinished
work, or unavailable external inputs must be resolved before treating a scan as current.

`graph_status.native_current` reports the native journal/analysis state. When external
sources are enabled, ordinary cached responses keep `current:false`: validating the
returned files cannot prove that another file has not gained an incoming reference.
`graph_status.providers` includes each source's scan generation, state and
`last_verified_at`. Addressables collection errors appear in provider counts and
per-asset diagnostics, separately from not-installed/not-configured states. An older
collector may leave the assembly-version list empty; this means not reported.

For an explicitly checked first page, add `"consistency":"checked"` to query or
diagnostics options. The request synchronizes this scope and rehashes external
files, then validates the delivered evidence and permissions. A pending/failed scan
returns a retryable conflict. A successful `checked_snapshot` is evidence verified
during that scan, not an atomic snapshot of all files or a safe-delete guarantee.
Use normal revision-bound cursors/offsets for subsequent pages. A checked query can
take as long as the underlying project scan; background `sync` remains preferable
for routine progress and large jobs that should outlive an MCP request.

Observed Unity dependencies, possible static matches, and manually declared edges
remain distinct. C# analysis follows supported Resources/Addressables calls,
literal and constant composition, and wrapper argument forwarding across files.
Supported Addressables calls include asset loads, instantiation and scene loads.
Resources keys follow case-insensitive lookup; Addressables keys and filesystem
paths retain their case. YAML GUID fields are possible references until their
serialized type is verified; strings and comments are excluded.
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
a changed grant atomically removes old generated edges, diagnostics and file caches.
Workers from the previous grant cannot republish them. Configuration files are
never modified, executed, or copied into the Unity project.

Paths refer to **the server filesystem**, including local transports. The server
operator must approve readable roots outside tool input before enabling a source:

- Local stdio and local authenticated HTTP use
  `UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS`, a JSON array of absolute directories.
  For example, set its value to `["/absolute/path/to/settings-repository"]` in the
  MCP server's startup environment. This approval applies to authenticated local
  sessions served by that process, under its OS account.
- Remotely hosted servers continue to use
  `UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS`, a JSON object mapping exact
  authenticated user IDs to arrays of absolute directories. Local approval never
  authorizes a remote user.

Roots default to no access. A source may select an approved directory or its
descendant, subject to the same linked-path and current-access checks. The
collector supplies an opaque project identity, not a trusted server filesystem
root, so the server cannot automatically approve project-contained directories.
Source configurations, evidence and cursors remain isolated by user, Editor and
project; process-level root approval does not copy another scope's sources.

**Migration:** existing local external sources now require explicit startup
approval. Review the directories you intend to expose, configure only those roots,
and restart the MCP server after changing its startup environment. Stored source
configuration alone grants no filesystem authority. Root approval, credentials
and current-file access are checked again during collection and before returning
cached external evidence, so a removed approval cannot authorize stale reads.
Disable a source with `enabled:false` to revoke its graph grant independently.

Unresolved external mapping diagnostics retain field, line and reason while
omitting the unknown value (`key_redacted:true`), including diagnostics stored by
older versions. Resolved asset links retain their normal keys and provenance.

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

## Operator budgets

Configure these values in the **server startup environment**, then restart the
server. Tool requests and plugin identities cannot raise them. Collection and
storage budgets are independent; raising one does not grant capacity in the other.

### Collection attempts

Collection settings accept positive signed 64-bit integers. The deadline covers
all collection/analysis phases of one attempt, including collector waits.

| Environment variable | Default | Scope |
| --- | ---: | --- |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_PAGES` | 1,000,000 | Collector requests per attempt |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_ITEMS` | 50,000,000 | Collected items per attempt |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_BYTES` | 68,719,476,736 (64 GiB) | Serialized response bytes; decoded source bytes also have this aggregate ceiling |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_SECONDS` | 86,400 (24 hours) | Whole attempt |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_SOURCE_BYTES` | 134,217,728 (128 MiB) | One source document |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_DEPENDENCY_ITEMS` | 1,000,000 | Dependencies of one asset |

Collector pages must advance with stable snapshot/revision identity. Exhaustion
returns a recoverable `CollectionBudgetExceeded` error naming the setting.
Partial census checkpoints remain resumable; malformed census snapshots restart
a complete census. Incomplete dependency/source streams never publish a partial
replacement or mark the asset clean: previous evidence remains available and
stale. Retry, narrow the requested work, or have the operator provision larger
budgets and restart. A failed or partial scan is not current evidence.

### Durable state

Every upgraded server sharing a state directory must use the same configuration.
Storage settings accept positive integers; the initial main reservation, capped
by the per-scope maximum, must allow at least 64 KiB.

| Environment variable | Default | Scope |
| --- | ---: | --- |
| `UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPES` | 1,024 | Durable scope reservations per directory |
| `UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_SCOPES` | 128 | Reservations per authenticated principal |
| `UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_BYTES` | 274,877,906,944 (256 GiB) | Directory reservation budget, including ledger allowance |
| `UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_BYTES` | 68,719,476,736 (64 GiB) | Reservation budget per principal |
| `UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPE_BYTES` | 8,589,934,592 (8 GiB) | Newly reserved SQLite main-file capacity per scope |
| `UNITY_MCP_REFERENCE_GRAPH_STORAGE_RESERVATION_BYTES` | 8,388,608 (8 MiB) | Initial main-file reservation, grown geometrically |

Each scope accounts for six times its reserved main capacity plus 1 MiB of
sidecar/lock headroom; the directory ledger accounts for another 33 MiB. This is
admission accounting, not preallocated disk. Count limits bind first at the
default initial reservation. There is no graph-node-count limit.

Scope admission and SQLite main-page limits are enforced across cooperating
processes. WAL/journal/SHM usage is sampled before writes, during SQLite VM work,
and around commit. Normal cache spilling remains enabled. A pinned reader may
delay checkpoints and cause write backpressure: let readers finish, then retry.
SQLite instructions and commit flushes can overshoot between samples. Provision a
filesystem/volume quota for a hard physical storage boundary. This does not bound
SQLite TEMP files, process RSS, older binaries or other applications.

New full-graph snapshots with identical evidence share immutable backing,
including last-page retry payloads, while preserving independent names and IDs.
Deleting one alias keeps evidence used by another; deleting the last releases
SQLite pages for reuse. Existing snapshots and mutation-incident evidence keep
their original semantics. Explicit cleanup releases abandoned query scratch and
staging, without deleting ready history. There is no automatic vacuum or eviction
of saved snapshots, authored declarations, grants or rules.

Reservations survive cancellation, process exit and restart. Crashed admission
can leave an unused reservation. Deleting history or lowering limits does not
automatically shrink reservations; existing databases and saved work are
preserved, including databases above a newly lowered limit. Main-page exhaustion
rolls back the operation; a response may report a larger admitted reservation and
ask for retry. No operation body or uncertain mutation is automatically replayed.
Basic reads remain available, but exploration that writes durable scratch may
still require capacity.

### Recovering capacity

Explicitly delete unneeded snapshots to reuse pages, or raise the applicable
budgets and restart cooperating servers. To reclaim scope reservations through
archival, stop **every graph server using the directory** and back up the complete
directory first. Archive selected scope databases, their sidecars and scope-lock
files outside the state directory, then archive `.storage.sqlite` and its
sidecars. Restart to inventory the remaining SQLite files conservatively. Preserve
the root `.storage.lock` inode; never remove or move live lock or ledger files.
Preserve unknown files and user-authored data. A storage error is not permission
to delete saved work.

See the measured [storage comparison](development/reference-graph-storage.md),
[analysis coverage and timings](development/reference-graph-analysis.md), and
[collector protocol](development/reference-graph-collector.md) for details.
