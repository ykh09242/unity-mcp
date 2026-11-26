# First-attempt Unity edits after the catalog and material corrections

**Core completed 12/12 edits and full completed 11/12**, up from 11/12 and 10/12 on the previous package. Across both catalogs, rejected Unity tool calls fell from **26/163 to 5/163**. Failed resource reads rose from **1/116 to 2/83**. The outstanding edit was the full catalog’s Russian tooltip. Its preview, write and read all succeeded, but the actual saved text contained one extra period. [Previous study](compact_edit_followup.md) · [Current case and call ledger](compact_edit_confirmation_results.json)

## Matched conditions

The study submitted the same 12 sealed UTF-8 requests once in each catalog, with the original case order, seeds, per-case catalog order and native fixtures. Each of the 24 fresh ordinary hosted turns received only its original request. All 24 started and finished with one submission each. The installed Codex CLI 0.172.0 binary, `gpt-6.2-sol` at medium effort, Unity 6000.0.69f1 and native observer binary matched the prior run. The Unity package was `194d3dce1`, the server was `91527cc5c` and the bundled skill was `bf612cb3b`. The comparison package was `33ebf544d`. A superseded setup performed local fixture checks and closed before any hosted submission.

Before the actors, the observer accepted legal native edits and detected deliberate changes to protected neighbors for all 12 fixtures. Four additional controls checked the serialized material, audio importer and both UXML edits. For those four cases the harness copied the asset and its metadata from disk before each actor and immediately after client exit, before querying Unity again. The verifier compares the exact saved field and every byte outside that field. It also checks the loaded native state, scene save requirements, protected neighbors, commissioned first inventory, successful public evidence and final answer. No actor used a host-side file edit.

All 24 SDK readiness probes succeeded on their first check, and all 24 actors received their commissioned first list. Core exposed 61 tools and full exposed 91. Their descriptors occupied 201,609 and 375,607 UTF-8 bytes. Each catalog included 35 resources and 13 templates. Independent Git archive reconstruction matched every archived package and server member. The actor Editor exited and all 130 shared tool preferences matched the original values.

## Outcomes and requests

In each cell, the prior measurement precedes the current measurement. Tool and resource failures use their respective public wire requests as denominators. The host tool denominator includes all terminal host tool invocation records, including failed invocations and host-side catalog operations. Host error records also include stderr and can overlap failed public replies.

| Panel | Catalog | Completed | Failed Unity tool calls | Failed resource reads | Failed host tool invocations | Host error records |
|---|---|---:|---:|---:|---:|---:|
| Original six | Core | 5/6 → 6/6 | 2/26 → 0/28 | 0/26 → 1/26 | 2/56 → 1/56 | 2 → 2 |
| Original six | Full | 6/6 → 5/6 | 0/27 → 1/37 | 0/30 → 0/19 | 0/61 → 1/60 | 6 → 3 |
| Multilingual six | Core | 6/6 → 6/6 | 17/65 → 3/50 | 0/34 → 1/18 | 17/103 → 4/68 | 17 → 6 |
| Multilingual six | Full | 4/6 → 6/6 | 7/45 → 1/48 | 1/26 → 0/20 | 8/74 → 1/70 | 20 → 2 |
| Both | Core | 11/12 → 12/12 | 19/91 → 3/78 | 0/60 → 2/44 | 19/159 → 5/124 | 19 → 8 |
| Both | Full | 10/12 → 11/12 | 7/72 → 2/85 | 1/56 → 0/39 | 8/135 → 2/130 | 26 → 5 |

Every public request received a reply. All seven current rejected replies had an MCP error marker. None had a domain error marker. The host recorded seven failed invocation events, two routing errors before a public request and four other stderr error records. There were no approval errors. Of the 23 completions, 18 had no rejected public request and five recovered from at least one rejection. The incomplete tooltip had no rejected request.

## Native result for each original

