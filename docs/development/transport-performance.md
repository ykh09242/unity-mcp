# Unity bridge performance and compatibility

The MCP client still uses the standard stdio or Streamable HTTP interface. These
optimizations affect the internal Unity bridge and server reads; they do not
replace MCP JSON-RPC with a private protocol. Existing tool defaults are
preserved; `read_console` adds an optional response-field selection.

## Connection negotiation

The WebSocket server advertises its supported `capabilities` in `welcome`. Unity
includes its supported list in `register`. Only the intersection returned in
`registered.capabilities` enables an extension. A missing list means no
extensions. Old clients retain text results and ordinary state RPCs; old servers
do not activate extensions on a new client. The existing legacy TCP path remains
available.

### Authenticated stdio bridge

External MCP stdio still runs over the client's subprocess pipes; this product
also has an internal loopback TCP connection from Python to Unity. That internal
connection now requires a v2 `FRAMING=1 AUTH=HMAC-SHA256` handshake. A fresh server
generation, per-connection challenge, client nonce and reciprocal server proof
bind authentication to this connection. Unity verifies the client before
replacing an established connection. Pending unauthenticated peers are limited
to 16 and have a three-second authentication deadline.

The launch credential uses the dedicated `MCPForUnity.Stdio:<generation>` user
credential namespace on Windows. Unix uses an owned private generation directory
and token file with owner/mode/no-symlink checks. Credentials are not included in
the banner or proof messages. This authenticates the local bridge; it does not
encrypt loopback traffic or isolate applications running as the same OS user.

Update the Editor package and its pinned Python server together. Python rejects
old unauthenticated peers by default. `UNITY_MCP_STDIO_ALLOW_LEGACY=1` deliberately
allows a genuinely old v1 banner; an advertised v2 authentication failure can
never trigger fallback. The new Unity bridge always requires authentication.

## Command processing and JSON

The receiver admits commands into a connection-owned queue and resumes reading
control messages. At most 32 commands, including the executing command, can be
owned by one connection. Admission rejects duplicate active IDs and overload.
Deadlines start at receipt, so an expired queued command never executes.

Commands retain their arrival order. If an already-started asynchronous handler
outlives its response deadline, the next command waits for actual completion.
Disconnect releases connection-owned waits and prevents stale sends. Opt-in
asynchronous handlers accept a `CancellationToken`; `batch_execute` checks it
between children and passes it to cooperative handlers. Legacy handlers still
settle before a following mutation starts. Cancellation cannot undo a completed
side effect. Commands are not automatically replayed after uncertain outcomes.

With negotiated `command_cancel_v1`, caller cancellation or an expired server
deadline sends `{"type":"cancel","id":"command-uuid"}` to the captured
connection generation. Old peers receive no new control messages. Duplicate or
completed IDs are harmless, and stale sockets cannot cancel replacement work.
Python releases pending/raw accounting before bounded, shielded cancellation
delivery. This explicit control message is a WebSocket extension; the stdio TCP
path currently propagates disconnection and local deadlines, not same-socket
active cancellation messages.

Synchronous results are projected to a JSON token on the Unity main thread and
serialized with their final response envelope. This removes an intermediate
string serialization and parse. Existing asynchronous string-returning tools and
the legacy JSON API retain their compatibility adapters.

The Python TCP receiver fills a bytearray with `recv_into`, growing it at most
64 KiB ahead of received data. It does not allocate the entire declared frame
before data arrives. Framed results reach the existing decoder without another
full bytes copy; the original frame limits, absolute deadline and EOF checks
still apply. Exact builtin bytes and bytearray JSON inputs need one strict UTF-8
decode, while subclasses retain their validation fallback. Streaming JSON size
checks count ASCII characters directly and encode non-ASCII chunks as needed.
Encoded-size, retained-memory, depth and node limits still apply independently.

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
not a second connection or a reduction in model input tokens.

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

Unity encodes the final JSON string incrementally into bounded UTF-8 frames,
preserving surrogate handling without allocating another full response byte
array. Small/legacy results retain a single text message.

### Optional `large_result_gzip_v1`

Compression requires both `large_result_v1` and `large_result_gzip_v1` in the
registration acknowledgement. Set `UNITY_MCP_RESULT_COMPRESSION=gzip` in the
Unity Editor process environment to opt in; it is disabled by default on local
and remote connections. Restart/relaunch the Editor with the intended environment.
At least 1 MiB of decoded data and at least 10% estimated size savings are
required. A bounded prefix probe skips low-gain data such as base64 previews;
the completed compressed representation must also meet the savings threshold.

