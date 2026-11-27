# Hosted Unity edits after the heartbeat and tool-contract changes

**Core completed 10/12 first attempts and full completed 12/12.** The previously measured package completed 12/12 in core and 11/12 in full. All 24 current actors reached the requested Unity state and preserved protected values. Two core slots fell short of the original completion rules. One actor was shown 29 unexpected tools. Another linked its correctly saved file to the wrong directory. Each of the 24 fresh hosted turns received one sealed original, with one submission per slot. [Case and call ledger](heartbeat_edit_confirmation_results.json) · [Previous measurements](compact_edit_confirmation.md)

| Catalog | Verified completions, previous → current | Failed Unity tool calls | Failed resource reads | Failed host tool invocations | Host error records |
|---|---:|---:|---:|---:|---:|
| Core | 12/12 → 10/12 | 3/78 → 1/98 | 2/44 → 0/34 | 5/124 → 1/133 | 8 → 4 |
| Full | 11/12 → 12/12 | 2/85 → 2/121 | 0/39 → 1/44 | 2/130 → 3/172 | 5 → 11 |

All 24 received final answers. Review judged 23 accurate. Every public request received a reply. Of the 22 verified completions, 19 had no rejected public request and three recovered from a rejection. Both core slots that fell short received successful public replies throughout.

## Native outcomes

| Original request | Observed result in both catalogs |
|---|---|
| Add a shipment label | Added only `Ready-Dispatch` and kept both previous labels. The core slot had an unverified catalog condition. |
| Disable a prefab child | Saved `WarningPlacard` inactive and preserved the open scene. |
| Change visible UXML text | Saved exactly `Send to review` and preserved the tooltip. |
| Set a Canvas sorting order | Changed `Status Canvas` from 6 to 18 and preserved the reference Canvas. |
| Remove a route label | Removed only `Draft-Safety` and preserved the file contents. |
| Change a UXML tooltip | Saved exactly `Opens the handover log` and preserved `Handover queue`. The core final contained an incorrect destination link. |
| Rename a prefab child | Saved `Service/ReturnValve` and preserved `Intake/Valve`. |
| Save a material render queue | Changed 2011 to 2457 in both loaded and serialized values. |
| Save a Canvas sorting order | Changed `Dispatch Overlay` from 14 to −12, saved the scene and preserved the hierarchy. |
| Move a prefab child | Saved `Bracket/Beacon` at local position (1, 3, −2) and preserved other transforms. |
| Disable audio preload | The loaded importer and saved metadata both reported false. The source WAV was unchanged. |
| Enable a collider trigger | Enabled `isTrigger`, preserved size (2, 3, 4) and left the scene dirty without saving. |

The first core shipment-label slot passed its SDK readiness check with the commissioned 61 tools. The actor's own first complete `tools/list` reply contained **90 tools**. It included all 61 commissioned bindings plus 29 others, including `manage_build`, `manage_terrain` and `delete_script`. These extra bindings were also absent from the commissioned full inventory. The actor used only `asset_labels`, and Unity and its answer both showed the correct labels. Each of the other 23 first inventories matched its commissioned catalog.

The readiness check and the actor started separate stdio servers. Readiness read two resources before listing tools. The actor requested its tool list immediately after initialization. The actor server log recorded an empty startup discovery. It found the selected Editor during the first request, returned the 90 names, and synchronized that Editor's tool settings about ten seconds later. This left the actor's first response subject to the server defaults. The original wire reply and its grading remain in the ledger.

The previous full tooltip actor saved an extra period. This time both actors saved the intended literal without the period. Full prepared two calls with an exact-text check. Core wrote the same correct text and verified the persisted source, then generated a Markdown link rooted in its temporary client directory. The actual file was in the owned Unity project. Under the existing destination-accuracy rule the core verdict was `incomplete_answer`.

## Rejected requests and host errors

Both audio actors asked `manage_tools` to describe the unsupported operation `get_import_policy` for `manage_audio`. After describing the tool, each used `read_import_settings` and `apply_import_settings` successfully. Full also tried to read a local audio skill file as an MCP resource. The server rejected the unregistered URI, and the actor subsequently read the advertised bundled skill.

The remaining tool rejection occurred after the full collider actor successfully changed the component. It passed `refresh_if_dirty` to `scene_inspect` with `action=get_active`. The otherwise identical request succeeded after it removed that argument. The actor left the scene unsaved as requested. Across the current panel, no public rejection reported an unavailable selected Editor or a malformed `exact_text` argument.

| Current panel | Catalog | Completed | Failed tools | Failed reads | Failed host invocations | Host error records |
|---|---|---:|---:|---:|---:|---:|
| Original six | Core | 4/6 | 0/33 | 0/17 | 0/51 | 1 |
| Original six | Full | 6/6 | 0/51 | 0/23 | 0/80 | 2 |
| Multilingual six | Core | 6/6 | 1/65 | 0/17 | 1/82 | 3 |
| Multilingual six | Full | 6/6 | 2/70 | 1/21 | 3/92 | 9 |

The four failed public replies had MCP error markers. Two also had domain-error markers. Host logs recorded four failed invocations, nine routing errors before any public request and two other stderr errors, for 15 records in total. Seven routing errors omitted the host server selector and two named an unregistered server. Host invocation denominators count all terminal host tool records. The host and public records can describe the same failure.

## Conditions and verification

The comparison package was `194d3dce1`. The new frozen package was `64f66bbe7`, with server `9c6cbe71b` and bundled skill `5875fd66f`. The same installed Codex CLI 0.172.0 executable ran `gpt-6.2-sol` at medium effort against Unity 6000.0.69f1. Original UTF-8 requests, seeds, catalog alternation, 360-second turn limit, native observer, fixture controls and grading matched the previous study. Each client used its original request as the entire user input. One panel startup ended before any case directory or submission existed. The next startup processed only previously unattempted slots.

The independent SDK readiness check matched on its first call for every slot. Core had 61 commissioned tools and full had 91, with descriptor sizes of 201,993 and 375,991 UTF-8 bytes respectively. Both catalogs advertised 35 resources and 13 templates. Forty successful actor reads matched the bundled skill bytes.

All 12 legal native edits and 12 deliberate protected-neighbor changes passed the observer controls before submission. Four additional saved-field controls passed. For both UXML edits, the material and the audio importer, the harness copied the asset and metadata before launch and immediately after the client exited, before contacting Unity again. All eight pairs confirmed the intended field and preserved the other bytes. The actors produced no host file changes. The owned Editor exited and all 130 shared tool preferences retained their original values.

The offline verifier checks the current ledger against its frozen digest and predecessor. With the private root it also replays the original inputs, wire replies, host events, native state, first inventories, disk copies and process cleanup. It reconstructs the pinned Git archives and compares all 1,600 package and 1,429 server members. The strict perfect gate exits with status 1 for this measurement. [Verifier](verify_heartbeat_edit_confirmation.py)

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py
python tools/experiments/verify_heartbeat_edit_confirmation.py --require-perfect
```
