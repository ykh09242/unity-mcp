# Unity outcomes through standard MCP clients

The final Unity package `e599e7bae0eed7ba7a24b9666a38002d10abd8c6` pins server `e221c9366dc87e6053f48d6d36f2239b50f2f452`. The intermediate package `658e6070b8f1dabfd2a4ee8ad16cc14f003f7031` pins server `7e5e1a89739b6859ef789b71e0ca61a080ce8cda`. Both use skill `96ec78009148a1f47aca17c0595faa00712db214`. The calling application owns the conversation. The server prepares calls against their advertised schemas, returns a standard `tools/list` request after selection, and lets an authorized client look up an opted-in execution receipt. [Discovery contract](../../docs/task-tool-discovery.md) · [Receipt contract and lost-response experiment](../../docs/execution-receipts-ledger.md)

For each scored native trial the benchmark explicitly launched an ordinary client and submitted one original user message. An observed read required a correct final answer backed by a successful public call. An edit also required the requested Unity change and preservation of unrelated state. Totals include every tool error even when the agent later completed the task. A `resources/read` error and a request that went unanswered have their own counts. The original-cohort recorder also counted failed host items and `ERROR` records written to host stderr. These observations can overlap a failed public call. The strict acceptance check includes host errors in cases whose public calls all succeeded.

## The 142 original requests

Each catalog received the same 71 originals in 17 languages, comprising 43 reads and 28 edits. The archived November 25 run used the original disabled terrain preference. The November 26 main runs enabled terrain in the disposable Editor so that both reads and edits could reach the native implementation. Compare package changes within the same setting. [Archived outcomes](catalog_outcome_shipping_results.json) · [Intermediate outcomes](agent_outcome_original_intermediate.json) · [Final original outcomes](agent_outcome_original_results.json)

| Package and terrain setting | Core completed | Full completed | Failed tool replies, core / full | Failed resource reads, core / full |
| --- | ---: | ---: | ---: | ---: |
| Archived `e3c0c6450`, off | 57/71 | 64/71 | 13 / 35 | 6 / 5 |
| Intermediate `658e6070b`, privately enabled | 68/71 | 69/71 | 43 / 49 | 9 / 4 |
| Final `e599e7bae`, privately enabled, assigned catalogs | 71/71 | 71/71 | 58 / 44 | 2 / 8 |

On the final package, both catalogs completed 43/43 reads and 28/28 edits, including all six terrain reads and all six terrain edits. Every case produced a host final answer, independent native observations and supporting public receipts. All 71 core actors received the expected 32 tools. In full, 70 actors received the expected 91 tools and completed 70/70 requests. The remaining Urdu terrain read passed with 133 tools visible. Its gate had shown 91, but the actor's own first listing contained 42 additional names. It invoked none of those names and obtained the answer from `manage_terrain.get_region`. The 71/71 full result includes this first attempt.

After freezing that result, one independent control repeated the Urdu read with the same package, original, seed and ordinary client. Both its SDK preflight and the actual actor listed the exact expected 91 names. It obtained the two requested terrain values, received five successful public replies and produced no host stderr error. Its result is reported separately from the original 71 assignments. [One-shot inventory control](agent_outcome_inventory_control.json)

Core received 58 unsuccessful tool replies in 405 tool calls. Full received 44 in 372. Only 38/71 core originals and 48/71 full originals finished without a failed tool reply. The two catalogs also had 2/227 and 8/238 unsuccessful resource reads, respectively. Every public call received a reply. Frequent rejected tool inputs included missing or incorrectly typed terrain targets and search inputs that were not strings. Host stderr also recorded 42 core and 60 full error records, including 21 and 30 records in cases with otherwise successful public calls and host items. The strict zero-error check failed for this sample.

Five core gates stopped before any prompt was submitted or actor launched. One listed no instance, one found no Editor when reading project information, and three saw 48 tools instead of 32. These records remain in the release evidence. After commissioning, each original was delivered once.

On the intermediate package, all 142 agents returned a final answer. Core missed one collider edit, one input identity read and one package list. Full missed an agent list and a terrain read. Five readiness checks failed before an actor was launched. The same fixed cases subsequently passed readiness and each original was delivered once. The private fixture verified its native terrain write separately from the scored agent trials. Host stderr recorded 58 errors on core and 56 on full. Of these, 25 core and 28 full records came from cases with otherwise successful wire and host items.

### Terrain with the shared setting retained

Six core terrain reads also ran with the terrain tool disabled in the isolated Editor, matching the existing shared setting. Its initial inventory contained 31 core or 90 full tools. Enabling terrain privately for the main trials added one tool to each catalog. All 130 shared preferences had the same digest before and after verification.

