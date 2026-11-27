# Resource reader ownership confirmation

Package `2024b8f8a` pins server `a268d792f` and skill `dda51947d`. The two cohorts used the same 13 sealed requests across 11 languages, installed Codex executable, model, reasoning effort, and owned project scope. Each cohort used its own frozen product and guide bytes. [Preceding study](initial_discovery_confirmation.md) · [Primary results](reader_ownership_confirmation_results.json) · [Korean results](korean_reader_ownership_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch host errors | Other host error records |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `0ad7eddf7` | Core | 13/13 | 13/13 | 0/174 | 1/1 | 1/23 | 0 | 2 |
| `0ad7eddf7` | Full | 13/13 | 13/13 | 2/176 | 0/0 | 0/21 | 0 | 2 |
| `2024b8f8` | Core | 13/13 | 13/13 | 1/143 | 0/4 | 0/22 | 0 | 1 |
| `2024b8f8` | Full | 13/13 | 13/13 | 0/158 | 0/0 | 0/23 | 0 | 0 |

All 26 originals completed through Unity MCP. Independent checks verified the intended Unity state and save scope. All 45 successful skill reads matched the new pinned bundle. The root guide SHA-256 begins `9e5189dd` in this release and `eb1f9ec9` in the preceding release. Both Korean, both Russian, and both Vietnamese UXML turns dispatched their exact guarded continuations and read back the edits. The Korean tooltip retained its final question mark, visible label, and protected source and metadata. [Frozen Korean request](korean_edit_request.json)

The Italian Core material turn read the packaged guide through `read_unity_resource({})` and saved render queue `2457`. Its earlier local-file resource rejection did not recur. The Japanese Full Canvas turn read the resource catalog before four successful component reads using `strict_selection=true&evaluate_getters=false`. The catalog had identified that option domain for the connection-bound reader. The turn saved sorting order `-12` and verified the protected Canvas and hierarchy. Both option-related failures in the preceding run were absent.

The Turkish Core collider turn submitted `refresh_if_dirty=false` to `scene_inspect(action="get_active")` after changing the trigger. The parameter is supported by the earlier `find_gameobjects` call and is absent from the scene operation's advertised schema. Argument validation rejected the request. The next call removed that parameter and succeeded. The one failed host item corresponds to the same rejected tool call. Independent Unity state and disk checks confirmed the collider size and unsaved edit. The French Full turn truthfully reported an unsaved change for a request that allowed an in-memory edit.

## Resource discovery and controls

The frozen runtime's 247-character initial description names the connection-bound guide and search, distinguishes host file paths from server resources, and instructs clients to pair each URI with its listing server. Four standalone SDK checks covered both catalogs in legacy and `2026-07-28` protocol modes. They verified the new guide bytes and the advertised reader domains. The native component templates include `evaluate_getters`, and the connection-bound adapter's advertised domain restricts that option to `false`. The standalone inventories contained 91 Core and 134 Full tools. Hosted initializations matched the released instruction digest in all 26 turns, with Editor-governed inventories of 62 and 92 tools.

Four Editor-connected SDK checks exercised the advertised domains and compared safe adapter reads against the native resource. A separate fixture passed all four combinations of Core and Full with legacy and automatically negotiated `2026-07-28` sessions on the frozen runtime and an owned Editor. Each verified the 247-character initial description and exact guide bytes. The adapter accepted an omitted or `false` value for `evaluate_getters`, and preparation rejected `true`. An authorized native read with `evaluate_getters=true` returned the same component. The fixture preserved the scene and its disk bytes, submitted zero model requests, and closed its Editor with all 130 shared preferences intact.

## Commissioning and cleanup

Nine successful capacity probes observed 76 to 128 available reservation entries over about 46 seconds. All hosted requests passed the commissioning gate before dispatch.

Both owned study Editors exited. All 130 shared preference values matched their starting fingerprints. All 26 hosted submissions retained their own workspace, exact sealed input, and launch receipt.

## Replay

Public and private replays pass. The primary and combined `--require-perfect` checks return exit code 1 for the Turkish Core tool rejection. The Korean-only strict replay passes.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/reader_ownership_confirmation_results.json --require-perfect
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_reader_ownership_confirmation_results.json --include-primary --require-perfect
```
