# Unity bridge performance and compatibility

The MCP client still uses the standard stdio or Streamable HTTP interface. These
optimizations affect the internal Unity bridge and server reads; they do not
change tool parameters or replace MCP JSON-RPC with a private protocol.

## Connection negotiation

The WebSocket server advertises its supported `capabilities` in `welcome`. Unity
includes its supported list in `register`. Only the intersection returned in
`registered.capabilities` enables an extension. A missing list means no
extensions. Old clients retain text results and ordinary state RPCs; old servers
do not activate extensions on a new client. The existing legacy TCP path remains
available.

## Command processing and JSON

The receiver admits commands into a connection-owned queue and resumes reading
control messages. At most 32 commands, including the executing command, can be
owned by one connection. Admission rejects duplicate active IDs and overload.
Deadlines start at receipt, so an expired queued command never executes.

Commands retain their arrival order. If an already-started asynchronous handler
outlives its response deadline, the next command waits for actual completion.
Disconnect releases connection-owned waits and prevents stale sends. It cannot
undo an already-started operation: legacy Unity handlers do not accept a
cancellation token. Commands are not automatically replayed after uncertain
outcomes.

Synchronous results are projected to a JSON token on the Unity main thread and
serialized with their final response envelope. This removes an intermediate
string serialization and parse. Existing asynchronous string-returning tools and
the legacy JSON API retain their compatibility adapters.

## `editor_state_v1`

Unity publishes the existing complete v2 state snapshot, with an epoch, sequence
and observation time:

```json
{"type":"editor_state","epoch":"domain-epoch","sequence":1,"observed_at_unix_ms":1,"state":{}}
```

The example shows the envelope only; `state` must contain a valid complete
snapshot. The publisher sends the first snapshot, changes, and observations
approximately once per second. It retains one in-flight send and one newest
pending snapshot. Unchanged observations reuse the cached state rather than
rebuilding it. All Unity state is observed on the main thread.

The server binds snapshots to the authenticated registered connection. It checks
epoch, sequence, wall time and monotonic receipt age. A duplicate observation
cannot extend freshness. Reload or an epoch change invalidates the slot until a
new registration. Cached reads require both a live connection and a snapshot no
older than two seconds. Disconnect and replacement remove the old slot.

Readiness preflight and refresh barriers use `get_editor_state_authoritative`
and still issue an RPC. The ordinary resource uses a detached cached copy when
fresh, otherwise the existing RPC. Snapshots are bounded independently to
128 KiB encoded, 512 KiB retained, 16 levels and 4,096 nodes, with at most 256
registered slots.

## `large_result_v1`

Results below 256 KiB use the existing text message. Larger results up to 32 MiB
use a text start followed by complete binary WebSocket messages:

```json
{"type":"result_start","id":"00000000-0000-0000-0000-000000000001","total_bytes":262144,"chunk_count":5}
```

Each binary message contains:

| Field | Bytes |
| --- | ---: |
| ASCII `ULR1` | 4 |
| Canonical lowercase command UUID | 36 |
| Byte offset, unsigned big-endian | 4 |
| Payload | Up to 65,492 |

A complete binary message is at most 65,536 bytes. The reassembled UTF-8 payload
is the original complete `command_result` JSON envelope; its command ID must
match the transfer ID. Each frame releases the send lock, allowing pong and
state messages between chunks. This is message multiplexing on one WebSocket,
not a second connection, compression, or a reduction in model input tokens.

The server requires a pending command on the exact connection generation before
allocating a buffer. It validates size, chunk count, offsets, JSON bounds and the
final envelope. Raw buffers and decoded text share the existing per-session,
per-user and global result budgets. Completed result objects remain charged
until their response owner releases them. Aggregate raw assembly is also capped
at 64 MiB. A transfer has a 30-second local deadline, bounded further by the
original command deadline; heartbeat maintenance reclaims stalled transfers.
Cancellation, disconnect and replacement reclaim partial results. Valid late
chunks are ignored without disturbing other commands. Malformed active
transfers close the offending connection; capacity refusals return a bounded
error and leave it usable. Legacy non-UUID IDs retain text fallback.

## Shared reads

On the HTTP/WebSocket bridge, positive `get_test_job.wait_timeout` callers can
share a status fetch for the same transport, authenticated user, registered
Unity session, job and detail flags. A running
snapshot is reused for at most two seconds while long-wait callers remain
active. Immediate reads remain independent. Each caller keeps its own deadline
and receives a detached result. The shared fetch owns its original response
independently of any caller. Each detached copy reserves capacity before it is
created and keeps that reservation until its caller's response is delivered or
cancelled. The final waiter releases shared work and its retained snapshot.

Concurrent batch settings cache misses share one read; each batch dispatch still
executes independently and preserves its command order and `failFast` behavior.
The existing five-second settings cache remains generation-aware. Missing
remote identity or registered connection cannot create a shared cross-user
entry. Shared registries are bounded to 128 identities per event loop; overflow
uses independent reads. The legacy stdio/TCP bridge does not expose a reliable
connection generation to these callers, so it uses fresh reads instead of this
sharing and settings cache. This adds a settings RPC per legacy batch rather
than risking reuse after a same-project reconnect.

## Evidence and further choices

On one Windows host, the production C# transport fixture answered a ping in
15 ms while a one-second asynchronous command was active. A 4,194,434-byte
Unicode result used 65 binary messages and allowed a pong between them. For 30
projections of the same 256 KiB result, measured allocation fell from
142,564,656 to 32,011,056 bytes (77.5%); elapsed time was 87 to 21 ms. These are
controlled fixture results, not live Editor latency guarantees.

Controlled HTTP-bridge server tests reduced 20 simultaneous batch settings reads to one
while retaining all 20 batch dispatches. Twenty long job waits over two polling
rounds used two status RPCs rather than 40. Forty fresh SDK state reads used no
Editor RPCs. Reproduce transport checks with
`tools/tests/fixtures/transport_architecture/RunTransportArchitecture.ps1` and
run the Python regression suites in `Server/tests`.

Changing the server language should follow measurements separating Unity work,
queue time, JSON conversion, MCP handling and wire time. Python, TypeScript,
C#, Go and Rust currently have [Tier 1 official MCP SDKs](https://modelcontextprotocol.io/docs/2026-07-28/sdk).
Python preserves the existing FastMCP behavior and tests. C# is the first
alternative to evaluate for shared contracts and Unity development experience;
Go is a candidate for standalone server deployment. Rust is a candidate for a
measured codec or data-processing hotspot. No cross-language performance claim
has been established by these tests.

A separate control/data WebSocket pair could avoid data backlog on the control
connection, but needs paired authentication, generation ownership and cleanup.
Local Named Pipes or Unix sockets are additional deployment options. They do
not remove Unity main-thread work, JSON volume, or unnecessary polling. Evaluate
them against the optimized persistent connection before adding a new transport.
