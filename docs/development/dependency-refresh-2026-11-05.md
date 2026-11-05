# Dependency refresh, November 5, 2026

## Review scope

The refresh checks the server's runtime, development and build dependencies,
website manifests and lockfile, GitHub Actions, packaging tools and Unity package
profiles against official registries. Release notes cover the intervening
published versions. When an upstream publishes only tags or generated package
artifacts, the review uses its commit comparison or published package diff.

The server lock updates 21 packages. The website lock updates 76 distinct package
names across 86 existing installation locations and adds two nested resolutions.
FastMCP and its slim distribution move together to 4.1.6. Pydantic 2.14.2 requires
exactly pydantic-core 2.50.2, which determines the resolved core version.

## Applied changes

- FastMCP 4.1.5 fixes JSON Schema constant/enum equality, tool definitions and
  mounted template query arguments. Version 4.1.6 fixes retained ClientGroup
  routing. Regenerate the reviewed SDK manifest and validate scene inputs, Tasks,
  skills and execution receipts against the exact new version.
  [4.1.5](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.5),
  [4.1.6](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.6)
- Pydantic 2.14.2 includes two security fixes and validation corrections. Raise
  the runtime floor alongside its matching core. Authlib and joserfc receive
  their published fixes through the lockfile.
  [Pydantic](https://github.com/pydantic/pydantic/releases/tag/v2.14.2),
  [Authlib changelog](https://docs.authlib.org/en/stable/upgrades/changelog.html),
  [joserfc](https://github.com/authlib/joserfc/releases/tag/1.8.0)
- Declare AnyIO directly because the server imports its task groups, streams and
  cancellation scopes. Version 4.16.0 includes cancellation and socket-lifecycle
  fixes. Give plugin retirement its own cancellation scope. Close scene-test
  receive streams in their reader task, after the sending side finishes.
  [AnyIO](https://github.com/agronholm/anyio/releases/tag/4.16.0)
- Adopt pytest 9.2.0 with pytest-asyncio 1.4.1.post1. Make the fixture event-loop
  scope explicit and pass the real pytest configuration to isolated worker
  probes, so warnings-as-errors also applies to their subprocesses.
  [pytest](https://github.com/pytest-dev/pytest/releases/tag/9.2.0),
  [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio/releases/tag/v1.4.1.post1)
- Raise the website's npm floor to 12.2.2 and selector-parser override to 7.1.7.
  Refresh the resolved Babel, Markdown, CSS, webpack and search packages. Preserve
  separate compatible nested dependencies where upstream major ranges differ.
  [npm](https://github.com/npm/cli/releases/tag/v12.2.2),
  [selector parser](https://github.com/postcss/postcss-selector-parser/blob/v7.1.7/CHANGELOG.md)
- Pin setup-dotnet 6.1.0, setup-uv 10.3.3, claude-code-action 1.0.268 and
  action-junit-report 6.7.0 to their full commit hashes. Use setup-dotnet's
  `skip-lts-prepass` for the three explicitly versioned .NET fixture jobs.
  Select uv 0.13.7 in CI and Docker and Node 26.13.0 in CI.
  [setup-dotnet](https://github.com/actions/setup-dotnet/releases/tag/v6.1.0),
  [setup-uv](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.3),
  [Claude action](https://github.com/anthropics/claude-code-action/releases/tag/v1.0.268),
  [JUnit report](https://github.com/mikepenz/action-junit-report/releases/tag/v6.7.0),
  [uv](https://github.com/astral-sh/uv/releases/tag/0.13.7),
  [Node](https://nodejs.org/en/blog/release/v26.13.0)
- Update the optional current-Unity performance test package to 3.7.0. Its fixes
  clear sample groups between tests and correct timing conversion on affected
  runtimes. Compare historical measurements with those corrections in mind.
  [Unity 3.7.0 package with changelog](https://download.packages.unity.com/com.unity.test-framework.performance/-/com.unity.test-framework.performance-3.7.0.tgz)

## FastMCP integration

FastMCP owns the server and the test-results App's typed `AppConfig`, CSP and
permissions. MIME-specific host negotiation uses the MCP SDK helper. The App
continues to use the packaged HTML resource and completed `view_test_results`
response.

The CLI's doctor and tool-description commands share `DiagnosticClient`, backed
by the public MCP SDK client. A trial with FastMCP 4.1.6's client failed the
existing real-HTTP slow-close regression. Its context exit schedules a shielded
stop task, allowing the caller's deadline to expire while that task still owns
HTTP cleanup. The shared SDK implementation keeps teardown in the caller's task.
Focused checks cover deadlines, final closure, redirects, response limits,
authentication and target metadata.

For a local sample preview, open `Server/tests/fixtures/test_results_host.html`,
select `Server/src/services/test_results_view.html`, then use **Run controlled
lifecycle cases**. A live MCP Apps host can display completed Unity test jobs by
calling `run_tests`, waiting through `get_test_job`, and passing the returned job
ID to `view_test_results`.

## Compatibility decisions

- The two Unity 2021.3 legacy projects retain their current packages, as requested.
- The optional Roslyn installer retains its reviewed 4.12.0 assembly set.
  Newer Roslyn releases require a coordinated upgrade of strong-named
  Immutable/Metadata dependencies and validation across the supported Unity
  runtimes. The existing guide documents that assembly contract.
  [Roslyn integration](../../website/docs/guides/roslyn.md)
- The experimental .NET transport benchmark retains its measured baseline.
  [Benchmark dependency policy](../dependency-performance.md)
- actions/cache stays on the already selected 6.1.0 release. The upstream's
  chronologically newer 5.1.0 maintenance release belongs to a separate major.
  [Cache releases](https://github.com/actions/cache/releases)
- The npm audit reports one low-severity deepmerge advisory through five package
  entries in the SVG build chain. The advisory has no patched release. The
  withdrawn braces advisory is reflected in the current audit.
  [Website dependency assessment](../../website/DEPENDENCIES.md)

## Verification scope

Local verification uses Windows, Python 3.14.10, Node 26.13.0 and npm 12.2.2.
The server suite passes 13,880 tests with 27 platform or optional skips. Its
isolated real-HTTP suite passes 2,635 tests with two skips. Repository-tool tests
pass 3,023 tests and 292 subtests with 14 skips. Actionlint, generated
SDK/reference checks, changed-script formatting and Python correctness lint pass.
The refreshed project produces its server wheel and source distribution.

Website verification includes 30 dependency contracts and three route checks, the production build,
production browser navigation and local search, plus development hot reload.
The current optional Unity profile resolves against the installed
6000.0.69f1 Editor data. Native Unity execution, Docker execution and live
third-party MCP Apps host interoperability require their separate runtime checks.
The controlled-host lifecycle browser suite retains its existing deferred status.

## Release source index

The grouped index below lists every changed Python and website dependency range.
Each source group includes all intervening releases found in the official
repository. The two DefinitelyTyped packages were compared using their published,
integrity-verified declarations. Selector-parser, Actions and Unity release
sources are linked in the applied changes above.

| Package ranges | Official releases or comparisons |
| --- | --- |
| `cyclopts 5.3.1 -> 5.3.3` | [5.3.2](https://github.com/BrianPugh/cyclopts/releases/tag/v5.3.2), [5.3.3](https://github.com/BrianPugh/cyclopts/releases/tag/v5.3.3) |
| `@types/estree 1.0.9 -> 1.0.10`, `@types/node 26.6.5 -> 26.6.6` | [ESTree declarations](https://www.npmjs.com/package/@types/estree/v/1.0.10), [Node declarations](https://www.npmjs.com/package/@types/node/v/26.6.6) |
| `electron-to-chromium 1.5.464 -> 1.5.477` | [range](https://github.com/Kilian/electron-to-chromium/compare/b358308d4d9f5badf36073774314156b9ba7e5e5...19708db3fcbeb7365186963df670c06f6543ea23) |
| `python-multipart 0.0.32 -> 0.0.33` | [0.0.33](https://github.com/Kludex/python-multipart/releases/tag/0.0.33) |
| `tslib 2.8.1 -> 2.9.1` | [2.9.1](https://github.com/microsoft/tslib/releases/tag/v2.9.1) |
| `lunr-languages 1.23.0 -> 1.24.0` | [1.24.0](https://github.com/MihaiValentin/lunr-languages/releases/tag/v1.24.0) |
| `@peculiar/asn1-cms 2.11.0 -> 2.11.1`, `@peculiar/asn1-csr 2.11.0 -> 2.11.1`, `@peculiar/asn1-ecc 2.11.0 -> 2.11.1`, `@peculiar/asn1-pfx 2.11.0 -> 2.11.1`, `@peculiar/asn1-pkcs8 2.11.0 -> 2.11.1`, `@peculiar/asn1-pkcs9 2.11.0 -> 2.11.1`, `@peculiar/asn1-rsa 2.11.0 -> 2.11.1`, `@peculiar/asn1-schema 2.11.0 -> 2.11.1`, `@peculiar/asn1-x509 2.11.0 -> 2.11.1`, `@peculiar/asn1-x509-attr 2.11.0 -> 2.11.1` | [2.11.1](https://github.com/PeculiarVentures/asn1-schema/releases/tag/v2.11.1) |
| `fastmcp 4.1.4 -> 4.1.6`, `fastmcp-slim 4.1.4 -> 4.1.6` | [4.1.5](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.5), [4.1.6](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.6) |
| `anyio 4.15.1 -> 4.16.0` | [4.16.0](https://github.com/agronholm/anyio/releases/tag/4.16.0) |
| `@algolia/abtesting 1.26.0 -> 1.27.0`, `@algolia/client-abtesting 5.60.0 -> 5.61.0`, `@algolia/client-analytics 5.60.0 -> 5.61.0`, `@algolia/client-common 5.60.0 -> 5.61.0`, `@algolia/client-insights 5.60.0 -> 5.61.0`, `@algolia/client-personalization 5.60.0 -> 5.61.0`, `@algolia/client-query-suggestions 5.60.0 -> 5.61.0`, `@algolia/client-search 5.60.0 -> 5.61.0`, `@algolia/ingestion 1.60.0 -> 1.61.0`, `@algolia/monitoring 1.60.0 -> 1.61.0`, `@algolia/recommend 5.60.0 -> 5.61.0`, `@algolia/requester-browser-xhr 5.60.0 -> 5.61.0`, `@algolia/requester-fetch 5.60.0 -> 5.61.0`, `@algolia/requester-node-http 5.60.0 -> 5.61.0`, `algoliasearch 5.60.0 -> 5.61.0` | [range](https://github.com/algolia/algoliasearch-client-javascript/compare/642e71a8cc2cd1f45a622ea1f00b236a33040d3c...10b58b56ca70cd99ffb95565ad52ad0464517a77), [5.61.0](https://github.com/algolia/algoliasearch-client-javascript/releases/tag/5.61.0) |
| `algoliasearch-helper 3.30.0 -> 3.30.1` | [3.30.1](https://github.com/algolia/instantsearch/releases/tag/algoliasearch-helper%403.30.1) |
| `uv 0.13.5 -> 0.13.7` | [0.13.6](https://github.com/astral-sh/uv/releases/tag/0.13.6), [0.13.7](https://github.com/astral-sh/uv/releases/tag/0.13.7) |
| `authlib 1.9.1 -> 1.9.4` | [1.9.2](https://github.com/authlib/authlib/releases/tag/v1.9.2), [1.9.3](https://github.com/authlib/authlib/releases/tag/v1.9.3), [1.9.4](https://github.com/authlib/authlib/releases/tag/v1.9.4) |
| `joserfc 1.7.7 -> 1.8.0` | [1.8.0](https://github.com/authlib/joserfc/releases/tag/1.8.0) |
| `@babel/core 7.29.7 -> 7.29.13`, `@babel/generator 7.29.8 -> 7.29.13`, `@babel/helper-compilation-targets 7.29.7 -> 7.29.13`, `@babel/helper-create-class-features-plugin 7.29.7 -> 7.29.13`, `@babel/helper-module-transforms 7.29.7 -> 7.29.13`, `@babel/helper-skip-transparent-expression-wrappers 7.29.7 -> 7.29.13`, `@babel/helper-string-parser 7.29.11 -> 7.29.13`, `@babel/helpers 7.29.10 -> 7.29.13`, `@babel/parser 7.29.11 -> 7.29.13`, `@babel/plugin-bugfix-safari-rest-destructuring-rhs-array 7.29.7 -> 7.29.13`, `@babel/plugin-transform-block-scoping 7.29.11 -> 7.29.13`, `@babel/plugin-transform-destructuring 7.29.7 -> 7.29.13`, `@babel/plugin-transform-for-of 7.29.7 -> 7.29.13`, `@babel/runtime 7.29.10 -> 7.29.13`, `@babel/traverse 7.29.10 -> 7.29.13` | [7.29.13](https://github.com/babel/babel/releases/tag/v7.29.13), [7.29.12](https://github.com/babel/babel/releases/tag/v7.29.12) |
| `resolve 1.22.13 -> 1.22.14` | [range](https://github.com/browserify/resolve/compare/56d746b8982fc9917a3c115dfa71c569e772b572...aa0d9e573c347ba035a9e6315fcf69747fe33f99) |
| `browserslist 4.29.4 -> 4.29.5` | [4.29.5](https://github.com/browserslist/browserslist/releases/tag/4.29.5) |
| `caniuse-lite 1.0.30001819 -> 1.0.30001820` | [range](https://github.com/browserslist/caniuse-lite/compare/7a73715a9b588c37d8eb331597a020299935b6a7...a16a804e063b7a84ca52dd3fe925a79af1023a1c) |
| `ansi-regex 6.4.0 -> 6.5.0` | [6.5.0](https://github.com/chalk/ansi-regex/releases/tag/v6.5.0) |
| `node-releases 2.0.60 -> 2.0.63` | [range](https://github.com/chicoxyzzy/node-releases/compare/eb9b4fe23a7d0323de4ddf3689b7c5d488a350cc...637e194c421cdd01c18a90adb931c4d127e779b7) |
| `uncalled-for 0.4.1 -> 0.4.2` | [0.4.2](https://github.com/chrisguidry/uncalled-for/releases/tag/0.4.2) |
| `coverage 7.17.0 -> 7.17.2` | [7.17.1](https://github.com/coveragepy/coveragepy/releases/tag/7.17.1), [7.17.2](https://github.com/coveragepy/coveragepy/releases/tag/7.17.2) |
| `cssdb 8.12.0 -> 8.12.1` | [8.12.1](https://github.com/csstools/cssdb/releases/tag/v8.12.1) |
| `joi 17.13.8 -> 17.13.9` | [range](https://github.com/hapijs/joi/compare/4ae6af96f7990fdb336aa8ad7dee5e9f847d084a...5af45306226782035951fdc49f10fab5ebe021a2) |
| `is-core-module 2.17.0 -> 2.18.0` | [range](https://github.com/inspect-js/is-core-module/compare/9b50b2d1bf457b56ab4f547b4ea744da1502cf74...335346560808d35b44a2bded673c0cc3376e29bb) |
| `qs 6.16.1 -> 6.17.1` | [range](https://github.com/ljharb/qs/compare/2471b66ae696a8a10915c01453ae2c0f139a5adc...43baea4655cb9b543fb1c5cc0db95cde42852140) |
| `npm 12.2.1 -> 12.2.2` | [12.2.2](https://github.com/npm/cli/releases/tag/v12.2.2) |
| `semver 6.3.1 -> 6.3.2`, `semver 7.8.5 -> 7.8.6` | [6.3.2](https://github.com/npm/node-semver/releases/tag/v6.3.2), [7.8.6](https://github.com/npm/node-semver/releases/tag/v7.8.6) |
| `opentelemetry-api 1.45.1 -> 1.45.2` | [1.45.2](https://github.com/open-telemetry/opentelemetry-python/releases/tag/v1.45.2) |
| `encodeurl 2.0.0 -> 2.0.1` | [2.0.1](https://github.com/pillarjs/encodeurl/releases/tag/v2.0.1) |
| `autoprefixer 10.6.1 -> 10.6.2` | [10.6.2](https://github.com/postcss/autoprefixer/releases/tag/10.6.2) |
| `postcss 8.5.31 -> 8.5.32` | [8.5.32](https://github.com/postcss/postcss/releases/tag/8.5.32) |
| `pydantic 2.14.1 -> 2.14.2`, `pydantic-core 2.50.1 -> 2.50.2` | [2.14.2](https://github.com/pydantic/pydantic/releases/tag/v2.14.2), [range](https://github.com/pydantic/pydantic/compare/v2.14.1...v2.14.2) |
| `pydantic-settings 2.16.1 -> 2.16.2` | [range](https://github.com/pydantic/pydantic-settings/compare/v2.16.1...v2.16.2) |
| `typing-inspection 0.4.4 -> 0.4.5` | [0.4.5](https://github.com/pydantic/typing-inspection/releases/tag/v0.4.5) |
| `pytest 9.1.1 -> 9.2.0` | [9.2.0](https://github.com/pytest-dev/pytest/releases/tag/9.2.0) |
| `pytest-asyncio 1.4.1 -> 1.4.1.post1` | [1.4.1.post1](https://github.com/pytest-dev/pytest-asyncio/releases/tag/v1.4.1.post1) |
| `websockets 17.3 -> 17.4` | [17.4](https://github.com/python-websockets/websockets/releases/tag/17.4) |
| `mypy-extensions 1.1.0 -> 1.2.0` | [range](https://github.com/python/mypy_extensions/compare/1.1.0...1.2.0) |
| `inline-style-parser 0.2.7 -> 0.2.9` | [0.2.8](https://github.com/remarkablemark/inline-style-parser/releases/tag/v0.2.8), [0.2.9](https://github.com/remarkablemark/inline-style-parser/releases/tag/v0.2.9) |
| `style-to-object 1.0.14 -> 2.0.2` | [2.0.0](https://github.com/remarkablemark/style-to-object/releases/tag/v2.0.0), [2.0.1](https://github.com/remarkablemark/style-to-object/releases/tag/v2.0.1), [2.0.2](https://github.com/remarkablemark/style-to-object/releases/tag/v2.0.2) |
| `dnspython 2.9.0 -> 2.9.2` | [2.9.1](https://github.com/rthalley/dnspython/releases/tag/v2.9.1), [2.9.2](https://github.com/rthalley/dnspython/releases/tag/v2.9.2) |
| `hast-util-from-parse5 8.0.3 -> 8.0.4` | [8.0.4](https://github.com/syntax-tree/hast-util-from-parse5/releases/tag/8.0.4) |
| `hast-util-to-jsx-runtime 2.3.6 -> 2.3.7` | [2.3.7](https://github.com/syntax-tree/hast-util-to-jsx-runtime/releases/tag/2.3.7) |
| `hastscript 9.0.1 -> 9.0.3` | [9.0.2](https://github.com/syntax-tree/hastscript/releases/tag/9.0.2), [9.0.3](https://github.com/syntax-tree/hastscript/releases/tag/9.0.3) |
| `mdast-util-directive 3.1.1 -> 3.2.0` | [3.2.0](https://github.com/syntax-tree/mdast-util-directive/releases/tag/3.2.0) |
| `mdast-util-gfm-table 2.0.0 -> 2.0.1` | [2.0.1](https://github.com/syntax-tree/mdast-util-gfm-table/releases/tag/2.0.1) |
| `mdast-util-mdx-jsx 3.2.0 -> 3.2.1` | [3.2.1](https://github.com/syntax-tree/mdast-util-mdx-jsx/releases/tag/3.2.1) |
| `mdast-util-to-markdown 2.2.0 -> 2.2.2` | [2.2.1](https://github.com/syntax-tree/mdast-util-to-markdown/releases/tag/2.2.1), [2.2.2](https://github.com/syntax-tree/mdast-util-to-markdown/releases/tag/2.2.2) |
| `cachetools 7.2.6 -> 7.2.8` | [range](https://github.com/tkem/cachetools/compare/v7.2.6...v7.2.8) |
| `platformdirs 4.13.5 -> 4.14.5` | [4.13.6](https://github.com/tox-dev/platformdirs/releases/tag/4.13.6), [4.13.7](https://github.com/tox-dev/platformdirs/releases/tag/4.13.7), [4.14.0](https://github.com/tox-dev/platformdirs/releases/tag/4.14.0), [4.14.2](https://github.com/tox-dev/platformdirs/releases/tag/4.14.2), [4.14.3](https://github.com/tox-dev/platformdirs/releases/tag/4.14.3), [4.14.4](https://github.com/tox-dev/platformdirs/releases/tag/4.14.4), [4.14.5](https://github.com/tox-dev/platformdirs/releases/tag/4.14.5) |
| `baseline-browser-mapping 2.11.32 -> 2.11.35` | [range](https://github.com/web-platform-dx/baseline-browser-mapping/compare/b010b392b06ae9f6de5c903b293da638d6145185...cc41ca07e90834563b394b822e7c0603ee8c71b0) |
| `enhanced-resolve 5.26.0 -> 5.26.1` | [5.26.1](https://github.com/webpack/enhanced-resolve/releases/tag/v5.26.1) |
| `minimizer-webpack-plugin 5.13.1 -> 5.14.0` | [range](https://github.com/webpack/minimizer-webpack-plugin/compare/f039f93844af0811b7ebd4f04d7a287186a9cb90...317e8536dad1c3d6d86cb0e8c6129d66779270be) |
| `schema-utils 4.8.1 -> 4.8.2` | [4.8.2](https://github.com/webpack/schema-utils/releases/tag/v4.8.2) |
| `webpack-dev-middleware 8.5.0 -> 8.6.0` | [8.6.0](https://github.com/webpack/webpack-dev-middleware/releases/tag/v8.6.0) |
| `webpack-sources 3.6.0 -> 3.7.0` | [3.7.0](https://github.com/webpack/webpack-sources/releases/tag/v3.7.0) |
| `ws 8.22.0 -> 8.22.1` | [8.22.1](https://github.com/websockets/ws/releases/tag/8.22.1) |
| `markdown-table 3.0.4 -> 3.1.0` | [3.1.0](https://github.com/wooorm/markdown-table/releases/tag/3.1.0) |
