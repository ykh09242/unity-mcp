# Matched Unity edits on the synchronized Editor catalog

Both catalogs completed all 12 original requests on package `79fc892df`. The zero-failure target was missed in both catalogs. Eight turns recovered from one rejected MCP request each. Every public request received a reply. [Per-case results](editor_catalog_confirmation_results.json) · [Previous measurement](heartbeat_edit_confirmation.md)

| Catalog | Completed, previous → current | Failed tools/call | Failed resources/read | Failed host tool invocations |
|---|---:|---:|---:|---:|
| Core | 10/12 → 12/12 | 1/98 → 1/119 | 0/34 → 3/42 | 1/133 → 4/161 |
| Full | 12/12 → 12/12 | 2/121 → 1/104 | 1/44 → 3/35 | 3/172 → 4/141 |

The actor's own first complete tools list matched the commissioned 61 or 91 tools in every run. The core shipment-label turn received its expected catalog. The core tooltip turn reported the correct saved file. All 24 final answers agreed with the native outcome, the requested save state and the protected values. The host recorded ten diagnostic events per catalog, including three routing errors per catalog before a public request was sent. Those diagnostics can overlap with a failed invocation.

## Rejected requests

| Call | Core | Full | What happened |
|---|---:|---:|---|
| Search the tool catalog | 1 | 0 | The actor placed a complete argument object inside the string field `query`. The server's startup example used a positional object. The actor recovered with flat arguments. |
| Inspect objects in the active scene | 0 | 1 | The actor selected the active scene and requested projection fields. The scoped search rejected the omitted predicate before dispatch to Unity. |
| Read a skill document | 3 | 3 | Five reads supplied client installation paths and one supplied an unpublished skill URI. The packaged root was listed as `SKILL.md` with an empty description. |

## Measurement

Each slot ran once in a fresh installed Codex CLI 0.172.0 session with `gpt-6.2-sol` at medium effort. The only user input was the original UTF-8 request. The same twelve requests in ten languages, alternating catalog order, 360-second limit, observer and grading rules were frozen before submission. The Unity 6000.0.69f1 project used server `bf92d33d3` and packaged skill `dad211b92`. The archive check covered every file in both pinned source trees. Independent Editor controls checked all twelve edits and protected neighbors. Additional disk controls covered the UXML, material and audio importer cases. After the owned Editor exited, all 130 shared tool preferences matched their initial values.

The verifier checks the public ledger and predecessor. With the private evidence directory it also replays the wire observations, actor turns, native and saved results. The strict gate exits with status 1 for this run.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/editor_catalog_confirmation_results.json
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/editor_catalog_confirmation_results.json --require-perfect
```

## Verification after the fixes

Package `33a5d84fc` selects server `a394fcb08` and skill `a359961d0`. A fresh isolated Unity 6000.0.69f1 project loaded the committed package. A standard MCP client used each catalog over stdio. Its first inventory contained 61 tools in core and 91 in full. The following checks succeeded in each:

| Contract | Observation in both catalogs |
|---|---|
| Skill discovery | The catalog and resource list exposed the root name and description from the packaged manifest. Following its published `read_call` returned the exact committed `SKILL.md` bytes. |
| Search arguments | The JSON example from initialization accepted the recorded search text. Both agent browsing and ranked search accepted `query` and `selection` as sibling arguments. |
| Scoped object listing | The original projection-only request returned six objects in the active scene, including `Dispatch Overlay` and `Reference Canvas`, with matching IDs and component types. The native scene snapshots matched before and after these reads. |

The startup instructions, both skill distributions and the discovery tool now describe how to open the guide before its first read. The server publishes root metadata from the validated manifest and continues to check access to each listed resource. The search handler and Editor share the bounded pager for explicit scene scopes. The default legacy lookup and incomplete selectors continue to require a usable filter. [Skill metadata specification](https://skills.extensions.modelcontextprotocol.io/specification/beta/skills#resource-metadata) · [MCP resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)

The previously failing projection also failed on the original native Editor before the change. All 86 scoped-search EditMode cases passed after it. The server and SDK regression groups passed 150 tests. Bundle, skill and release-tool checks passed 97 tests with 383 subtests. The shipping Editor exited and all 130 shared tool preferences matched. Hosted first-attempt completion and failure rates in this report apply to `79fc892df`. The later checks used deterministic clients. [Native tests](../../TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/FindGameObjectsScopedSearchTests.cs) · [SDK search tests](../../Server/tests/test_search_intent_contract.py) · [HTTP skill tests](../../Server/tests/http/test_mcp_skills.py)
