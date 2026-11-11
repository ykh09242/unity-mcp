# Dependency refresh: 2026-11-11

## Python runtime and formatter

The reviewed runtime advances FastMCP to exactly 4.1.8 while keeping MCP and
mcp-types exactly 2.3.0. Python 3.11 remains the declared minimum. The generated
SDK manifest changes with the lock; optional adapter gates remain exact and
the `fastmcp[tasks]` extra is not enabled.

| Package | Previous lock | Reviewed lock |
| --- | --- | --- |
| fastmcp / fastmcp-slim | 4.1.6 | 4.1.8 |
| click | 8.5.0 | 8.5.1 |
| pydantic | 2.14.2 | 2.14.3 |
| pydantic-core | 2.50.2 | 2.50.4 |
| regex | 2026.9.29 | 2026.11.9 |
| ruff | 0.17.3 | 0.17.4 |
| authlib | 1.9.4 | 1.9.5 |
| cachetools | 7.2.8 | 8.0.0 |
| cyclopts | 5.3.3 | 5.4.0 |
| markdown-it-py | 4.2.0 | 4.3.0 |
| sse-starlette | 3.6.0 | 3.7.0 |
| uncalled-for | 0.4.2 | 0.6.1 |

The lock was regenerated and synchronized with uv 0.13.7. All 13 expected
package version changes resolved; no package names were added or removed,
the resolution markers are unchanged, and lock revision 5 is preserved.
Normal and development dependency edges are unchanged. The
`fastmcp-slim[server]` optional graph adds one edge to the already locked
`jsonschema` package, matching
[the upstream 4.1.8 server requirements](https://raw.githubusercontent.com/PrefectHQ/fastmcp/v4.1.8/fastmcp_slim/pyproject.toml)
and installed package metadata (`jsonschema>=4.20.0; extra == 'server'`).
Artifact hashes and URLs follow the newly resolved releases.
Ruff's exact version agrees across the development requirement, root
`ruff.toml`, formatting runner and formatting guide.

### Release-note applicability

- [FastMCP 4.1.7](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.7)
  changes list middleware messages to typed requests. Existing middleware
  forwards its context to the next handler and reads request-specific fields
  only where applicable. Real modern/legacy listing and authorization contracts
  exercise this path; no middleware compatibility rewrite was needed.
- [FastMCP 4.1.8](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.8)
  fixes transformed mutable defaults, schema hydration, client timeout ownership
  and Tasks edge cases. Added repeated transformed-tool calls in both protocol
  modes verify fresh defaults and unchanged published schemas. Existing
  application request deadlines, Tasks private-hook gates and receipt ownership
  remain authoritative. OAuth proxies, OpenAPI imports and the upstream
  background-worker Tasks implementation are not adopted by this update.
- [Cachetools 8](https://github.com/tkem/cachetools/blob/f4217fa285e120d7b77e04dcbdc6326be55bf4f6/CHANGELOG.rst)
  removes deprecated decorator behavior. The installed py-key-value-aio memory
  backend constructs `TLRUCache` directly and does not use the removed
  `cachedmethod` options or a `None` cache. Added tests exercise public memory
  reads, snapshot isolation, eviction, deletion and collection separation, plus
  the real FastMCP Pydantic state adapter's finite TTL and session-key isolation.
- [SSE-Starlette 3.7](https://github.com/sysid/sse-starlette/releases/tag/v3.7.0)
  bounds interrupted/encoding-failure iterator cleanup to 30 seconds by default.
  Added ASGI tests use a short explicit cleanup budget to prove that stalled
  cleanup is cancelled and the original send/encoding error still propagates.
  Product request and shutdown deadlines are unchanged; real HTTP streaming and
  cancellation tests cover the application boundary.
- [Pydantic 2.14.3](https://github.com/pydantic/pydantic/releases/tag/v2.14.3)
  includes schema/serialization and core fixes. Registration, SDK wire schemas
  and the memory state adapter exercise the application's existing integration;
  no model migration is required.
- [Click 8.5.1](https://github.com/pallets/click/releases/tag/8.5.1) fixes command
  copying, interruption handling and help details. Installed `unity-mcp`,
  `mcp-for-unity` and `fastmcp` help commands all exit successfully; existing CLI
  version and rendering tests retain their contracts. Cyclopts, markdown-it-py
  and uncalled-for changes remain within the upstream CLI dependency tree.
- [Authlib 1.9.5](https://docs.authlib.org/en/stable/upgrades/changelog.html#version-1-9-5)
  carries cryptographic key-confusion fixes; retain the upstream fix without
  adding an authentication integration or weakening existing admission tests.
- [Regex 2026.11.9](https://pypi.org/project/regex/2026.11.9/) includes fuzzy-match
  crash/compile fixes. Existing VERSION0 behavior, operation timeouts, input caps,
  selection budgets and worker lifetime tests remain in place and pass.
- [Ruff 0.17.4](https://github.com/astral-sh/ruff/releases/tag/0.17.4) changes
  diagnostics and formatter internals. All 1,395 eligible authored Python files
  pass formatting checks with the new canonical version; no broad formatting
  rewrite was needed.

### Verification and fixture correction

Focused SDK, regex, CLI, receipt and Tasks tests pass (166 tests), and separate
startup/SDK-manifest/formatter tests pass (62 tests). Six new dependency runtime
checks cover transformed defaults, memory state and SSE cleanup. Correctness
lint and changed-source formatting pass. After the synthetic close-handshake
correction below, the full isolated HTTP child suite passes 2,723 tests with
two skips and zero failures/errors; all 52 tests in the three affected HTTP
files also pass in a focused run. These runtime observations are
separate from successful resolution and installation; Python 3.11 execution
remains a CI responsibility because the local verification interpreter is 3.14.

The first full isolated HTTP run exposed an incomplete synthetic-peer close
handshake. After asserting the server's rejection, the test awaited command
cleanup before TestClient sent `websocket.disconnect`. Observed socket state
showed the send side closed while the receive side and command future remained
open. Explicitly acknowledging the server close completed that cleanup
immediately. The same initial failure reproduced with the original locked
dependency set in an isolated environment on the same interpreter.

Three rejection tests now assert close code 4400, send the synthetic client's
normal close acknowledgement, then await cleanup and retain their isolation
and allocation assertions. Production heartbeat cadence, housekeeping, request
deadlines and close behavior are unchanged. An intermediate heartbeat-interval
change exposed the pending disconnect instead of fixing it and was removed.
