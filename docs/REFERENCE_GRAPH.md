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

Cold inventory and direct-dependency preparation runs across Editor updates. The
collector shares immutable preparation between matching consumers and checks its
epoch and revision before publication. Canceling one consumer leaves other readers
intact; canceling the last releases unfinished preparation. Native status exposes
bounded progress and measured callback durations. Unity's census calls remain
indivisible, so a small result page does not guarantee a short first response. See
the [collector protocol](development/reference-graph-collector.md#preparation-and-responsiveness)
for the work limits and timing boundaries.

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

`diagnostics` accepts `node`, `limit`, and a returned `cursor`. Continue with
`next_cursor` until it is null; byte admission may shorten a requested page.
The cursor seeks after the last delivered provider/ordinal and is bound to the
node, graph revision and authenticated scope. Reusing a cursor repeats that page;
a changed revision requires restarting. Legacy `offset` access remains supported;
supply the returned `revision` for nonzero offsets, or switch to its `next_cursor`.
It reports unresolved keys, dynamic expressions, ambiguity,
binary serialization, and the coverage of each evidence provider. Every response
includes `graph_status` with progress, errors, queue state, and freshness. Importing,
compilation failure, Addressables event-observer errors, journal gaps, unfinished
work, or unavailable external inputs must be resolved before treating a scan as current.

During initial collection, a cached `query` can return an empty page with
`graph_status.admission_pending` while the collector verifies the inventory and
exclusion rules. Once that check succeeds, `graph_status.partial_static` marks
pages containing only the direct Unity dependencies verified so far. Neither
kind of page establishes that a reference is absent. The response includes a
progress hint until collection is current. Run the query again to see later
results. With path exclusions enabled, `graph_status.catalog_pending` also
withholds cached references while the collector checks imports, moves and
deletions against the current exclusion rules.

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

During automatic polling, the server can track changes in admitted directories
with nonrecursive filesystem watches. It discovers those directories, registers
them, and verifies the source with a complete sweep after registration. An idle
poll skips traversal. For changed files, the next poll checks exact entry names
in the affected directories and their admitted ancestors. An ambiguous spelling
can trigger a full sweep that rehashes admitted files in the affected directory
without recording the excluded filename. When a reported parent cannot be matched,
the sweep rehashes the admitted source. Before reading changed content and publishing
it, the server verifies the exact name, ancestor directories and current ignore files.
A new event for the same path survives acknowledgment of an earlier edit.
An explicit `sync` still schedules a full
sweep, and `force:true` additionally rehashes files with unchanged stamps.
`last_verified_at` records successful full sweeps.

During active polling, a full sweep is due after 60 seconds to recover missed
notifications. A restart, watcher failure, changed source or policy, directory
change, or ignore-file change also triggers full verification. Parking, stopping
a watch, credential retirement, and server shutdown close its handles. Checked
delivery still verifies the current grant, admission and evidence.

Each active scope can track up to eight sources. Each registration covers at
most 128 admitted directories and 8,192 observed entries. Its coalesced journal
holds at most 512 paths and 128 KiB of UTF-8 path names. Larger sources continue
using complete sweeps. The filesystem backend can accumulate a batch before it
yields to Python. These limits bound registrations and the durable queue, while
the backend's pre-yield memory use during an event flood has no documented bound.

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

Normal domain reload preserves the Editor identity and journal. After a complete
Editor restart, the new graph checks the current inventory and can reuse static
evidence from a previous Editor for the same user and project. It compares each
asset's current identity, file stamp and imported dependency hash, and checks the
dependency set against the current inventory and exclusions. Changed inputs are
collected again. Source analysis also has to match the parser version and, for
C#, the current compilation context.

The new session enforces `.gitignore` and preserves attributable graph exclusions.
If it cannot safely recover a previous policy, `status` and `policy` expose the
required policy choice. Read the version and use `set_policy` to supply the
intended exclusions before collecting. A provisional project identity cannot
authorize reuse of an earlier Editor's static evidence. External source grants,
rules, manual declarations, runtime captures, snapshots and mutation receipts
belong to their original Editor. Use `export_config` and `import_config` to
transfer authored configuration explicitly.

The storage ledger records a project digest after workspace identity is verified.
Known foreign projects are skipped before their databases are opened. Legacy
entries retain an unknown project marker: missing, corrupt, or unreadable prior
policy candidates leave the new scope policy-pending, while `status`, `policy`,
and `set_policy` remain available. Explicit policy recovery does not mark graph
admission complete. Prior files are preserved, and failures opening the current
scope still propagate.

`watch` with `enabled:false` stops the current server's watcher. The next graph
operation can activate it again; this is not a project-wide disable switch.
Server shutdown joins background work and closes owned handles. A crashed server
can resume durable progress after its worker lease expires. Compilation/importing
pauses collection without forcing `AssetDatabase.Refresh`, asset loads for source
analysis, or project writes. Missing journal history triggers a complete census.
`rebuild` clears derived evidence while retaining authored setup.

Warm runtime, code-analysis, and external-source catalogs apply changed GUIDs
and removal tombstones rather than reloading every asset payload. The durable
identity journal retains at most 128 revisions and 8,192 GUID records; it stores
no duplicate payloads. Missing or evicted history, policy resets, cold caches,
and upgrades fall back to a complete catalog load. Cache updates retain shared
key membership and exclusion/provider status, and publication still checks the
catalog revision. Saved snapshots and authored configuration are unaffected.

Public `cleanup` also releases the workspace's runtime key and admission caches.
It waits for current cache readers without blocking the server event loop. The
next lookup rebuilds those caches from durable evidence; cleanup does not delete
captured evidence or authored configuration.

Code analysis keeps the most recent 8,192 document changes in a separate journal
of owner IDs. A warm pass loads changed C# facts, rebinds affected callers, and
pages the durable queue of owners awaiting publication. Deleted documents and
unavailable inputs lose their bindings. An edit can affect many callers when
they share a declaration. On restart, cache eviction, rule changes, or a journal
gap, the server rebuilds the C# index in pages. The worker checks document and
catalog generations and the rules revision before completing analysis.

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
| `UNITY_MCP_REFERENCE_GRAPH_MAX_ITEMS` | 50,000,000 | Collected items, external directory entries (including nonmatches), JSON nodes, and CSV headers, rows and fields examined per attempt |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_BYTES` | 68,719,476,736 (64 GiB) | Serialized response bytes; decoded source bytes also have this aggregate ceiling |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_SECONDS` | 86,400 (24 hours) | Whole attempt |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_SOURCE_BYTES` | 134,217,728 (128 MiB) | One source document |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_DEPENDENCY_ITEMS` | 1,000,000 | Dependencies of one asset |
| `UNITY_MCP_REFERENCE_GRAPH_MAX_DIRECTORY_ENTRIES` | 100,000 | External directory entries and pending directories retained for deterministic enumeration |

Collector pages must advance with stable snapshot/revision identity. Exhaustion
returns a recoverable `CollectionBudgetExceeded` error naming the setting.
Partial census checkpoints remain resumable; malformed census snapshots restart
a complete census. Incomplete dependency/source streams never publish a partial
replacement or mark the asset clean: previous evidence remains available and
stale. Retry, narrow the requested work, or have the operator provision larger
budgets and restart. A failed or partial scan is not current evidence.
External JSON/CSV documents and external ignore-rule files use these same
per-document and per-attempt decoded-byte budgets. Size checks and bounded reads
precede decoding. Exhausting an external scan retains its previous evidence as
stale until a successful retry verifies that source again.

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
budgets and restart cooperating servers. The local maintenance module can reclaim
one selected scope reservation through explicit archival. It is an **offline
operation**: stop every graph server using the directory, including older binaries,
before inspection or archival. `--all-servers-stopped` is the operator's assertion
of that prerequisite, not a switch that stops processes. Back up the directory
before maintenance. Use the Python environment containing the upgraded server:

```sh
python -m services.reference_graph.maintenance list --directory /absolute/state --all-servers-stopped
python -m services.reference_graph.maintenance inspect --directory /absolute/state --scope <64-character-scope-digest> --destination /absolute/archives/selected-scope --all-servers-stopped
python -m services.reference_graph.maintenance archive --directory /absolute/state --scope <64-character-scope-digest> --destination /absolute/archives/selected-scope --all-servers-stopped --apply
```

On Windows use absolute Windows paths and quote paths containing spaces. The
destination's parent must exist; the selected archive directory must be new and
outside the state directory. `list` returns at most 50 scope reservations, with
`next_after` for the next `--after` page; `--limit` accepts 1 through 100. Select
the exact lowercase scope digest from that inventory. Unknown files are not
eligible. `inspect` and `archive` without `--apply` are dry runs. They report the
scope, principal/project digests, known-file sizes and SHA-256 checksums, reserved
capacity, evidence/history counts, policy version/exclusion count, and the number
of retained scopes referencing that policy. Stored document contents, source
configuration and raw user identities are not printed. Lock files may be created
by inspection, but graph data and reservations are not changed. Results are JSON;
failure returns a nonzero exit status.

Upgraded servers hold a directory ownership lock while any graph handle remains
alive. Maintenance serializes with new openers and refuses a live ownership lock,
selected scope writer/policy lock, or unexpired worker lease. It does not infer
inactivity from file age or expired worker leases alone. Older binaries cannot
participate in lifetime ownership, so stopping all servers remains mandatory.
The upgraded reservation ledger uses version 3; older servers refuse that ledger.
Initialize/upgrade the directory with the current server before using maintenance.

Applying the command copies only the selected database and its present `-wal`,
`-shm`, and `-journal` sidecars. It flushes and verifies every copy before removing
any original, then atomically records an archive receipt and releases that scope's
reservation. `archive.json` retains the inventory and checksums beside the complete
database evidence, policies, authored setup, snapshots and mutation history.
The ledger and all stable lock files stay in place; retained scopes, unknown files
and user-authored files are preserved. Archival requires extra disk space for the
verified copy. There is no automatic eviction, vacuum, history deletion, or bulk
ledger rebuild.

An interrupted operation leaves `.archive-pending.json` in the state directory.
New graph handles refuse to open while it exists. Keep all servers stopped and
repeat the **same archive command**, scope and destination; omit `--apply` first
to inspect `recovery_required`, then use `--apply` to resume. Completed copies are
verified and reused, and an already committed ledger release is not applied twice.
Copy/permission/ledger failures retain the reservation until recovery completes.
Conflicting or changed copies are refused and require operator investigation;
never remove the pending marker, ledger, or locks to bypass recovery. Unknown files
and incomplete metadata fragments remain preserved. File contents are flushed;
directory entries are also synced where the platform supports it. This is process
interruption recovery, not a substitute for backups against disk or power loss.

Archived scopes cannot be reopened under the same Editor identity. The ledger
keeps a policy-candidate tombstone without consuming a scope reservation. A future
Editor may require the existing explicit `set_policy` recovery flow before
collection, so archival never silently drops prior exclusions. Retained Editors
keep their policies and history. Archive restoration/import is not provided by
this command: preserve the archive intact and use `export_config`/`import_config`
before archival when authored setup must transfer to another Editor. A storage
error is not permission to delete saved work.

### Retained native capture files

Editor runtime captures use a separate `MCPForUnity/ReferenceRuntime` directory
beneath .NET's `LocalApplicationData` location (normally `%LOCALAPPDATA%` on
Windows). They are outside the graph SQLite budget. An Editor restart preserves
old files but does not grant the new Editor access through normal graph actions.
The maintenance module provides explicit, OS-owner-verified recovery for them:

