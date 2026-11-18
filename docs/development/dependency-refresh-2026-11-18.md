# Dependency refresh for November 18, 2026

The fork's package is `com.ykh09242.unity-mcp` 2.5.0. Coplay's latest published release is v10.3.0. Its release commit `aa5fc638d6` is already an ancestor of the fork's `beta` checkout. [Coplay release](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.3.0)

## Server runtime

FastMCP and FastMCP-Slim advance from 4.1.8 to 4.2.1. The public package metadata, uv lock and generated SDK compatibility manifest agree on FastMCP 4.2.1, MCP 2.3.0 and MCP Types 2.3.0. The lock updates 17 of its 87 package versions and retains its platform resolution markers.

- **Packaged Skills.** `skills/list` and `skills/get` use the SDK's typed request and result models. The server validates the entire manifest before publishing resources and copies the admitted response per request. The packaged adapter serves the recorded revision and file digests with the existing visibility transforms, authentication and private zero-TTL cache policy. FastMCP's provider extension still skips discovery on a server with transforms. [FastMCP 4.2.0](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.2.0) · [SDK integration notes](../reviewed-sdk-runtime.md#skills-on-fastmcp-421)
- **Tool results.** FastMCP now applies middleware edits to public result fields while retaining protocol extensions. Registered Unity MCP tools check raw results before the SDK serializes structured content. Final delivery checks the merged wire result, preserving middleware edits and protocol metadata within the existing size and worker budgets. [FastMCP 4.2.1](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.2.1) · [Result merge change](https://github.com/PrefectHQ/fastmcp/pull/6520)
- **Legacy SSE.** The upstream transport now sends a single HTTP response start when the stream disconnects. Both local and remote-mode cases assert that behavior while checking a real initialization response on the stream. [Upstream fix](https://github.com/PrefectHQ/fastmcp/pull/6431)
- **Session storage.** The package requires `py-key-value-aio` 0.5.0 or later so unlocked installs get the Python 3.15 runtime protocol fix. Candidate CI now exercises the library's default runtime type checks. The session test covers the public setup boundary and a real put/get for the allowed collection. It also verifies that rejected collection names never enter backend setup. The separate released-launcher canary temporarily retains its older archive's exception. [Storage release](https://github.com/strawgate/py-key-value/releases/tag/0.5.0)
- **Maintenance tools and transitive fixes.** uv 0.13.9 is pinned in CI and packaging. Ruff 0.17.5 is shared by the dev environment and formatter runner. The lock includes pytest-asyncio 1.5.0, Pylint 4.1.4, regex 2026.11.18, Authlib 1.9.9 and HTTPX2/HTTPCore2 2.16.2. [uv release](https://github.com/astral-sh/uv/releases/tag/0.13.9) · [Ruff release](https://github.com/astral-sh/ruff/releases/tag/0.17.5) · [pytest-asyncio release](https://github.com/pytest-dev/pytest-asyncio/releases/tag/v1.5.0) · [Authlib changelog](https://docs.authlib.org/en/stable/upgrades/changelog.html)

## Unity and C# diagnostics

The standalone .NET 10 diagnostics host loads Roslyn Workspaces and CodeStyle 5.12.0, up from 5.9.0, and `Microsoft.Bcl.AsyncInterfaces` 11.0.0. Its reported provider versions, notices and NuGet lock identify those binaries. The test fixture uses NUnit 5.0.0, NUnit3TestAdapter 6.3.1 and Microsoft.NET.Test.Sdk 18.12.0. Process, protocol and installation tests now await NUnit's asynchronous exception assertions. [Roslyn package](https://www.nuget.org/packages/Microsoft.CodeAnalysis.CSharp.Workspaces/5.12.0) · [NUnit 5 release](https://docs.nunit.org/articles/nunit/release-notes/framework.html#nunit-500---sept-27-2026)

The optional Unity 6000.0 profile advances Settings Manager from 2.1.1 to 2.1.2. The registry checksum was verified and the archive's SHA-256 was recorded for reproducible acquisition. Upstream changes concern its test classes and build metadata. The runtime UPM dependencies retain versions compatible with the declared Unity 2021.3 minimum. Editor Coroutines 1.1.0 requires Unity 2022.3. [Settings Manager archive with changelog](https://download.packages.unity.com/com.unity.settings-manager/-/com.unity.settings-manager-2.1.2.tgz) · [Editor Coroutines registry](https://packages.unity.com/com.unity.editorcoroutines)

## Documentation website

npm advances from 12.2.2 to 12.2.3. The compatible lock refresh updates 69 existing package locations across 67 package names. The selector parser minimum is now 7.2.1. A PostCSS Modules test verifies that an escaped class produces the same identifier in the generated CSS selector and the module export. [npm release](https://github.com/npm/cli/releases/tag/v12.2.3) · [Selector parser changelog](https://github.com/postcss/postcss-selector-parser/blob/main/CHANGELOG.md#721---2026-11-10)

The November 18 npm audit reports one underlying high-severity advisory in `picomatch` and counts its dependent packages as 36 high entries. No patched release was available. The dependency tree and production build pass their own checks. [GitHub advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) · [Website dependency policy](../../website/DEPENDENCIES.md)

## Actual MCP client

The interoperability fixture advances from Codex CLI 0.167.1 to 0.170.0. The CI workflow pins the official Linux archive digest. On Windows the official archive and executable matched their published SHA-256 digests. The real client passed all five cases against the updated server, covering legacy and modern stdio and authenticated HTTP, plus pooled stdio. This exercised discovery, Unicode, error handling, cancellation, large result retrieval and two concurrent session owners. Codex's new multi-environment server naming is client-side. [Codex 0.170.0 release](https://github.com/openai/codex/releases/tag/rust-v0.170.0) · [Client assurance](client-assurance.md#actual-codex-client-regression)

## Verification

- Both .NET projects restored in locked mode and all 26 diagnostics host tests passed on .NET 10.
- On Python 3.14, the server suite passed 16,321 tests with 27 skips. Its separate HTTP worker suite passed 2,944 tests with two skips.
- The repository tools suite passed 3,899 tests and 372 subtests with 14 skips. Python correctness lint, changed-source formatting, SDK manifest validation and actionlint on all changed workflows passed.
- On Python 3.11, 147 focused tests passed across SDK registration, result limits, session state, Skills and HTTP. On Python 3.15, 114 focused tests passed with the storage library's optional runtime type checks enabled and warnings treated as errors.
- The source distribution and wheel built in isolation. On Python 3.14 and 3.15 the installed wheel completed initialization, listed 43 core tools and executed `debug_request_context` on both stdio and authenticated loopback HTTP.
- On Windows, the first stdio initialization of an uncompiled Python 3.15 installation could exceed the 10-second probe deadline. A timing run measured 10.08 seconds on first start, 2.80 seconds on the next start, and under 0.2 seconds for each catalog and tool call. With `uv pip install --compile-bytecode`, the first stdio and HTTP exchanges passed within their original deadlines. The Docker build now precompiles installed dependencies. [Packaging details](../PACKAGING_INPUTS.md)
- The site passed a clean npm install, dependency-tree validation, 34 prebuild contracts and three production route checks. Browser verification covered local search, result navigation and automatic updates to an open page on the development server.

Native Unity Editor, Player and Docker image execution are outside these local verification results. The package's immutable launcher reference follows the existing release process.
