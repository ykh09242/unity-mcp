# Bounded MCP response delivery

Native response admission and client transport admission are separate limits. The
existing native workload and complete admitted MCP result graph retain their
64 MiB ceilings, including the existing result-envelope allowance. A result
containing both text and structured content consumes the complete graph budget;
duplicating a native payload does not increase that budget.

The legacy Unity stdio response decoder also checks the shared depth and node
limits before allocating JSON objects. Its 128 MiB framed envelope ceiling is
unchanged. Concurrent decoders reserve proportional raw/text working bytes and
decoded graph estimates in thread-safe budgets: at most `5 * 128 MiB + 4096`
raw/text bytes and the existing 512 MiB graph ceiling. These are working-set
reservations, separate from downstream response delivery ownership; there is no
fixed two-decoder gate. A cancelled async caller cannot release a running
decoder's reservation. The worker checks the original command deadline before
and after parsing; a refused or late response after dispatch retains an unknown
execution outcome and never causes a mutation replay.

Unframed compatibility replies use an incremental byte scanner for strings,
escapes and nested containers, followed by one bounded complete-input decode.
Scalar roots require whitespace or EOF so a fragmented number is not truncated.
Trailing data in the received slab is rejected. As with any persistent unframed
stream, bytes arriving after a complete container/string cannot be distinguished
from a subsequent message without an external delimiter; framed transport avoids
that ambiguity.

The supported Codex client's stdio line and HTTP response limits are 8 MiB.
Ordinary tool results, resource/prompt reads and completed wait-task observations use an
8 MiB result budget minus 32 KiB reserved for the request ID, JSON-RPC envelope and
pinned SDK metadata. The stdio writer additionally checks actual serialized UTF-8
bytes, including the newline, before writing a frame.

Known tool/resource/template/prompt inventory methods, modern `server/discover`
and legacy initialization are also bounded. Oversized inventories return an
explicit MCP error with a retained handle in `error.data.delivery`; the server
does not silently omit catalog entries. The complete authorized inventory is
checked before the SDK's optional deduplication and pagination. The normal
repository catalogs fit this budget. This is not a general HTTP streaming-frame
limiter. Expected visible SDK tool, resource and prompt errors, including validation
errors, are bounded while preserving their MCP error semantics and recovery handles.
Small expected errors and unexpected-exception masking retain the pinned SDK's
behavior. Arbitrary other SDK-generated protocol errors, notifications and third-party
extension handlers outside these known response paths are not covered by the
HTTP adaptation. The exact stdio writer check still applies to their outgoing
frames and reports an unknown operation outcome if a response must be refused.

## Recovering a large result

When an admitted result exceeds the client delivery budget, its response contains
`delivery.state = "retained"` and an opaque `response_id`. This changes delivery;
it does not execute the operation again. The summary preserves the original MCP
error flag, bounded outcome evidence and execution receipt metadata. The complete
admitted result, including its original content, structured content and metadata,
is retained as immutable UTF-8 JSON bytes before the final SDK envelope.

Call the always-visible `read_response` tool with the handle and `offset = 0`. Each call returns at most
256 KiB of original bytes encoded in `data.data_base64`. Decode each page, append
the bytes in offset order and continue using `data.next_offset` until it is null.
Verify the complete bytes against `sha256`, then decode UTF-8 and parse JSON.
Pages may split a multibyte UTF-8 character, so decode UTF-8 after joining bytes.
There is no caller-controlled page size or filesystem path.

Call `read_response` with `release = true` to discard a result explicitly. A read
does not extend its ten-minute monotonic lifetime. Repeated observations of the
same immutable task result reuse an existing handle without extending expiry.
The durable task journal keeps its original result; task cancellation, receipt
handling and native execution are independent of this delivery cache.

## Ownership and capacity

Handles belong to one running FastMCP server, authenticated principal and resolved
Unity project. Legacy protocol handles also belong to the originating MCP session.
Modern sessionless requests use the authenticated principal and project domain.
Remote reads check the current credential lease and require at least the grants
captured with the original tool response. Task observations retain their existing
read-grant requirement. Missing, expired, released and foreign handles all return
`response_not_found` without reading result bytes. Restarting the server invalidates
these process-local handles.

The cache holds at most 256 MiB and 64 results per server, with at most 128 MiB and
16 results per principal. It charges actual encoded bytes only for oversized
results. It adds no admission or concurrency limit to ordinary operations. Live
handles are never evicted to admit another result. Expired bytes are reclaimed on
the next cache operation or server collection; retained bytes remain quota bounded
while idle. Response-owner release and cancellation cannot invalidate another
request's artifact or release a native worker's existing reservation early.

If retention capacity or a safe delivery scope is unavailable, the response says
`delivery.state = "unavailable"` and `retry_safe = false`. Its original operation
outcome remains separate from this delivery failure. A successful mutation is not
reported as failed merely because its full response cannot be retained. Use an
existing execution receipt or native job identifier to observe an uncertain
operation; repeating a mutation to retrieve output is unsafe. This fallback is
explicit and does not promise recovery of unavailable bytes.

The MCP read tool is the retrieval interface. Existing REST/CLI workloads retain
their existing workload limits; a separate CLI invocation cannot claim another
legacy MCP session's delivery handles.
