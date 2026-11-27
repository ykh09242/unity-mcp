# Initial discovery confirmation

Package `0ad7eddf7` pins server `238cc2c55` and skill `3dace93bf`. Each catalog ran the same 13 sealed requests across 11 languages as the preceding study. The hosted runs used the same Codex CLI executable, model, reasoning effort, owned project scope, and independent checks of final answers, Unity state, and disk. [Preceding study](project_scope_confirmation.md) · [Primary results](initial_discovery_confirmation_results.json) · [Korean results](korean_initial_discovery_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch host errors | Other host error records |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `21ef2c610` | Core | 13/13 | 13/13 | 0/147 | 0/10 | 0/21 | 0 | 0 |
| `21ef2c610` | Full | 13/13 | 13/13 | 0/138 | 0/0 | 0/19 | 1 | 0 |
| `0ad7eddf7` | Core | 13/13 | 13/13 | 0/174 | 1/1 | 1/23 | 0 | 2 |
| `0ad7eddf7` | Full | 13/13 | 13/13 | 2/176 | 0/0 | 0/21 | 0 | 2 |

All 26 originals reached the model once and completed through Unity MCP with no host file edits. Both Korean turns applied the exact guarded UXML continuation and read the saved tooltip with its question mark. Their visible label, protected source bytes, and metadata matched the originals. Both Russian tooltip turns and both Vietnamese label turns also applied their exact continuations. The Turkish Full turn completed with the intended unsaved collider change and had zero host errors. [Frozen Korean request](korean_edit_request.json)

The Core Italian material turn sent a host-local skill `file:` URI through Unity's MCP resource reader. The MCP server rejected it as an unadvertised resource. In the same initial group, `read_unity_resource({})` and `search_unity_tools` succeeded. The agent later verified the pinned guide and saved render queue `2457` with the original shader and protected settings. The rejection counts as one wire resource failure and one failed skill read. The failed host item and its stderr record refer to that same rejection.

The Full Japanese Canvas turn passed `strict_selection` and `evaluate_getters` in two component URIs. Both options came from the native resource template in the search reply, whose family summary carried no reader binding. The connection-bound tool rejected both calls and directed the actor to the native reader. The actor successfully read the same components using plain URIs, then saved sorting order `-12` and verified the reference Canvas and hierarchy. Both failed tool replies and their two host item records remain in the totals.

## Initial metadata controls

Codex CLI 0.172.0 renders the first trimmed line of the instructions for a deferred MCP namespace, capped at 250 characters. Four offline prompt-input observations reused the captured tool schemas for each profile and exchanged only setup and inventory messages with a mock MCP connection. The rendered Unity namespace description grew from 74 to 239 characters in both profiles. It now names the connection-bound guide and action search, and says that shared readers may require the listed server and URI. The observations measure the compact surface without a model request. [Pinned client implementation](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/core/src/context/world_state/tools.rs#L50-L83)

Four separate SDK controls on the frozen runtime exercised the first-line guide and search examples under Core and Full in legacy and `2026-07-28` protocol modes. The guide matched the pinned bundle bytes and the search succeeded in each. Standalone inventories exposed 91 Core and 134 Full tools. The hosted sessions used Editor-governed inventories of 62 and 92 tools, respectively. All 26 captured initializations matched the candidate's frozen instruction digest. The first public request by wire order was a successful connection-bound skill read in 17 of 26 turns, compared with 13 of 26 in the preceding study. This measures observed routing in the two cohorts.

Separate offline prompt-input checks staged the installed skill and the release's packaged skill independently. Both runs displayed a local file entry alongside the connection-bound description. These records show the two available paths. The source of the actor's routing decision remains unknown.

## Commissioning and cleanup

The four Editor-connected SDK controls passed for both protocol modes and catalogs. All nine reservation capacity probes succeeded as available entries rose from 78 to 128 in about 46 seconds. All hosted requests passed commissioning before dispatch.

Both owned Editors exited. All 130 shared preference values matched their starting fingerprints. Private replay checks each original and scope through the workspace probe, exact executable arguments, launch receipt, and independently archived Unity and disk evidence.

## Replay

Public and private replays pass. The primary and combined `--require-perfect` checks return exit code 1 for the recorded failures. The Korean-only strict replay passes.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/initial_discovery_confirmation_results.json --require-perfect
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_initial_discovery_confirmation_results.json --include-primary --require-perfect
```