```json
{"type":"result_start","id":"00000000-0000-0000-0000-000000000001","total_bytes":1200,"chunk_count":1,"encoding":"gzip","decoded_bytes":1048576}
```

`total_bytes` and frame offsets refer to compressed wire bytes. `decoded_bytes`
is the exact uncompressed JSON length, still capped at 32 MiB. Identity starts
remain unchanged. The decoder writes one gzip member directly into the reserved
decoded buffer, uses bounded output chunks, and rejects bad CRC, truncated,
trailing, multi-member or length-mismatched data. Before allocation it reserves
`5 * decoded_bytes + 4096 + 512 KiB` against the shared result budgets. The
separate 64 MiB assembly cap counts decoded buffers; decompressor working space
is included in the shared reservation. Compression reduces wire traffic, not
the JSON content presented to an MCP client or model.

## Console response projection

Use `read_console(format="json", fields=["type", "message"])` to omit file,
line and stack fields. `type` and `message` are required; `stackTrace` additionally
requires `include_stacktrace=true`. A JSON-encoded list is also accepted.
Projection is restricted to `get` with `json` or `detailed` format. Omitting
`fields` retains the existing response schema. Filtering/pagination still scan
the same native log entries, but only entries in the requested page are split
and formatted. Projection does not add an Editor call.

## Shared reads

The HTTP bridge shares a pending readiness ping only among callers with the
same registered principal, Unity session, socket generation and command epoch.
An ordinary state resource that misses the pushed-state cache can likewise
share a pending raw state RPC; each caller enriches its own detached copy.
These two pools have no completed-result freshness window. A later call starts
a new RPC unless the existing pushed-state cache can serve it.

Authoritative, private and parameterized state reads remain independent and
prevent later ordinary reads from joining an older pending observation.
Mutation admission also separates reads before and after the command, including
console clear and unknown/custom/batch commands. A generation or mutation change
between readiness and dispatch returns a bounded retry response. Independent
fresh state reads can both complete; their state observation epochs govern
sharing rather than rejecting one another. Capacity refusal does not trigger
repeated readiness probes.

On the HTTP/WebSocket bridge, positive `get_test_job.wait_timeout` callers can
share a status fetch for the same transport, authenticated user, registered
Unity session, job and detail flags. A running
snapshot is reused for at most two seconds while long-wait callers remain
active. Immediate reads remain independent. Each caller keeps its own deadline
and receives a detached result. The shared fetch owns its original response
independently of any caller. Each detached copy reserves capacity before it is
created and keeps that reservation through delivery. Cancellation releases it
when delivery is known not to retain the response; ambiguous stdio handoffs use
the conservative lifetime described below. The final waiter releases shared
work and its retained snapshot.

Concurrent batch settings cache misses share one read; each batch dispatch still
executes independently and preserves its command order and `failFast` behavior.
The existing five-second settings cache remains generation-aware. Missing
remote identity or registered connection cannot create a shared cross-user
entry. Shared registries are bounded to 128 identities per event loop; overflow
uses independent reads. Authenticated stdio/TCP connections also expose a
server/connection generation for shared job and batch-settings reads. Missing
authentication, a busy connection gate, a closed socket, or a changed generation
prevents a shared cache hit. Legacy opt-in connections retain independent reads.
Ordinary stdio state reads use at most 128 slots with one-second freshness;
authoritative readiness and refresh reads invalidate and bypass this cache.
No unsolicited state frames are introduced on the TCP stream.

When a shared source has no HTTP transport reservation, a process-wide 256 MiB
fallback budget admits its retained response and each detached copy before
allocation, with at most 1,024 charges. Source expiry and consumer delivery have
independent ownership; one caller cannot release another caller's copy.

For stdio, a producer finishing its SDK queue send does not imply that stdout
has finished writing. A connection-owned delivery registry retains each charged
result until the actual stdout write and flush complete. It preserves integer
and string request IDs and the SDK's stdout descriptor protection. The adapter
targets the verified FastMCP 4.0.11 / MCP 2.3.0 runner and fails clearly for an
unsupported runner or stream shape; dependency upgrades must rerun its real
subprocess and blocked-writer tests.

