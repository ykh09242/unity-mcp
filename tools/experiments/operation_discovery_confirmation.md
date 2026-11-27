# Operation discovery confirmation

Package `163fe032c` pins server `9decbcf3e` and skill `3dace93bf`. Each catalog ran the same 13 sealed requests across 11 languages. Each request reached the model once in a fresh Codex CLI session. The installed host, model, reasoning effort, and independent Unity, disk, and final-answer checks matched the preceding study. [Preceding study](resource_bindings_confirmation.md) · [Primary results](operation_discovery_confirmation_results.json) · [Korean results](korean_operation_discovery_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch host errors |
|---|---|---:|---:|---:|---:|---:|---:|
| `4903d8416` | Core | 13/13 | 13/13 | 0/141 | 0/6 | 0/21 | 0 |
| `4903d8416` | Full | 13/13 | 13/13 | 1/143 | 0/3 | 0/22 | 0 |
| `163fe032c` | Core | 12/13 | 12/13 | 0/153 | 0/0 | 0/23 | 0 |
| `163fe032c` | Full | 12/13 | 12/13 | 0/139 | 0/7 | 0/22 | 0 |

Both Russian turns obtained a successful UXML preview with a `follow_up.next_call` containing the exact requested replacement, source hash, and Editor binding. Both stopped before dispatching that call. Their answers attributed the skipped write to a read-only filesystem, although no apply request or denial was recorded. The source still held the original tooltip. Both were incomplete under the frozen rubric.

The Full Japanese Canvas turn used advertised discovery and resource reads, saved sorting order `-12`, and verified that the reference Canvas settings and hierarchy matched their earlier observations. The preceding run incurred an operation-description error on this request. Every public tool call and resource read in the current run received a successful reply. The Full host also recorded one successful resource enumeration. All 45 skill reads matched the pinned bundle bytes.

Both Korean turns applied the exact next call returned by their UXML previews and read back the saved tooltip with its final question mark. The protected source bytes, asset metadata, visible label text, and open scene matched their original state. Verification covered the saved source and the independent native state. Both Turkish turns changed only the Box Collider trigger, preserved the collider size, and left the scene open with its on-disk copy unchanged. [Frozen Korean request](korean_edit_request.json)

## Commissioning and cleanup

A harness inventory mapping error interrupted the first SDK pass. After correction, all four SDK controls passed across Core and Full under the legacy and `2026-07-28` protocol modes. The controls exercised paginated operation discovery, exact Russian and Korean UXML saves, rejection of stale source and a wrong Editor before writing, and scene preview, apply, and undo.

The repeated SDK setup filled the owned Editor's 128-entry reservation journal with fences that had been released and had yet to expire. After the first hosted request completed, seven pre-delivery readiness checks returned 21 failed project probes. An eighth preflight was interrupted. All eight records were retained, with zero model submissions during those checks. Once the fences expired, the controller verified the same Editor and resumed the 23 unissued primary requests. The table counts the 26 hosted turns. Both owned Editors exited and all 130 shared preference values matched their starting fingerprints.

## Replay

Both evidence replays exit successfully. The combined `--require-perfect` check returns exit code 1 for the two incomplete Russian edits.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/operation_discovery_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_operation_discovery_confirmation_results.json --include-primary
```
