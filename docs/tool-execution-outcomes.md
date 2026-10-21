# Tool execution outcomes

MCP tool results preserve native machine `code` values, human `error`/`message`,
`data`, and `hint`. The optional code is omitted when the native response has none;
string and integer codes retain their existing meaning. This does not introduce a
new retry policy or rewrite native codes.

An explicit top-level boolean `success: false` in an application response sets the
MCP `isError` flag. The boundary also recognizes the SDK's declared `result` wrapper
only when its `fastmcp.wrap_result` metadata is true. It does not parse text, search
nested business objects, or interpret strings/numbers as boolean failure. Known
content-only custom handlers carry the flag from their typed response before text
serialization. Structured details and application metadata remain available.

A successful observation of a failed, cancelled, or still-running Unity job remains
a successful tool call: its outer `success` is true and the observed state lives in
`data`. CLI completion waits have a separate process-exit contract described in the
CLI usage guide. A transport's unknown outcome remains distinct from a proven
native refusal. Neither classification nor a client error flag resends a mutation.

Execution receipts retain this same flag before removing internal SDK metadata.
Returning a retained result reproduces the observed failure without rerunning the
handler. Receipt `completed` means a response was observed, including a failed
response; it does not mean the operation succeeded. See
[execution receipts](execution-receipts-ledger.md) for ownership and replay rules.

Modern and legacy SDK clients are covered by protocol tests. A caller using the
FastMCP client may select `raise_on_error=False` to inspect an expected tool error
and its structured details. Protocol-level malformed requests remain separate from
application execution failures, following the
[MCP tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).
