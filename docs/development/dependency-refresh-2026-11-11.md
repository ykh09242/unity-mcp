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

## Website dependency graph

All 13 direct declarations remain at their reviewed current versions, including
Docusaurus 3.10.2, React/DOM 19.3.1 and local search 0.55.3. The only manifest
change raises the selector-parser override from `^7.1.7` to `^7.2.0`. npm 12.2.2
regenerated the lock within existing compatible ranges on Node 26.13.0. Existing
overrides, Node support ranges and dependency lifecycle restrictions remain.

The lock updates 40 existing locations and adds two nested locations, affecting
39 distinct package names; no locations are removed. The nested parse5/entities
names already existed elsewhere in the graph.

| Package or family | Previous lock | Reviewed lock |
| --- | --- | --- |
| postcss-selector-parser / undici | 7.1.7 / 7.30.1 | 7.2.0 / 7.30.2 |
| algoliasearch and matching 5.x clients/requesters | 5.61.0 | 5.64.0 |
| @algolia/abtesting | 1.27.0 | 1.30.0 |
| @algolia/ingestion, @algolia/monitoring | 1.61.0 | 1.64.0 |
| algoliasearch-helper | 3.30.1 | 3.30.2 |
| hast-util-raw | 9.1.0 | 9.2.0 |
| hast-util-raw's nested parse5 / entities | absent | 8.1.1 / 9.0.0 |
| image-size | 2.0.4 | 2.1.0 |
| mdast-util-gfm-autolink-literal | 2.0.1 | 2.1.0 |
| mdast-util-gfm-task-list-item | 2.0.0 | 2.0.1 |
| mdast-util-to-markdown | 2.2.2 | 2.2.3 |
| regexpu-core / regjsparser | 6.5.3 / 0.13.4 | 6.5.4 / 0.14.1 |
| schema-utils / serve-handler | 4.8.2 / 6.1.7 | 4.8.3 / 6.1.8 |
| watchpack | 2.6.3 | 2.6.4 |
| ws (two locations) / ansi-regex (three locations) | 8.22.1 / 6.5.0 | 8.22.2 / 6.6.0 |
| ci-info / colord / lilconfig | 4.4.0 / 2.10.0 / 3.1.4 | 4.5.0 / 2.12.0 / 3.1.5 |
| caniuse-lite / electron-to-chromium | 1.0.30001820 / 1.5.477 | 1.0.30001823 / 1.5.480 |
| vfile-location / vfile-message | 5.0.3 / 4.0.3 | 5.0.4 / 4.0.4 |

The edge review includes optional/peer metadata. Hast-util-raw now requests
parse5 `^8`; its nested parse5 requests entities `^9`, whose Node >=20.19.0
requirement fits the site's supported runtimes. Regexpu requests regjsparser
`^0.14`; selector-parser removes its cssesc edge; Algolia's exact internal edges
advance together. Schema-utils relaxes its optional webpack peer from
`^5.112.0` to `^5.0.0`. Optional dependency edges, peer optionality, lifecycle
script flags and platform constraints are unchanged.

### Website release-note applicability

- [Selector-parser's changelog](https://github.com/postcss/postcss-selector-parser/blob/7591d8d67f42fde79490d5e9c8fac0b8deb43fa2/CHANGELOG.md)
  covers the CPU-resource correction after 7.1.7 and the 7.2.0 escaping/input-limit
  changes. New contracts retain complex selectors, decoded escaped identifiers,
  attribute flags and trailing whitespace, and exercise the explicit input cap.
  Docusaurus does not opt into that cap automatically. The production build
  continues through CSS minification with the existing css-minimizer override.
- [Undici 7.30.2](https://github.com/nodejs/undici/releases/tag/v7.30.2)
  carries six upstream fixes. The graph stays on compatible 7.x; Cheerio
  buffer/stream decoding contracts and generated search indexes pass.
- [Algolia's reviewed release range](https://github.com/algolia/algoliasearch-client-javascript/compare/10b58b56ca70cd99ffb95565ad52ad0464517a77...09270d19cece0cdd959cb257ae7e5108730ab8ea)
  advances its generated client family without changing local-search config.
  Browser autocomplete, full results and result navigation pass.
- [Hast-util-raw's raw-tree API](https://github.com/syntax-tree/hast-util-raw/blob/85b189ddcf339f9aca2b27dbc060634d9281628b/readme.md#rawtree-options)
  reparses authored raw HTML through its new nested parse5. A focused contract
  verifies implicit table-body insertion, entity decoding and inert template
  content in HAST. The docs build and an automatically refreshed development
  table/entity fixture exercise the surrounding pipeline.
- [Regjsparser's reviewed range](https://github.com/jviereck/regjsparser/compare/da648707fa004d6190d638abf876c9a98766acef...1b14f512c37849c604fffe4cc1a2df713ec09821)
  updates malformed-pattern syntax-error handling through regexpu's selected
  0.14 range. This remains inside the Babel build graph; application regex
  policy and browser query parsing are unchanged.

### Website verification and residual limits

Every npm command uses `npx --yes --package npm@12.2.2 npm`, because global npm
is older. Clean `ci --ignore-scripts` and `ls --all` succeed; omitted optional
binaries for other platforms are expected. The final build runs 33 dependency,
braces and UI contracts and three published-route contracts, all passing.

Owned loopback production/development servers provided actual browser checks.
The homepage linked to Install; autocomplete for `play scenario` opened a
full-results page with 49 documents, and a result opened the expected CLI
reference anchor. A development Markdown probe appeared and disappeared without
manual navigation. CSS hot reload applied and removed a temporary heading
outline while preserving an entered search value, establishing a live update
without a full-page reload. Exact original source bytes were restored, and the
owned tabs and servers were closed. Development correctly reports that its
search index requires a production build; search was checked on the production
preview. The transient template was tested for content preservation in HAST;
the browser's inert template content was empty, so no browser template-content
preservation claim is made.

`npm audit --json` exits 1 with five low entries from unchanged deepmerge 4.3.1
and its SVG-tooling dependents; there are no moderate, high or critical entries.
No patched deepmerge release is listed. Its force-fix proposal downgrades
Docusaurus preset-classic to 3.6.3 and is intentionally rejected.
[Deepmerge advisory](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)

The nested parse5 update does not replace every parse5 path. Cheerio and its
stream/adapter consumers retain compatible 7.3.0; no newer released 7.x was
found during review. Local search parses generated HTML with Cheerio at build
time; visitors search generated JSON. Keep authored HTML and plugins trusted;
do not claim every parser path is patched or force parse5 8 without compatibility
evidence. This update does not certify malformed HTML across all retained paths.

Context7 npm and selector-parser lookups succeeded. Its selector index omitted
the new `maxInputLength` API, which was checked against installed official 7.2.0
declarations/implementation. The raw-HTML lookup returned the HAST schema rather
than hast-util-raw's API; the pinned upstream README above supplies that API.
Exact versions and edges come from the regenerated lock and installed manifests.
