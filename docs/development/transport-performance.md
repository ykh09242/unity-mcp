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

The response-size visitor checks each value's exact builtin type once before
the compatibility path. Subclasses, Pydantic models, URL objects and custom
serialization hooks retain their existing behavior and per-visit accounting.
This removes repeated type checks in dense object trees; a large single string
still incurs its encoded-size work.

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

An event loop creates its weak shared-read registry only on its first use.
A successful explicit Editor lookup validates the selected session directly,
without copying the full session listing. Remote ownership checks, unknown
targets, automatic selection and replacement detection retain their existing
paths.

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

The default stdin adapter bounds a JSON-RPC line to 64 MiB before UTF-8 decoding
or JSON parsing. It reads at most 64 KiB at a time and stops at the first excess
byte, including when the sender never supplies a newline. Line delimiters are
excluded from the limit. Oversized input closes the connection without trying
to parse a request ID or drain an unbounded line. UTF-8 replacement, universal
newlines, EOF handling and protected input/output descriptors remain compatible
with the pinned SDK. Explicit text streams are checked before parsing, but
their producer owns the prior text allocation. A cancelled blocked stdin worker
is awaited until it actually settles, so cancellation may wait for input or EOF.

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

### Same-protocol before/after measurements

The v2 harness compared product revision `789a815b` with `cf2f2232`, using the
fixed harness from `b6186fb5` on Windows 10, Python 3.14.6, FastMCP 4.0.11 and
MCP 2.3.0. Each profile used three alternating baseline/candidate rounds,
30 warmed samples per workload after three warmups, a 4 MiB large payload and
zero requested synthetic delay. Each cell shows baseline to candidate in
milliseconds: the median of the three per-round p50 values, calculated
separately for each revision. These are descriptive observations, not pooled
90-sample percentiles or statistical significance claims.

| Path / workload | Sequential, before → after | Two concurrent calls, before → after |
| --- | ---: | ---: |
| stdio / small | 2.512 → 2.386 | 3.729 → 3.867 |
| stdio / state | 3.372 → 3.235 | 5.981 → 5.717 |
| stdio / 4 MiB | 319.809 → 298.379 | 378.588 → 321.854 |
| stdio / job | 3.081 → 2.521 | 4.044 → 3.706 |
| HTTP / small | 3.865 → 3.873 | 6.650 → 7.029 |
| HTTP / state | 5.688 → 5.746 | 9.317 → 9.256 |
| HTTP / 4 MiB | 125.406 → 129.320 | 158.534 → 146.726 |
| HTTP / job | 3.143 → 3.163 | 6.013 → 5.764 |

The large stdio response took 6.7% less time sequentially and 15.0% less with
two concurrent callers; the latter HTTP response took 7.4% less. Some medians
were higher: sequential HTTP rose 0.2–3.1%, concurrent small stdio rose 3.7%,
and concurrent small HTTP rose 5.7% (0.379 ms). This does not establish that
every workload improved or predict actual Editor wall time.

Every round preserved payloads, public-call counts and source fingerprints.
HTTP readiness pings before the separate partial-transfer probe stayed at 105
for sequential calls and fell from 105 to 75 for concurrent calls. Separate
held-body resource tests reduced two overlapping ordinary state/ping pairs to
one while keeping two authoritative pairs. Natural timing runs used neither
the forced cohort gate nor the resource contract. Cancellation and late-frame
checks observed positive held reservations followed by zero; these counters
are not process RSS or continuous allocation peaks.

### Follow-up runtime and validation improvements

Product revision `1229a17f` reduces redundant JSON type checks, creates shared
read registries lazily, avoids a full session listing for successful explicit
Editor selection, and bounds stdio input before decoding. The baseline for this
follow-up is `d28805b8`, which already includes the previous transport work.

On the same Windows host, the actual response-size visitor produced these
component medians. Each cell is baseline to product; each runtime was measured
once with 11 samples per fixture. Small objects use 1,000 calls per sample;
dense and scalar fixtures use one. All 166 differential cases per runtime
preserved admission, accounting and serialization-hook behavior.

| Fixture | Python 3.14.6 | Python 3.11.15 |
| --- | ---: | ---: |
| Small JSON | 9.541 → 8.279 µs | 8.578 → 7.273 µs |
| 14,000 dense rows | 100.551 → 88.911 ms | 94.307 → 82.340 ms |
| 8 MiB ASCII scalar | 20.234 → 20.128 ms | 20.423 → 20.294 ms |

Small-object inspection took 13.2–15.2% less time and dense-object inspection
took 11.6–12.7% less. The scalar result shows no material gain. These are
component timings, not end-to-end Editor latency. Allocation peaks were measured
separately from timing, excluding the pre-existing inputs and process RSS.

Deterministic operation-count tests reduced weak-registry creation from 20 to
one for 20 simultaneous leases. Twenty successful explicit Editor lookups
eliminated all 20 session-list copies while retaining 20 fresh target lookups.
Those counts do not establish a whole-request speedup. Remote identity checks,
unknown targets, automatic selection and connection-generation checks remain.