| Package | Core reads completed | Failed tool replies / attempts | Failed resource reads / attempts | Agent final answers |
| --- | ---: | ---: | ---: | ---: |
| Branch baseline `4f454080c` | 0/6 | 7/86 | 0/47 | 5/6 |
| Intermediate `658e6070b` | 0/6 | 13/92 | 0/48 | 6/6 |
| Final `e599e7bae` | 0/6 | 13/88 | 0/53 | 6/6 |

In this setting the six originals did not yield the requested terrain mask value. The baseline lost one host final answer. The final run returned all six answers and recorded two host stderr errors in one case.

## Held-out requests

The later paired edit experiment revisits the six edits from these panels using the same originals, a new multilingual panel and the revised default startup profile. [First-turn edit comparison](compact_edit_outcomes.md)

Eight originals cover German, French, Russian and Vietnamese requests about asset labels, prefabs, uGUI and UI Toolkit. The prompts, expected native state, collateral checks and case allocation were sealed before product changes. Both catalogs received the same four reads and four edits on each package. Four additional originals stayed sealed through development of the final correction and ran for the first time on the final package. [Intermediate prompts and first attempts](agent_outcome_holdout_results.json) · [Final paired and first-use reserve results](agent_outcome_holdout_confirmation.json)

| Package and panel | Catalog | Verified reads | Verified edits | Completed | Failed tool replies | Failed resource reads |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Intermediate, primary | Core | 2/4 | 1/4 | 3/8 | 11 | 1 |
| Intermediate, primary | Full | 4/4 | 4/4 | 8/8 | 1 | 4 |
| Final, same primary | Core | 2/4 | 1/4 | 3/8 | 9 | 1 |
| Final, same primary | Full | 4/4 | 4/4 | 8/8 | 3 | 0 |
| Final, first-use reserve | Core | 2/2 | 0/2 | 2/4 | 5 | 0 |
| Final, first-use reserve | Full | 2/2 | 2/2 | 4/4 | 0 | 0 |

Every held-out actor returned a final answer, every public request received a reply and all protected native state was preserved. In both primary runs, two core answers omitted requested values and three core edits left the target unchanged. In the reserve run, core read the prefab hierarchy and Canvas properties. It did not remove the requested asset label or save the requested UI Toolkit edit. Full completed all twelve final requests. On the primary French Canvas edit, the final full actor set the open scene's sorting order from 6 to 18 and correctly reported that the scene was unsaved. The original requested an edit in the open scene. The requests that specified saving a prefab or UI source were checked at their actual destinations.

On the final primary runs, core/full host records included 10/3 failed items and 7/4 stderr failure diagnostics. One full stderr record was a warning about a failed MCP operation. The reserve runs had 5/0 failed items and 2/0 stderr failure diagnostics. The formal sessions used the ordinary Codex CLI 0.172.0 with `gpt-6.2-sol` and medium reasoning. These percentages describe this client, model, sample and catalog configuration.

## Replaying the known failures

The 16 archived tool calls cover eight oversized search pages, five missing original queries and three oversized serialization pages. They reproduced on `4f454080c` over both stdio and authenticated Streamable HTTP. On the candidate, `manage_tools(action="prepare")` produced a schema-valid call in all 16 cases on each transport. For the five missing queries the caller supplied the original text. Thirteen searches returned catalog results on each transport. The three serialization requests reached the connection or native command boundary while preserving the target. In a separate real Editor, inspecting a Camera returned 41 records and a GameObject returned `invalid_target`. [Wire and native controls](agent_outcome_wire_results.json)

Two further drafts from the intermediate run had valid inputs for unrelated operations, `manage_components.set_property` and `manage_input_actions.edit`. Each named the current Editor inside the draft. The intermediate server rejected them until the client repeated that selection on the outer request. With the same archived arguments and matching fixture selection, the final server prepared both unchanged over stdio and HTTP. Drafts naming a different or unavailable Editor still failed. This control prepared calls without executing their edits. [Scoped draft comparison](agent_outcome_wire_results.json)

A dependency-free Node client exercised initialization, tool discovery, call preparation and a scoped resource read. The Python SDK exercised both transports. These protocol checks required no model invocation. The native lost-response check discarded the HTTP success reply to a scene activation. An independent SDK connection retrieved the receipt and observed the target scene without issuing a second public edit. [Node client](agent_outcome_wire_node.mjs) · [Native receipt observations](../../docs/execution-receipts-ledger.md)

## Offline verification

Use the project's existing Python environment. The report verifiers check the published evidence and totals from an ordinary checkout and run in the tooling test suite. Add `--require-perfect` to an outcome verifier to check the completion and error targets.

```console
python tools/experiments/agent_outcome_wire.py
python tools/experiments/verify_agent_outcome_originals.py
python tools/experiments/verify_agent_outcome_holdout.py
python tools/experiments/verify_agent_outcome_holdout_confirmation.py
```
