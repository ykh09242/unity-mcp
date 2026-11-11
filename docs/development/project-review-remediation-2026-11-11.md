# Project review remediation: 2026-11-11

The review started at `d3fa72d600a5731578660a3d0e0c9c7b8ff67cba` on `beta`.
The changes implement all 16 original review items and add actual Codex shared
connection assurance. Dependency updates preceded the behavior changes. The
dependency groups and original 16 remediation items received independent
specification and code-quality reviews before their task-sized commits.

The [dependency refresh report](dependency-refresh-2026-11-11.md) records exact
versions, upstream release-note applicability, compatible holds and package
verification. It includes the separately authorized 130-file CSharpier 1.4
migration and its token, literal and comment preservation checks.

## Implemented improvements

| ID | Resulting behavior | Commit | Focused evidence |
| --- | --- | --- | --- |
| R01 | Script compensation preserves external replacements that this operation never attempted. Restoration after an attempted replacement requires retained preimage proof. | `f9bd11d18` | Native regressions cover external writes equal to the prepared candidate and unavailable or changed preimages. |
| R02 | Retried stdio attempts detach their own progress source; the logical request retains its reporter. Cancellation revokes progress before shielded native cancellation delivery. | `afad31c28` | Authenticated TCP retry/resume, stale-source and cancellation regressions; 83 tests in the final affected selection. |
| R03 | Persisted physical-reclamation generations make old graph cursors return an explicit restart result. | `5e136e3d2` | Reclamation, legacy cursor, reopening and transactional rollback contracts; semantic graph revision is not falsely advanced. |
| R04 | Retired hidden graph rows no longer consume the visible response-byte allowance. | `5e136e3d2` | Near-limit query/snapshot cases retain genuine oversized-row refusal. |
| R05 | Runtime binding lookup uses both session and handle columns of its index. | `4d3cef05c` | Matching policy results and bounded SQLite instruction measurements, detailed below. |
| R06 | Native release and offline reclaim remove proven empty owner directories under the shared lock. Native startup also performs bounded recovery of old empty owners. | `f6bde721e` | Native and offline ownership cases preserve racing replacements, nonempty directories and unknown contents. |
| R07 | Both installed skill mirrors use focused default tools, label optional groups, and document explicit modern request routing. | `1a0988ff4` | Examples validated against actual default SDK catalogs and both protocol schemas; the generated bundle and immutable source pins were refreshed afterward. |
| R08 | Subprocess completion and native action observations are associated with the actual execution, checkout and attached output digests. | `fdba48dd7` | Producer/consumer roundtrips and refusal of mixed outputs, wrong attempts, missing completion and misleading native outcomes. |
| R09 | A healthy authenticated server with no Editor returns a bounded `no_unity_instances` diagnosis that the CLI preserves. | `dc1925265` | Actual CLI-to-HTTPX-to-route checks plus proxy, authentication, timeout and valid-dispatch controls. |
| R10 | Focused GameObject creation rejects omitted, null or blank names and invalid prefab combinations before native dispatch. | `bc5937af2` | Both public protocol schemas and dispatch guards; generic legacy action differences remain valid. |
| R11 | Failed-output cleanup requires a private marker proving directory ownership. | `7d22fba90` | Native move/recreate-path cases preserve replacements and clean unchanged owned empty directories. |
| R12 | Reference-only changes run the actual resource-URI contract. | `934114efa` | Trigger checks and rejection of an invalid authored example; the actual URI suite passed 13 cases. |
| R13 | Path-proof count and high-water sequence advance transactionally with insertion. | `4d3cef05c` | Rollback, reopening, duplicate/replay and revocation contracts, plus bounded marker instruction measurements. |
| R14 | Source-pin PRs and beta pushes select one distribution canary; installed archive provenance must match the selected input. | `c078cbe37` | URL/subdirectory checks and independently hashed candidate ZIP controls, including valid legacy hash metadata. |
| R15 | The offline container smoke preserves the shipped environment and entrypoint. | `9a9ea4517` | Command, deadline and exact-name cleanup controls; actual startup requires an executed container job. |
| R16 | Shared asset-path validation rejects trailing ASCII spaces or dots in path segments. | `b7a1f5d0b` | Windows native reproduction established unconsented canonical `.cs` destination creation before the fix; fixed cases refuse it. This does not establish arbitrary code execution. |

## Integration follow-up

The whole-suite integration pass also exposed three existing defects that
predated this refresh, plus SDK and test-fixture compatibility gaps. They were
addressed in separate commits:

| Commit | Resulting behavior |
| --- | --- |
| `ab8e53c23` | Internal mutation recovery retains the typed, in-process zero-dispatch proof. Public responses remain normalized, handler dictionaries cannot authorize replay, and the private option never reaches native dispatch. |
| `35d8a30f3` | Failed large-result processing releases raw payload references and their charge before asynchronous socket close can suspend. Direct and detached processing preserve other generations' allocations and duplicate-result protection. |
| `6344ea896` | CLI catalog construction no longer imports the material operation's server tool runtime; color execution still uses the shared normalizer. |
| `1d0b439e5` | Isolated SDK scenarios clear the updated callable caches between cases, and diagnostic instrumentation accepts the reviewed FastMCP 4.1.8 runtime. The cache cleanup is confined to the test worker. |
| `91e70f2d8` | Catalog, native generated descriptions and passive doctor expectations agree with the intentional creation and diagnostic contracts. |
| `c12936e90` | CI tests inspect the generated bridge matrix, both canary alternatives and the actual compiler wrapper arguments, including failure propagation. |