```sh
python -m services.reference_graph.maintenance native-list --directory NATIVE_ROOT --limit 50
python -m services.reference_graph.maintenance native-inspect --directory NATIVE_ROOT --editor EDITOR_ID --capture CAPTURE_ID
python -m services.reference_graph.maintenance native-export --directory NATIVE_ROOT --editor EDITOR_ID --capture CAPTURE_ID --destination NEW_ABSOLUTE_DIRECTORY
python -m services.reference_graph.maintenance native-export --directory NATIVE_ROOT --editor EDITOR_ID --capture CAPTURE_ID --destination NEW_ABSOLUTE_DIRECTORY --apply
python -m services.reference_graph.maintenance native-reclaim --directory NATIVE_ROOT --editor EDITOR_ID --capture CAPTURE_ID
python -m services.reference_graph.maintenance native-reclaim --directory NATIVE_ROOT --editor EDITOR_ID --capture CAPTURE_ID --fingerprint PREVIEW_FINGERPRINT --inspected-at PREVIEW_TIMESTAMP --apply
```

Replace placeholders with the absolute native root and exact IDs returned by
inventory. Quote paths containing spaces. `--limit` accepts 1-100; pass `next_after`
as `--after` for another page. Export and reclaim default to preview. Export copies
raw event and metadata evidence into a new directory outside the native root,
checks the copies and writes `recovery.json`; it preserves the originals and does
not import the capture into another Editor. An interrupted export keeps its
partial destination for inspection; retry with a new destination.

