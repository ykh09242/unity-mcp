# Scoped observation bindings confirmation

Package `4903d8416` pins server `5376ce9f6` and skill `d5a080284`. Each catalog ran the same 13 sealed requests across 11 languages. Each request was submitted once in a fresh Codex CLI session. The installed host, model, reasoning effort and independent Unity, disk and final-answer checks matched the preceding study. [Preceding study](connection_observation_confirmation.md) · [Primary results](resource_bindings_confirmation_results.json) · [Korean results](korean_resource_bindings_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch host errors |
|---|---|---:|---:|---:|---:|---:|---:|
| `0324c39b7` | Core | 13/13 | 13/13 | 1/146 | 0/0 | 0/20 | 0 |
| `0324c39b7` | Full | 13/13 | 13/13 | 2/147 | 0/0 | 0/21 | 0 |
| `4903d8416` | Core | 13/13 | 13/13 | 0/141 | 0/6 | 0/21 | 0 |
| `4903d8416` | Full | 13/13 | 13/13 | 1/143 | 0/3 | 0/22 | 0 |

The three requests with observation routing errors on `0324c39b7` completed cleanly in this run. The remaining failure was in the Full Japanese Canvas turn. It asked `manage_tools.describe` for the unadvertised `manage_components.get_components` operation. The rejection provided a tool-only description call, which the agent used. It then read the target and reference Canvas through `read_unity_resource`, saved sorting order `-12` and verified the protected reference settings and hierarchy. The failed host item mirrors the one public tool rejection. Every public request received a reply.

All 43 successful skill reads matched the pinned bundle bytes. The direct reader served 34 of them, with the remainder verified through the legacy tool and native resource routes. Skill reads through tools contribute to the tool totals. The native observations and independent disk checks confirmed exact edits and preservation of protected state. Both Turkish turns left the scene open and unsaved with only the collider trigger changed.

Both Korean turns applied the exact continuation returned by a successful UXML preview. They saved and read back the requested tooltip, including its final question mark, and preserved the visible label, protected source bytes, asset metadata and open scene. Both finals scoped their verification to the saved source. [Frozen Korean request](korean_edit_request.json)

Separate SDK controls on both catalogs checked the advertised observation bindings under the legacy and `2026-07-28` protocol modes. The fixture's ownership check was corrected to match Unity's reported project data path before any primary request was submitted. Instance, Editor, tag and menu observations then matched their native resource or fixed tool counterparts. Both owned Editors exited and all 130 shared preference values matched their starting fingerprints.

## Replay

The evidence replays exit successfully. The combined `--require-perfect` check returns exit code 1 because Full had one failed tool reply.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/resource_bindings_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_resource_bindings_confirmation_results.json --include-primary
```