Focused executions passed 190 core retry/lifetime/isolation cases, 132 expanded
lifetime/worker cases, 142 mutation-boundary cases, 21 doctor cases, seven catalog
cases and 155 tooling cases. These selections overlap. The lifetime assertions
retain their weak-reference checks and now cover detached processing; no garbage
collection assertion was weakened. An isolated cache-root probe established that
the new metadata cache retained a prior scenario's application and that releasing
that root freed it. Formatting, correctness lint and canonical native capability
generation checks passed for the final changed inputs. The server source pin was
advanced again after these integration fixes.

## Codex connection sharing

The [versioned sharing matrix](shared-connections.md) separates the actual Rust
client observations, executed server contracts and remaining host limits.
`cd1bf4fbc` adds negotiated native capability diagnostics; `6f399b5a8` adds shared
connection assurance and documents its boundaries.

The final owned Codex 0.167.1 execution passed five cases: legacy and modern
protocols over private stdio and external HTTP, plus marker-free pooled stdio.
The pooled case negotiated MCP `2026-07-28` with the client sharing feature
enabled, while the ordinary modern feature was disabled and the protocol marker
was absent. The unimplemented optional `codex/connection-lease` extension was not
advertised.

Actual tool identities establish that two ephemeral owners reuse one process and
startup UUID. The checks also establish overlapping requests, correlated deadline
cancellation and handler cleanup, recovery, continued access after one owner
unloads, and a different process for the next stdio owner after the last unload.
The complete observations retain ancillary catalog/reload process starts; this is
not a claim that Codex launches only one child during the whole scenario.

Distinct retained results survive one owner's unload within the shared authority
domain, reconstruct to their expected hashes, and can be released independently.
Host thread metadata is not a new authorization boundary. Modern tools require
`arguments.unity_instance` per request; resources use `_meta.unity_instance`.
Legacy sticky selection belongs to an MCP session, not a Codex thread.

Passive stdio diagnostics read the current retained authenticated session and
generation using nonblocking locks. Unknown, busy or retired state stays unknown;
a verified empty negotiation remains empty. This metadata snapshot does not
establish remote socket reachability. Existing cache/liveness probes retain
their separate authorization checks.

The actual client run used isolated homes, an offline provider, zero model turns
and no user credentials. It did not exercise a real model-agent turn, desktop UI,
native Unity interaction or an actual multi-Editor Codex inventory. Those claims
require their corresponding real-surface scenarios.

## Executed evidence and limits

Counts describe individual selections and can overlap; they are not a sum of
unique repository tests.

- The isolated Windows Unity 6000.0.69f1 ownership/path selection passed 56/56
  native cases. The spool selection passed 27/27. Both exited normally with
  code 0; required NUnit cases, source-snapshot hashes and synthetic preference
  restoration were verified. All 10 changed native source/test files still
  match those passing snapshots.
- The broad graph selection passed 1,290 tests with four Windows-inapplicable
  POSIX skips. The final offline identity guard received its own 31-case run.
- The public SDK/CLI selection passed 180 tests. Adjacent transport selections
  passed 52 and 54 cases; only the 54 cases initially blocked by temporary-file
  permissions were retried. Strict documentation checks passed 37 tests with
  344 subtests and no warnings.
- Delivery assurance passed 542 focused tests. A later archive compatibility
  correction passed all 14 affected cases plus lint and formatting. Cached
  actionlint passed the changed workflows.
- Final sharing checks passed 23 harness/workflow, 94 HTTP/routing and 120
  controlled server/TCP/retention cases, plus the five actual Codex cases.
  The observation fixture's concurrent append race was fixed with process-owned
  journals and a complete-line merge before the final actual run passed.
- Ten managed fixture executions remain reusable: their compiled/extracted
  production inputs do not include the later changed C# behaviors. The formatter
  migration preserved literals, comments and nonoptional tokens; two files
  differed only by optional trailing commas. The later native behavior is
  covered by the native selections above.

For the runtime binding lookup, SQLite VM instructions changed from
3,779/340,378 to 196/193 with 100/10,000 unrelated bindings. For the path-proof
marker, work changed from 6,018/60,015 to 14/14 instructions with 1,000/10,000
proof rows. These measure SQL work, not end-to-end latency or a general frame-time
improvement.

The directory marker and spool identity guards coordinate repository writers;
an uncooperative filesystem replacement at the final check/action boundary is
not made atomic by a path check. Query database schema 6 requires a compatible
reader for a later downgrade. No existing user database was migrated during
this work.

Local native execution used Windows Unity 6000.0.69f1, not Unity 2021. Docker was
unavailable locally; its default-startup behavior is established only by an
executed container job. Hosted CI results must be inspected for the actual
receiving commit. The dependency report retains the unpatched low-severity
deepmerge advisory and the intentional compatibility/historical pin holds.

## Distribution consistency

The skill bundle was generated from the committed skill source with Git
verification. The original package metadata was restored before the supported
source-pin updater applied its transaction. The generated bundle was committed
before the server archive pin advanced. The final metadata and immutable
server/skill pair passed source-drift, bundle checksum and committed-content
validation.

[`release-source-inputs.json`](../../release-source-inputs.json) and
[`MCPForUnity/package.json`](../../MCPForUnity/package.json) remain the canonical
selected inputs. Product versions were not incremented, and no release or tag
was created by this maintenance work.
