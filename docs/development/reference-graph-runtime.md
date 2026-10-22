# Opt-in runtime reference evidence

Runtime observations complement static references with loads that actually pass
through instrumented wrappers. They are historical observations, not an unused
asset or memory leak proof. A dynamic key without an observed asset remains
unresolved; it is never converted into a guessed static edge.

## Instrument an existing wrapper

The package supplies `MCPForUnity.Runtime.ReferenceGraphRuntime`. It requires no
driver, compiler, additional package or runtime service. Hooks do not intercept
Unity APIs automatically and do not own or release resources.

```csharp
var asset = Resources.Load<UnityEngine.Object>(key);
ReferenceGraphRuntime.Load("resources", key, asset, "Assets/Scripts/MyLoader.cs");
```

For an Addressables or custom wrapper, report a successful acquisition after the
existing operation completes, and report release after the existing release:

```csharp
string observation = ReferenceGraphRuntime.Acquire("addressables", key, asset);
// Keep the observation token with the wrapper's own resource handle.
// Run the wrapper's actual resource release first.
ReferenceGraphRuntime.Release(observation);
```

The token is a scalar observation identity, not an Addressables handle. Balanced
events do not prove correct resource ownership; missing releases can also mean
incomplete instrumentation or a capture ending before the release. Unmatched
releases remain visible. Addressables itself is optional.

When disabled, calls take a subscription check and allocate no per-call token or
event storage. Callers should guard expensive key construction with `Enabled`.
The opt-in Editor sink resolves asset GUIDs on the main thread; background hooks
record scalar keys without calling Unity object APIs. No asset object is retained
after a callback. These captures currently cover the connected Editor and its
Play Mode, not a standalone Player transport.

## Capture and read in pages

Use `manage_reference_graph` with these actions and `options`:

| Action | Options | Behavior |
| --- | --- | --- |
| `runtime_start` | optional `id` | Create a capture, or recover the existing capture's status after a lost response. |
| `runtime_sessions` | `after`, `limit` | Enumerate this scope's captures. |
| `runtime_poll` | `id`, `limit` | Import the next native page into SQLite. |
| `runtime_status` | `id` | Read persisted progress without consuming events. |
| `runtime_stop` | `id`, `limit` | Stop native observation and import one page. Continue polling while `has_more` is true. |
| `runtime_events` | `id`, numeric `after`, `limit` | Read imported events. |
| `runtime_handles` | `id`, string `after`, `limit` | Read observed acquisition/release balances. |
| `runtime_release` | `id` | Stop and remove this capture's native spool and derived graph evidence. |

Page sizes are 1-1000 records; there is no total event, session age or capture-count
cap. Polling is explicit, with no permanent per-capture server task. A cancelled
poll keeps its durable byte cursor; retrying cannot duplicate events or balances.
Status counters are maintained transactionally rather than recounting all events.

Captures use separate buffered files and are replayed page by page. Runtime
provenance includes the capture, runtime session, build context, sequence, original
owner path and observed target path. Query/explain/impact/snapshot evidence retains
the `runtime_observed` or `runtime_unresolved` confidence and historical context.
The graph's normal static refresh does not reinterpret runtime evidence.
Rule changes and static rebuilds preserve these observations rather than queuing
them for static analysis.

## Isolation, lifecycle and limits of evidence

SQLite ownership is the authenticated user plus actual Editor and project. Native
spool files live under the OS user's local application-data directory, beneath
`MCPForUnity/ReferenceRuntime/<editor-instance>/`. An unguessable native credential
is held only in that scope's SQLite state and internal native requests; public
capture IDs are insufficient to address another scope's native files. Normal OS
user account permissions are the filesystem boundary; processes running as the
same OS account are not isolated from one another by filesystem permissions.

The store and native bridge recheck their existing scope/credential rules. Current
collection exclusions apply at ingestion and again when delivering historical
events, handles and graph evidence. Removing a capture also invalidates graph
snapshots that require its source. Authored declarations are preserved.

Independent consumers have independent files and can stop separately. Domain
reload, Editor quit and Play Mode transitions close subscriptions and writers.
Captures overlapping in time share a runtime context; after all
consumers stop, the next capture conservatively starts a new context, including
when Play Mode disables domain reload. No lifecycle observer remains while idle.
Existing records remain readable
until explicitly released; no silent age-based deletion removes large sessions.
An interrupted process can leave an incomplete final record, which is reported
as an error rather than silently accepted. Observer I/O failures stop that sink
and expose its error without throwing into gameplay or retaining resource objects.
Explicit capture writes have I/O/serialization cost; enable them for targeted
diagnostics and release completed histories when no longer needed.