Reclamation requires an idle upgraded capture, verified filesystem identity and
ownership, explicit `--apply`, and the fingerprint and timestamp from a preview no
older than 300 seconds. Changed files or active capture leases are refused.
On Windows, the filesystem owner must match the current user's SID. Group-owned
files, including files created with an elevated Administrators default owner,
remain refused; maintenance never adopts them or rewrites their ownership.
Reclamation does not imply export. Legacy files are labeled `legacy_unverified`:
inspection/export additionally requires `--all-editors-stopped`, and legacy
reclamation is unsupported. That flag asserts that all Editors are stopped; it
does not stop them. No capture is automatically pruned or deleted by age.

Upgraded producers allow 64 MiB of events per capture, a 1 MiB event, 64 KiB of
metadata, 256 captures and 512 MiB of native-root logical file bytes, reserving a
metadata rewrite allowance for each admitted capture. Directory census is bounded
at 4,096 entries. Exhaustion preserves existing bytes and reports `storage_limit`
with `stop_reason`. Existing roots above these limits are preserved and refuse
additional storage. These cooperative limits are not a physical disk quota or a
guarantee for legacy producers.

Temporary root-lock contention queues events in memory without ending the active
capture. The FIFO is bounded at 128 records and 4 MiB, and each retry drains at
most 32 records or 1 MiB with one nonblocking lock attempt. Small observation
bursts share the next Editor update; a full batch triggers a bounded fallback
drain. Each batch checks retained storage under the cross-process root lease and
flushes once. Status exposes `pending_count`, `pending_bytes` and `storage_waiting`.

