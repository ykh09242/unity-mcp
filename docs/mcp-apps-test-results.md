# Optional test-results view

`view_test_results(job_id)` reads exactly one page from the selected Unity Editor's
canonical `get_test_job` command. It requests up to 100 failed/skipped test details.
The tool always returns ordinary text and structured data. A client that advertises
`io.modelcontextprotocol/ui` with `text/html;profile=mcp-app` may also show the static
`ui://unity-mcp/test-results-v1` resource.

The view accepts completed, matching jobs with available retained results. It shows
the whole-job summary, the delivered page size, whether further details exist, and
native truncation information. Filtering and expandable details operate on this
page only. There is no refresh, polling, paging, test execution, cancellation, export,
or outgoing app tool call. Running, cancelled, missing, unavailable, or invalid jobs
produce an ordinary error response without an additional read.

Remote access requires READ on both the requester and selected Editor plugin.
Test, edit, or run permission alone does not authorize the wrapper. The static HTML
resource requires authenticated READ access and no Editor selection. Only its exact
URI is accepted; query strings, fragments, and alternate routes are rejected.
The HTML contains no job, tenant, or credentials. It loads no network resources.

The iframe bridge negotiates Apps protocol `2026-01-26`, separately from the outer
MCP protocol. It binds a complete tool input to one job before accepting a result,
accepts messages only from its captured parent window, and clears data on failure,
cancellation, timeout, or a `ui/resource-teardown` request. A teardown request gets
a response with the same JSON-RPC id before listeners and timers are detached.

Capability negotiation and fallback must be checked in both modern and legacy MCP
modes. The controlled-host fixture in `Server/tests/fixtures/test_results_host.html`
can inspect the exact packaged HTML without a network server. Controlled-host
results do not establish interoperability with an actual Apps host.

The 21 controlled-host browser cases remain unrun at the user's request
(`UNRUN_USER_DEFERRED`). Actual MCP Apps host interoperability is `UNVERIFIED`.
Server contract checks and genuine-wheel resource checks do not establish either
browser lifecycle behavior or actual-host interoperability.