The paired benchmark now requires explicit baseline and candidate labels.
Those labels are caller assertions; source hashes and separate Git verification
establish provenance. Natural concurrent captures retain each version's own
valid readiness-count range. Cross-version nonincrease is required for the
separate cohort-gated contract, and declared sharing downgrades fail in either
mode. A forced-overlap resource contract separately verifies actual sharing;
natural request counts alone do not prove it.

Fork beta pushes now invoke the full Python validation workflow once through
`Fork Beta Tool Tests`. Its two Python runtimes and three OS bootstrap jobs are
unchanged. This eliminates the duplicate five-job set previously invoked by
Beta Release as well; it does not imply a halved parallel CI completion time.
Upstream release and publication policies are unchanged.

A separate end-to-end comparison used the common harness from `b2c04360`,
Python 3.14.6 and the same pinned SDKs. Each profile ran three alternating
baseline/candidate rounds, 30 warmed samples per workload after three warmups,
4 MiB large responses and zero requested synthetic delay. Each cell is the
median of three run p50 values, computed independently for each version.

| Path / workload | Sequential, before → after (ms) | Two concurrent calls, before → after (ms) |
| --- | ---: | ---: |
| stdio / small | 2.467 → 2.432 | 3.645 → 3.713 |
| stdio / state | 3.398 → 3.174 | 5.773 → 5.657 |
| stdio / 4 MiB | 279.690 → 279.689 | 322.594 → 325.743 |
| stdio / job | 2.524 → 2.550 | 3.922 → 3.779 |
| HTTP / small | 3.846 → 3.898 | 6.640 → 6.654 |
| HTTP / state | 5.783 → 5.606 | 9.123 → 10.032 |
| HTTP / 4 MiB | 114.887 → 121.956 | 157.217 → 155.792 |
| HTTP / job | 3.193 → 3.236 | 6.061 → 5.973 |

These measurements do not establish an overall MCP speedup. Eight of the 16
medians were higher: the largest increases were concurrent HTTP state (10.0%)
and sequential large HTTP responses (6.2%). Sequential large stdio was
essentially unchanged. Tail observations also matter: the median of run p99s
increased 15.3% for sequential HTTP jobs, 14.3% for concurrent HTTP state and
11.7% for concurrent large HTTP responses. These are descriptive results from
three rounds, not pooled percentiles, statistical significance or a causal
attribution to an individual function. Real Editor execution is excluded.

Payloads, schemas, explicit MCP calls and source fingerprints matched.
Sequential readiness pings were 105 in every round; both concurrent revisions
recorded 76, 75 and 75. A separate gated resource probe confirmed that both
versions already share ordinary reads (one state RPC and one readiness ping),
while authoritative reads remain private (two of each). Each held 310,890 bytes
of accounted ownership before releasing to zero; partial-transfer late-frame
cleanup and reconnect checks also passed. These snapshots are not continuous
allocation peaks or RSS. Immutable source checks covered 165 selected files per
revision and all 161 product Python files captured by the benchmark.

### Permission lookup, reconnect and optional diagnostics

Product revision `ea52dd42`, compared with `6659ab3f`, removes two remaining
ASCII sizing copies, checks selected remote tool membership without building a
session/tool catalog, and wakes missing-session waiters when the registry changes.
Permission checks retain the principal, session, socket and generation boundary.
Tool disabling takes effect immediately. Resource and listing requests do not
capture a tool permission identity, and explicit selectors retain their existing
fresh selection validation. Reconnect notification preserves the absolute
deadline, independent cancellation and server shutdown/replacement behavior.

The actual ASCII input scanner produced these component medians on the same
Windows host. Each runtime was measured once, with 11 untraced elapsed-time
samples per fixture. Small inputs use 500 calls per sample; larger inputs use 11.
All 398 differential comparisons per runtime preserved results and accounting.

| Raw ASCII input | Python 3.14.6, before → after | Python 3.11.15, before → after |
| --- | ---: | ---: |
| 64 characters | 1.197 → 0.699 µs | 1.634 → 0.620 µs |
| 128 KiB | 14.300 → 7.036 µs | 12.736 → 7.064 µs |
| 8 MiB | 748.491 → 403.982 µs | 766.245 → 414.782 µs |

The 8 MiB scanner took about 46% less time. Separately traced temporary peaks
for the 128 KiB and 8 MiB ASCII fixtures fell from 131,762 to 200 bytes on 3.14
and from 131,818 to 320 bytes on 3.11. These peaks exclude existing inputs and
are not process RSS. Small Unicode and string-subclass inputs were 0.014–0.051 µs
slower. A larger Unicode fixture (131,070 characters, 436,902 quoted UTF-8 bytes)
was essentially unchanged. Whole-document JSON sizing showed no material time
or peak improvement: all four fixtures stayed within 1% in elapsed time, and
some 3.14 peaks rose by 64 bytes. Removing a copy does not by itself prove a
whole-request or peak-memory improvement.

A separate middleware/registry benchmark used three alternating baseline and
candidate rounds per runtime, 500 uninstrumented calls per fixture after five
warmups. Each cell below is the median of the three run medians, not a pooled
1,500-sample percentile. Every session in these owned fixtures belongs to the
same synthetic principal, with a persisted selected instance and no inline
selector. HTTP, SDK argument/result conversion, IPC and actual Unity work are
excluded.