The default stdout path serializes with the SDK's public JSON-RPC bytes adapter
and writes the payload, one LF and flush in the same worker. It supports partial
writes and retains response ownership through actual worker completion, even
when cancellation is requested repeatedly. A blocked OS pipe can therefore
delay cancellation until the reader drains or disconnects. Explicit text
streams keep the SDK text adapter.

Duplicate active IDs are rejected before tool execution. A duplicate error or
cancellation during an uncertain SDK handoff can make output ownership
ambiguous, so that identity's charges remain until the stdio connection closes.
The registry admits at most 256 identities and retains the aggregate byte
budget. Repeated ambiguous requests can exhaust this bounded capacity and cause
refusals until the client restarts its stdio connection. Normal successful
responses release their charges after flush.

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
measured codec or data-processing hotspot. The representative comparison below
does not establish full product parity or predict real Editor latency.

A separate control/data WebSocket pair could avoid data backlog on the control
connection, but needs paired authentication, generation ownership and cleanup.
Local Named Pipes or Unix sockets are additional deployment options. They do
not remove Unity main-thread work, JSON volume, or unnecessary polling. Evaluate
them against the optimized persistent connection before adding a new transport.

The [owned transport benchmark](../../tools/tests/fixtures/transport_bench/README.md)
compares real MCP stdio/HTTP subprocess paths through the production TCP/WS
routing with deterministic simulated Editor responses. Its v2 harness imports
the selected source's actual `UnityMCP` adapter and response middleware. It
separates measured queue/work/peer encoding from the combined wire/framework
residual and records payload equality, cancellation cleanup, reconnect and
source fingerprints. It excludes real Editor execution and full catalog
registration. A separate resource contract exercises ordinary and authoritative
resource delivery with positive held reservations; its forced overlap is not a
natural latency measurement.

The earlier v1 subset harness, before the actual `UnityMCP` adapter was included,
produced the following client latency medians on Windows 10 with Python 3.14.6,
FastMCP 4.0.11 and MCP 2.3.0. It used 30 warmed calls per workload with no
synthetic delay. These historical measurements cannot serve as a v2 baseline:

| Workload | stdio | Local HTTP |
| --- | ---: | ---: |
| Small console result | 2.63 ms | 4.83 ms |
| State command | 2.93 ms | 5.65 ms |
| 4 MiB console result | 292.64 ms | 117.66 ms |
| Completed test job | 2.72 ms | 3.45 ms |

Both paths returned equal payloads. HTTP negotiated uncompressed chunks, and
its readiness checks add RPCs for the gated console/state tools. Runs with a
requested 2 ms synthetic delay also include Windows timer granularity, so their
larger differences are not estimates of protocol overhead alone. Small calls
favored stdio here; large results favored the complete HTTP path. Repeat the
owned benchmark on the target machine before choosing a transport for latency.

Separate same-output fixtures measured console projection from 9,448 to 6,698
bytes for a 50-entry page and C# sender allocation from 4,936,616 to 138,640 bytes
for roughly 4.86 MB of JSON. The latter excludes the existing JSON string. With
512 KiB/s target pacing, negotiated gzip reduced complete receive/validation
time from 10,020 to 459 ms for repetitive JSON; low-gain base64 data bypassed
compression and showed no material improvement. These component measurements
do not predict end-to-end Editor speedups.

The isolated Editor runner, `tools/unity_editor_transport_qa.py`, snapshots the
package and runs explicitly selected tests against owned endpoints. Its final
Unity 6000.0.69f1 run passed 54 checks, including actual domain reload,
authentication, cancellation, reconnection, large Unicode chunks and console
projection. The dedicated integration fixture requires the runner's child-only
`UNITY_MCP_OWNED_TRANSPORT_TESTS=1`; ordinary Editor runs skip those 13 cases
without starting or stopping a bridge. Existing tests that alter global
preferences are outside this owned suite.

The [transport experiment results](../../tools/experiments/transport/RESULTS.md)
include actual Windows Named Pipe and Unix socket endpoints, paired control/data
streams under forced backpressure, and an official C# MCP SDK subset with schema
and output equivalence checks. All three product migrations are deferred:
stream microbenchmarks exclude Unity/MCP work, and the C# subset has different
binding costs and substantial observed memory retention after large responses.
The probes and measured limits are retained for a future justified decision.
