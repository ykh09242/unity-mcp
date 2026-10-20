# Resource lifetimes

The server and Editor keep reusable state, but each owner must define when that
state expires. These policies avoid silent eviction of active work.

| Resource | Retention policy | Release or admission behavior |
| --- | --- | --- |
| FastMCP session state | Up to 4,096 entries, 16 MiB of encoded keys and values in aggregate, and 64 KiB per entry | Expiration uses the same Unix clock as the SDK's deadlines. Reads and writes expire elapsed entries; an owned task also sweeps every 30 seconds. Shutdown clears the store. |
| Legacy stdio connections | Up to 64 pooled connections | Discovery must report a target missing for 60 seconds, with no recent use, before an idle generation can retire. Running commands and queued async callers prevent retirement. |
| Dynamic compilations | Up to 256 successful assembly loads per scripting domain | Successful source/reference/compiler identities remain reusable. New compilation is refused when the budget is exhausted. Failed compilation does not consume a slot. |
| Editor object ID metadata | Live identities only | A 10-second Editor callback removes destroyed identities. Reload and quit clear the map. Player builds do not own this reverse lookup map. |
| Asset search snapshots | Existing 60-second pagination lifetime and capacity limits | A one-second callback reclaims expired snapshots without requiring another search. The callback detaches when the cache is empty. |
| UI capture targets | Existing panel/pixel budgets, with 60-second idle retention | A one-second callback releases inactive targets and restores prior panel bindings. Active deferred captures are protected. Detaching the last document releases its unused target. |
| Material instances | One reusable tool-owned instance per renderer slot when exclusive | Repeated assignment, instance color and render-state edits reuse the instance. A material shared with another renderer or slot is cloned before mutation. |
| Generated defaults | Up to 32 live default material entries | An Editor-session owner survives managed domain reloads. ProBuilder vertex-color defaults are reused. Quit destroys only generated, nonpersistent materials. Borrowed built-in resources are preserved. |

The session byte limits count UTF-8 keys and serialized values, not total process
RSS. Capacity rejection preserves existing live values. HTTP request completion
does not delete persistent MCP session state; the SDK's state TTL remains in force.
Connection retirement runs during discovery and admission, without adding a
background port scanner.

## Dynamic compilation

Mono cannot unload an individual dynamically loaded assembly. Removing a managed
cache entry or clearing execution history therefore does not release the assembly.
The shared compilation budget covers `execute_code` and the optional Roslyn runtime
compiler example. The latter caches a successful load before validating the entry
point, so a missing entry point cannot cause the same source to load repeatedly.

`execute_code` returns `compilation_budget_exhausted` with the loaded count and
limit when a new identity cannot be admitted. Existing compiled identities remain
usable. Reference changes can require a new identity and consume another slot.
Prefer structured tools or parameterized helpers for repeated operations. When
appropriate, the operator can deliberately reload the scripting domain; the tool
never triggers a reload automatically.

On Windows, CodeDom stages references beyond `MAX_PATH` in an owned short temporary
directory. It removes those copies after both compilation success and failure.
Original package and project DLLs are not modified.

## Native ownership and Undo

Material ownership is serialized in an Editor-session `ScriptableSingleton`, without
a project file or an asset path. Reuse checks the current renderer slot and other
live renderer references before editing. Single-slot color edits no longer access
`renderer.materials`, which can instantiate every slot.

Undo retains historical objects intentionally. The instance registry drops obsolete
references but does not destroy materials that an Undo record, user script or scene
could still need. Generated defaults survive domain reload so existing renderers
keep valid references. These policies reduce unnecessary allocations; they are not
a promise that Undo or Unity's asset system immediately returns all native memory.

UI cleanup releases only owned render textures and restores a previous binding only
when the panel still uses the owned target. A shared `PanelSettings` remains in use
until its last document or supported panel renderer detaches.

## Regression coverage

Server lifetime tests cover physical expiration, capacity rejection, independent
sessions, connection churn, in-flight work and async waiter handoff. Editor tests
cover idle expiration without a follow-up request, live object handles, more than
64 compiled snippets, admission exhaustion, native material counts, shared slots,
Undo/Redo and render-target restoration.

`DefaultMaterialLifetimeTests.ActualDomainReloadPreservesDefaultAndInstanceOwnership`
is an explicit test that requests an actual domain reload. Run it only in a
controller-owned isolated Unity project, using Unity Test Framework's reload-capable
runner. Ordinary fixture groups must not include this test. The Unity 6.6 entity ID
API requires that Editor version for native validation; compiling the conditional
code on other versions is not a substitute.
