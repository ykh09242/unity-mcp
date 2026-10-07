# CI validation

The workflows validate the server, repository tools and Unity package independently.
Compilation checks API compatibility; licensed Editor tests exercise runtime behavior.

## Python

[`python-tests.yml`](../../.github/workflows/python-tests.yml) runs both complete suites on
Python 3.11, 3.12, 3.13 and 3.14. Minor-version selectors allow the available patch release to
advance without editing every workflow. The server and tool suites use separate matrix jobs,
so tool tests do not wait for the longer server suite.

- Pylint runs once, in the Python 3.14 tools job.
- The complete Python 3.14 server suite collects coverage. Other versions run the same tests
  without duplicate coverage collection. Existing subprocess coverage boundaries are unchanged.
- Every suite treats warnings as errors, reports its 20 slowest tests, and uploads JUnit results.
  Artifact names include both the Python minor and suite, preventing parallel uploads colliding.
- Dependency caching is keyed by the Python minor and the server manifest/lock. Only the tools
  job saves a cache for each minor. Jobs still install from the committed lock.
- The Windows, Linux and macOS cold/offline bootstrap checks retain separate empty cache
  directories. Candidate server startup is also checked once on every Python minor with an
  independent dependency resolution, rather than relying on the locked test environment.

To inspect local test durations, run from `Server/`:

```sh
uv run --python 3.14 --locked --extra dev pytest tests/ -q -W error --tb=short --durations=20
uv run --python 3.14 --locked --extra dev python -m pytest ../tools/tests/ -q -W error --tb=short --durations=20
```

Keep test-node selection and failure behavior intact when optimizing. Deadline tests can use a
controlled clock at the tested module's boundary while preserving the configured budget and
refusal assertions. Do not shorten production timeouts to accelerate tests. Worker-level
parallelism needs separate verification: SDK subprocesses and import-time rotating log files
have different isolation requirements from independent CI jobs.

## Unity

[`unity-versions.json`](../../tools/unity-versions.json) is the authoritative matrix consumed by
the compile and licensed-test workflows. Keep supported/LTS rows when adding a new preview.
Track the current beta and alpha releases; retire superseded preview rows when advancing
those channels instead of retaining every earlier alpha from the same release family.
Stable rows pin GameCI images by digest; preview rows pin official Editor/module downloads by
version, size and publisher integrity metadata. Updating CI does not migrate the local Unity
test project or change the package's minimum supported Unity version.

[`compile-check.sh`](../../tools/compile-check.sh) compiles each matrix version with Windows,
macOS and Linux platform defines, including runtime, Editor, custom Roslyn and test assemblies.
[`unity-ci-packages.json`](../../tools/unity-ci-packages.json) selects the matching package
profile. Validate actual archive layouts, bundled compilers and references before adding a new
major version; a new version number alone does not establish compatibility.

Unity 7 is currently represented by the official `7000.0.0a7` alpha. Its initial CoreCLR Editor
still exposes the documented C# 9/.NET Standard 2.1 compatibility level. Treat later language/API
upgrades separately and verify them against the corresponding release's real inputs.

The Unity 7 profile uses the archive's bundled SDK and .NET Standard 2.1 references for both
Editor and runtime assemblies. It compiles the bundled uGUI runtime and Editor sources before
their consumers because this alpha no longer ships the template's precompiled UI assemblies.
Its cache validates those source inputs, and its defines select CoreCLR instead of Mono.
Earlier Unity profiles retain their existing reference and compiler contracts.

The existing license gate stays explicit. A skipped licensed-test job is not runtime test
evidence, and a successful compile is not proof that an Editor session was executed.

## Warning policy

Repository-owned Unity assemblies compile with warnings as errors. The existing CS1701/CS1702
reference-facade exceptions remain. Bundled `UnityEditor.TestRunner` suppresses CS0169/CS0649,
matching native Unity's unused-field policy. Only its pinned `6000.0.84f1` build also suppresses
CS0618 for UTF 1.6.0's obsolete Switch host-I/O call. These vendor exceptions do not apply to
the package or its tests; reassess the version-specific exception when updating that Editor.

The documentation build disables Node's experimental Web Storage because static rendering
does not need file-backed browser storage. Only the pinned Pages deploy action filters DEP0040
while its bundled code still imports `node:punycode`; other diagnostics remain visible. Remove
that exception when the action fixes its dependency. These are scoped vendor/runtime policies,
not a claim that third-party source has been repaired.

The website pins a nested `encoding-sniffer` override for search-local's Cheerio dependency to
remove deprecated `whatwg-encoding`. Dependency contracts exercise its CommonJS API and HTML
buffer/stream decoding. Reassess the override when Cheerio publishes the newer dependency.

The npm audit output remains enabled. As of 2026-10-08,
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) affects `braces` through
3.0.3 and has no published fix. npm also counts its affected parent dependencies, so multiple
high-severity entries can refer to this single advisory. The existing braces contract test
demonstrates the library's explicit `maxLength` option; it does not install a guard in every
transitive caller. Keep this limitation visible, recheck upstream releases, and review the
dependency paths instead of applying npm's suggested breaking downgrade automatically.
The checked deployment uses these packages during site generation and publishes static files;
repository and plugin glob configuration remain inputs to review.

## Reference material

- [Unity alpha releases](https://unity.com/releases/editor/alpha)
- [Unity October 2026 CoreCLR update](https://discussions.unity.com/t/coreclr-scripting-and-net-update-october-2026/1738338)
- [GitHub matrix jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations)
- [Pinned setup-uv inputs](https://github.com/astral-sh/setup-uv/blob/c18668ad3cf93ea998bef934396af7bb5c839dc7/action.yml)
- [Node 26.10 command-line options](https://github.com/nodejs/node/blob/v26.10.0/doc/api/cli.md)
