# First-attempt Unity edit follow-up

Core completed **11 of 12** requests and full completed **10 of 12**, one more each than the previous candidate. Core tool failures rose from 12/89 to 19/91. Full tool failures fell from 13/91 to 7/72. Both catalogs preserved the audio edit on disk. Both saved the requested material queue, but only core retained the requested live queue through the end of the turn.

## Conditions

The same 12 sealed original requests ran once in each startup catalog against package `33ebf544d`, server `01eb2d770` and bundled skill `f808ebff8`. The comparison uses the 24 first attempts already recorded for package `46cee3cd3`. Both runs used the same installed client, model, reasoning setting, Unity version, observer binary, original UTF-8 prompts, order and native fixtures. The comparison measures this client's behavior on these requests. [Previous study](compact_edit_outcomes.md)

The installed Codex CLI 0.172.0 started a fresh ordinary `gpt-6.2-sol` turn at medium reasoning effort per slot. Each turn received only the original request. One disposable Unity 6000.0.69f1 Editor hosted the fixtures. Legal native edits and deliberately unrelated edits tested the observer for every original before the first actor. Four additional local positive controls checked disk readback for the material, audio and two UI Toolkit source edits.

For those four originals, the harness read the asset and its metadata from the owned project immediately before starting the client process and on its return, before sending the follow-up Editor snapshot. The verifier decodes the exact edited value and hashes every byte outside the one permitted field, including the source audio, material metadata and neighboring UXML. Strict completion also requires the requested native state, preserved neighbors, the commissioned initial catalog, successful public evidence and an accurate final answer. A failed public call stays in the error total when the same turn later succeeds. [Case and call ledger](compact_edit_followup_results.json)

## Completion and error rates

Each failure fraction counts failed replies over actual requests of that method. Every public request received a reply. Every actor received its commissioned first list of 61 core or 91 full tools. The initial tool descriptors occupied 200,434 and 374,432 UTF-8 bytes respectively. Each catalog included 35 resources and 13 templates. The owned Editor exited and the hashes of the 130 shared tool preferences matched before and after.

The prefab-position request needed a second local SDK readiness probe in both profiles. The first core probe saw 90 tools and the first full probe raised an Editor-identity error. The second probes established the expected 61 and 91 tools before the respective prompts were submitted. Each actor then received its one scheduled turn.

| Panel | Catalog | Prior completed | Follow-up completed | Prior failed tools | Follow-up failed tools | Prior failed resource reads | Follow-up failed resource reads |
|---|---|---:|---:|---:|---:|---:|---:|
| Original six | Core | 6/6 | 5/6 | 3/30 | 2/26 | 0/29 | 0/26 |
| Original six | Full | 5/6 | 6/6 | 4/32 | 0/27 | 0/31 | 0/30 |
| Multilingual six | Core | 4/6 | 6/6 | 9/59 | 17/65 | 1/33 | 0/34 |
| Multilingual six | Full | 4/6 | 4/6 | 9/59 | 7/45 | 1/35 | 1/26 |

Of the 21 successful turns, 13 completed without a rejected public request and eight completed after one or more rejections in the same turn. Two of the three incomplete turns had zero rejected public requests. The host logged 19 error records in core and 26 in full. These include 27 records for failed public replies, 16 full-profile routing errors before any public request, and two further full-profile stderr records. No scheduled turn had an approval rejection or a second model submission.

## Observed edits

- **Source text.** Both Vietnamese label-text edits saved the exact requested string and preserved the remaining UXML and metadata. The Russian tooltip request succeeded in full. Core saved an extra period at the end of the tooltip. Its preview, write and read all succeeded, and the independent disk copy contained the extra period. The previous candidate had missed this tooltip in full.
- **Material queue.** Both actors changed the saved queue from 2011 to 2457. The rest of the material and metadata remained byte-for-byte identical, and the native material GUID matched the captured metadata. Both write replies reported `asset_saved` and `disk_verified=true`. Core ended with the loaded queue at 2457. Full made a successful `get_material_info` call after the write, then the passive native observer read **2000** while the file still contained 2457. The final answer claimed completion. The full slot failed the frozen native-target criterion.
- **Audio import.** Both actors set default `preload_audio_data` to false and reimported. The native readback and serialized metadata agreed. The original WAV bytes, compression, remaining default settings and metadata outside the one preload field were preserved in both runs.
- **Prefab position.** Full left the Beacon at its original local position. The host's unscoped resource and template listings returned empty arrays, and the actor reported that no Editor was connected. Its first tool list contained the expected 91 names. It issued zero Unity tool calls and zero resource reads. Core saved the exact position in the paired request and preserved the other transforms.

## Rejected public requests

The counts below classify each of the 26 failed tool replies once. A schema rejection that mentions several invalid fields counts as one reply. The call ledger retains each binding, reply layer and sanitized reason.

| Rejection | Core | Full |
|---|---:|---:|
| Used `describe_call` as a `manage_tools` action | 6 | 3 |
| Sent a non-string search `query` or `catalog_query` | 3 | 0 |
| Sent `render_queue` outside `render_state` | 2 | 0 |
| Prepared draft had missing fields or extra fields | 2 | 1 |
| Used `operation` or `arguments` with an inapplicable `manage_tools` action | 2 | 0 |
| Sent an unsupported search selection and unexpected fields | 2 | 0 |
| Searched an unknown catalog family | 1 | 0 |
| Used an invalid audio action or target argument | 1 | 1 |
| Requested a catalog page limit above the allowed maximum | 0 | 1 |
| Selected Editor was unavailable when applying a scene edit | 0 | 1 |

The single failed resource read requested an unregistered skill URI in full. The same actor subsequently saved and verified the audio edit. The full collider actor observed the Editor again after the unavailable-instance reply, applied the change and left the scene dirty as requested.

## Corrections after the measured revision

Commit `7ba7252aa` moved shader property inspection to a disposable material copy. Both regression cases failed on the earlier code. On Unity 6000.0.69f1, the repeated public material read, edit and read sequence then left both the passive native queue and the saved queue at 2457. Undo restored 2011 and neighboring state was preserved. The broader Direct3D11 material suite passed 78/78. [Native regression tests](../../TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ManageMaterialRenderStateTests.cs)

Commit `a65ceb08b` changed the startup instructions and connection references to show the callable form `manage_tools(action="describe", tool=name, operation=action)`. The relevant installed-SDK suites passed 29 tests across the legacy and modern protocol modes. Neither correction received a new hosted trial. The 24 first attempts above describe package `33ebf544d`. [Call guidance](../../docs/task-tool-discovery.md)

## Verify the recorded run

Run the offline verifier from the repository root. The perfect gate requires all 24 completions, zero failed or unanswered public requests, zero domain, MCP and host error records, and zero slots left unready before delivery (`pre_delivery_not_ready`). The recovered readiness probes passed that last condition. The gate exits with status 1 on the frozen result.

```sh
python tools/experiments/verify_compact_edit_followup.py
python tools/experiments/verify_compact_edit_followup.py --require-perfect
```