| Requested edit | Core | Full |
|---|---|---|
| Add shipment label | Added `Ready-Dispatch`, retained both existing labels | Same |
| Deactivate prefab child | Saved `WarningPlacard` inactive | Same |
| Change visible UXML text | Saved exactly `Send to review`, tooltip preserved | Same |
| Set Canvas sorting order | Changed 6 to 18, other Canvas preserved | Same, with a permitted scene save |
| Remove route label | Removed only `Draft-Safety` | Same |
| Change UXML tooltip | Saved exactly `Opens the handover log` | Saved `Opens the handover log.` |
| Rename prefab child | Saved `Service/ReturnValve`, retained `Intake/Valve` | Same |
| Save custom material queue | Loaded and saved queue both 2457 | Same |
| Set Canvas order and save scene | Set −12 and saved the scene | Same |
| Move prefab child | Saved local position (1, 3, −2), other transforms preserved | Same |
| Disable audio preload | Loaded and serialized preload false, source WAV unchanged | Same |
| Enable collider trigger without saving | Trigger enabled, size preserved, scene left dirty | Same |

Both material actors called `get_material_info` after the write, then read render state again. Both independent native and on-disk reads returned 2457, with the remaining material and metadata bytes preserved. The earlier full actor had ended at live queue 2000 despite saving 2457. The earlier full prefab-position actor had stopped without issuing any Unity requests. This time the full actor inspected the prefab, previewed the change and saved the requested position.

The Russian tooltip original places a sentence-ending period immediately after the unquoted English phrase. The presealed expected tooltip excludes the period. This time core saved the expected text and full included the punctuation. The preceding run showed the reverse. Neither current tooltip actor invoked the optional exact-text preparation check. The full actor accurately displayed the value it had saved in its final message, which missed the requested literal as graded.

## Rejected calls

| Reason for rejected Unity tool call | Prior | Current |
|---|---:|---:|
| Used `describe_call` as a catalog action | 9 | 0 |
| Used a non-string search query | 3 | 0 |
| Sent the material queue outside `render_state` | 2 | 0 |
| Prepared target arguments omitted required fields or included extra fields | 3 | 0 |
| Sent `operation` or `arguments` to an incompatible catalog action | 2 | 0 |
| Used unsupported search selection and unexpected fields | 2 | 0 |
| Searched an unknown catalog family | 1 | 0 |
| Used an invalid audio action or target argument | 2 | 0 |
| Exceeded the catalog page limit | 1 | 0 |
| Selected Editor unavailable | 1 | 2 |
| Supplied an invalid `exact_text` preparation argument | 0 | 3 |
| **All rejected tool replies** | **26** | **5** |

All three `exact_text` rejections occurred in the same core prefab-rename turn. The actor first sent a non-list value, then supplied `field` and `text` where `path` and `expected` were required, and then sent a scalar path. The fourth preparation succeeded and checked one literal. The actor completed the rename. In full, an unavailable-instance reply occurred during the prefab deactivation write and again during call preparation for the scene save. Both actors recovered. Across the entire run, core used the `describe`, `prepare` and `list_instances` actions 9, 6 and 3 times respectively. Full used them 10, 8 and 2 times.

Both failed resource reads were in core. One requested a local skill file as an MCP resource, and one requested an unregistered audio skill URI. Neither original was blocked. The prior run had one unregistered skill-resource failure in full.

## Separate SDK controls

After all actors finished, a direct SDK connection listed the owned Editor, read the live material, described `set_render_state` and successfully prepared the returned draft for the target asset. The material remained unchanged. An initial negative probe selected `manage_ui`, which was unavailable in core. The server refused the tool before the literal check and the tooltip remained unchanged.

A separate SDK-only Editor then loaded the same frozen package and preferences. Its first core inventory advertised `ui_source`. On the same fixture a draft with an extra period returned a literal mismatch and no prepared call. The otherwise identical draft with the exact text returned a prepared call and one checked literal. Neither preparation changed the native or serialized tooltip. This second Editor exited with all 130 shared preferences unchanged. These controls used no hosted turns and are excluded from every actor denominator.

## Verification

From the repository root, run the offline verifier. The public mode checks the frozen current and previous payloads. With the matching private root it also checks original bytes, client events, wire replies, first inventories, native observations, archived products, disk copies, separate SDK controls and cleanup receipts. The perfect gate exits with status 1 on this result.

```sh
python tools/experiments/verify_compact_edit_confirmation.py
python tools/experiments/verify_compact_edit_confirmation.py --require-perfect
```