| Sessions / tools per session | Python 3.14.6, before → after (ms) | Python 3.11.15, before → after (ms) |
| --- | ---: | ---: |
| 1 / 16 | 0.0190 → 0.0155 | 0.0188 → 0.0160 |
| 4 / 64 | 0.0331 → 0.0159 | 0.0303 → 0.0164 |
| 32 / 256 | 0.1290 → 0.0165 | 0.1155 → 0.0166 |

A separate 20-call operation count eliminated 20 catalog listings and reduced
hash lookups from 40 to 20, retaining 20 session reads and adding 20 atomic
membership checks. Counting wrappers were removed before timing. An inline
selector still requires its existing catalog validation; the optimization
removes the additional permission catalog. List output remains complete.

In a controlled reconnect seam, registration occurs immediately after the
first missing-session lookup. The median of three run medians (12 observations
each) for registration-to-resume fell from 252.887 to 0.011 ms on 3.14 and
253.115 to 0.015 ms on 3.11. This demonstrates removal of the remaining polling
interval in that scenario; it excludes Editor reload and registration work and
does not predict the average saving for arbitrary registration timing. Connected
lookups were also recorded, but baseline observations followed a polling wait
while candidate observations ran immediately after notification, so their
scheduler/cache conditions do not establish a standalone normal-path speedup.

The common harness now offers `--diagnostic`, OFF by default. It records actual
product and pinned SDK boundaries plus whole-process batch CPU in a bounded,
separate sidecar. It adds eight visible diagnostic control calls per mode and
marks its captures so the natural comparator rejects them. Overlapping spans,
observer overhead and CPU clock quantization prevent treating these values as
natural latency or per-request CPU. See the [diagnostic commands and exact
boundaries](../../tools/tests/fixtures/transport_bench/README.md#optional-diagnostic-observation).

The same-product comparison also ran with diagnostics OFF on Python 3.14.6,
MCP 2.3.0 and FastMCP 4.0.11. Each profile alternated baseline/candidate order
over three serial rounds, with 30 warmed observations, three warmups and a
4 MiB large payload per capture. The table reports the median of three run p50s
in milliseconds; these are not pooled 90-sample percentiles or significance
claims. Positive change means the candidate was slower.

| Concurrency | Mode | Workload | Baseline p50 | Candidate p50 | Change |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | stdio | small | 2.385 | 2.421 | +1.50% |
| 1 | stdio | state | 3.187 | 3.190 | +0.09% |
| 1 | stdio | large | 283.217 | 280.751 | −0.87% |
| 1 | stdio | job | 2.552 | 2.516 | −1.41% |
| 1 | HTTP | small | 3.867 | 3.863 | −0.09% |
| 1 | HTTP | state | 5.522 | 5.541 | +0.34% |
| 1 | HTTP | large | 115.839 | 126.732 | +9.40% |
| 1 | HTTP | job | 3.113 | 3.200 | +2.80% |
| 2 | stdio | small | 3.756 | 3.663 | −2.47% |
| 2 | stdio | state | 5.627 | 5.589 | −0.66% |
| 2 | stdio | large | 332.112 | 327.481 | −1.39% |
| 2 | stdio | job | 3.733 | 3.810 | +2.07% |
| 2 | HTTP | small | 6.620 | 6.647 | +0.40% |
| 2 | HTTP | state | 9.632 | 9.129 | −5.22% |
| 2 | HTTP | large | 138.472 | 146.317 | +5.67% |
| 2 | HTTP | job | 5.979 | 6.045 | +1.09% |

Nine of the sixteen candidate medians were higher, including both large HTTP
profiles. These observations do not establish an overall MCP latency improvement.
The remote permission component and registration-after-miss fixture above are
different workloads, so their gains cannot explain these local steady-state
results. Per-round p50/p95/p99, individual samples, source fingerprints and all
slower conditions remain in the Phase10 evidence. With 30 observations per run,
nearest-rank p99 is that run's maximum; independent stage quantiles are not
additive. A separate small gated/resource capture verified sharing, payload
parity, positive bounded reservations and cancellation/final-frame drain to zero;
its deliberately synchronized timing is excluded from this table. Actual Editor
execution, full catalog setup and global selection remain outside this harness.

Separate diagnostic-ON captures used ten warmed observations per workload,
three warmups and one cold call. Their large HTTP p50 was 131.288 to 130.828 ms
at concurrency one and 140.971 to 140.619 ms at concurrency two. The candidate's
nested client SDK/dispatcher p50s were 130.720/130.584 ms and
140.501/140.351 ms, respectively; these include transport/framework waiting,
not just client CPU or wire time. They are overlapping spans, not components
to add. The natural large-HTTP increase was not reproduced in these observed
runs, but diagnostic overhead and a single run per condition prevent using
them to dismiss or explain that increase. Its cause remains unresolved.
The natural results above remain the before/after evidence.

### Earlier cross-protocol measurements

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