`runtime_stop` immediately ends observation. If accepted events remain, the
capture reports `finishing` and retains its capture lease until that queue has
settled. Each Editor update or `runtime_poll` drains at most one batch; repeated
stop requests are idempotent. Continue `runtime_poll` while `capture.state` is
`finishing`, including when `has_more` is false: that field describes already
persisted event pages, not pending writes. `runtime_status` is cached and does
not poll the Editor. Successful completion reports `stopped` with no unwritten
events. Persistent contention stays visible as `finishing` until ownership can be
acquired; no root lease is held while waiting between retries.

`runtime_release` explicitly cancels any finishing work and removes that capture.
Domain reload, Editor quit and Play Mode transitions cannot wait across updates.
If the queue fills or a forced close cannot drain it, `storage_backpressure` and
`unwritten_count` make the incomplete capture explicit. Existing event bytes are
preserved on forced close; these diagnostics do not claim the queued events were
saved. A persisted `active` or `finishing` receipt without a live capture owner is
reported as `interrupted` after restart.

Path searches retain protected search evidence in the owning query's SQLite
storage, including evidence outside the selected route. Responses and replay
records carry only a private scope-bound proof marker. Every delivery rechecks
the accumulated proof in count- and byte-bounded batches, with cancellation
checks between batches and the existing source, policy, and revision fences.
Total revalidation work remains linear in accumulated proof; no authorization
grant is cached across requests. Proof consumes the normal scope storage budget
and is reclaimed by `release_query` or bounded cleanup of completed, obsolete,
or abandoned jobs. Existing inline-proof jobs migrate on continuation; snapshots
and authored data are preserved.

See the measured [storage comparison](development/reference-graph-storage.md),
[analysis coverage and timings](development/reference-graph-analysis.md), and
[collector protocol](development/reference-graph-collector.md) for details.
