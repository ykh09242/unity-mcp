# Preflight refresh policy confirmation

Package `73d7441da` pins server `0d1c79ef9` and skill `dda51947d`. Both releases were tested with the same 13 sealed requests across 11 languages. Each agent received the original request as its only task-specific input. The installed Codex CLI `0.172.0` used `gpt-6.2-sol` at medium reasoning effort with an isolated Windows Unity `6000.0.69f1` project. [Preceding study](reader_ownership_confirmation.md) · [Primary results](preflight_policy_confirmation_results.json) · [Korean results](korean_preflight_policy_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Host error records |
|---|---|---:|---:|---:|---:|---:|---:|
| `2024b8f8` | Core | 13/13 | 13/13 | 1/143 | 0/4 | 0/22 | 1 |
| `2024b8f8` | Full | 13/13 | 13/13 | 0/158 | 0/0 | 0/23 | 0 |
| `73d7441d` | Core | 13/13 | 13/13 | 0/175 | 0/0 | 0/25 | 0 |
| `73d7441d` | Full | 13/13 | 13/13 | 0/176 | 0/0 | 0/25 | 0 |

Each of the 13 requests reached the model once per catalog, and all 26 edits completed through Unity MCP. All 351 public tool calls succeeded and all 50 skill reads matched the pinned bundle. There were zero failed or missing replies, approval errors, host stderr errors, or pre-dispatch routing errors. The preceding Core host error record refers to its one rejected tool call.

All six UXML turns applied their exact guarded continuation and read back the saved source. The Korean turns preserved the final question mark, visible label, metadata, and protected source bytes. The French Canvas turns accurately reported changes in the open scene, for which the sealed request allowed either save scope. Both Japanese Canvas turns saved the requested order and preserved the reference Canvas and hierarchy. [Frozen Korean request](korean_edit_request.json)

## Refresh policy evidence

All 55 hosted calls with `refresh_if_dirty=false` succeeded across seven tools. Both Turkish collider turns successfully observed the active scene with the option set to `false`, including the Core operation rejected in the preceding cohort. Independent Unity and disk checks confirmed the trigger change stayed in the open scene, with dimensions `2 × 3 × 4` and the scene file preserved.

Separate no-model controls compared both releases on the same operations in isolated Unity Editors. Across both catalogs and both protocol modes, the new package accepted `refresh_if_dirty=false` on the active-scene and asset queries and preserved the pending external change. Omitting the option retained the previous refresh behavior. A separate public-schema comparison checked the 301 tool requests from the preceding hosted cohort and found the old rejected Core call accepted by the new schema. These checks are outside the hosted counts above. [Native and recorded-input comparisons](reader_ownership_confirmation.md#replaying-the-refresh-option-in-unity)

## Commissioning and replay

The four Editor-connected SDK controls passed. Nine capacity probes succeeded while available reservations rose from 76 to 128 in about 46 seconds. Both owned Editors exited, and all 130 shared preference values matched their starting fingerprints.

The public and private `--require-perfect` replays pass. Private replay checked every original submission, its unique workspace receipt and scoped arguments, native outcomes, and disk evidence. All 26 hosted runs negotiated MCP `2025-06-18` and saw Editor-governed inventories of 62 Core or 92 Full tools.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/preflight_policy_confirmation_results.json --require-perfect
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_preflight_policy_confirmation_results.json --include-primary --require-perfect
```
