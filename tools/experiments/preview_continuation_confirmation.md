# Preview continuation confirmation

Package `71efe67fe` pins server `4ae402b31` and skill `27438f9a6`. Core and Full each received the same 13 sealed requests across 11 languages, once per request in a fresh Codex CLI session. The model, reasoning effort and native-state, disk and final-answer rubric matched the preceding study. [Preceding study](action_contract_confirmation.md) · [Primary results](preview_continuation_confirmation_results.json) · [Korean results](korean_preview_continuation_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch router records | Host approval rejections |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `fb6f96ef3` | Core | 12/13 | 12/13 | 0/155 | 0/15 | 0/26 | 2 | 0 |
| `fb6f96ef3` | Full | 13/13 | 13/13 | 0/150 | 0/13 | 0/20 | 1 | 0 |
| `71efe67fe` | Core | 13/13 | 13/13 | 1/150 | 1/9 | 1/22 | 0 | 1 |
| `71efe67fe` | Full | 12/13 | 12/13 | 2/152 | 0/9 | 0/22 | 6 | 0 |

Every public request received a reply. All 43 successful skill document reads matched the pinned bundle. Tool-based document reads also count toward tool calls. The primary trials completed 12/12 in Core and 11/12 in Full. Independent Unity observations checked the requested target and protected state, with disk comparisons for the saved UXML, material and audio settings. Both isolated Editors exited with all 130 shared preference values matching their starting fingerprints.

## Continuation outcomes

Both Korean actors used the exact `follow_up.next_call` supplied by the successful preview. They saved and read back the specified Korean tooltip, including the final question mark. Independent disk evidence confirmed the visible label, neighboring elements and metadata were preserved. Their final answers described the saved file and scoped the observation to the source. This was 2/2 on the measured package, up from 1/2 in the preceding study. [Frozen request](korean_edit_request.json) · [Korean results](korean_preview_continuation_confirmation_results.json)

Core used the guarded continuation for the Russian tooltip and saved the exact literal. Full previewed and prepared the exact write, then stopped after reading client sandbox metadata in a routing diagnostic. The diagnostic marked Editor edit authority as untested. Full reported a read-only session and left the file unchanged. The independent SDK control successfully applied the guarded source edit from a different client working directory on both catalogs. It also checked stale-hash and wrong-Editor rejection before writes and verified scene preview token apply, replay and undo.

## First-attempt failures

- **French Canvas.** Core tried to read a host-local `SKILL.md` URI from the MCP server. The server rejected the resource request, which also counts as a failed document read. Core then read the packaged guide via `manage_tools.read_skill`. Full logged two bursts of three missing-`server` router records near two successful scene-save preparation requests. The logs identify the missing field and leave the intended wrapper calls unknown. Both actors saved the correct Canvas order and preserved the reference.
- **Russian tooltip.** Full's request combining the `skills` catalog group with a query received a failed reply before the successful preview. The later decision to stop after preparing the write accounts for the incomplete edit.
- **Turkish collider.** Core's first preview with `isTrigger` was rejected by the native allowlist. The host then rejected a distinct draft proposing an extra size change to `(1, 1, 1)` before it reached the server. Core's final preview targeted only `m_IsTrigger`. Full submitted `fields` as an operation selector to describe `inspect_serialized_object` and recovered by describing the tool and reading the collider by property. Both actors applied the exact scene preview continuation, preserved the original collider size and left the scene open and unsaved. Both answered in Turkish.

## Replay

The evidence replays exit successfully. The combined `--require-perfect` check exits with status 1 for this cohort.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/preview_continuation_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_preview_continuation_confirmation_results.json --include-primary
```
