# Apply-route confirmation

Package `21ef2c610` pins server `b721756f3` and skill `3dace93bf`. Each catalog ran the same 13 sealed requests across 11 languages. Each request reached the model once in a fresh Codex CLI session. The installed host, model, reasoning effort, and independent Unity, disk, and final-answer checks matched the preceding study. [Preceding study](operation_discovery_confirmation.md) · [Primary results](apply_route_confirmation_results.json) · [Korean results](korean_apply_route_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch host errors | Other host stderr errors |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `163fe032c` | Core | 12/13 | 12/13 | 0/153 | 0/0 | 0/23 | 0 | 0 |
| `163fe032c` | Full | 12/13 | 12/13 | 0/139 | 0/7 | 0/22 | 0 | 0 |
| `21ef2c610` | Core | 12/13 | 12/13 | 0/170 | 0/0 | 0/24 | 0 | 1 |
| `21ef2c610` | Full | 12/13 | 12/13 | 0/162 | 0/4 | 0/22 | 0 | 0 |

All 24 primary requests completed. The two Korean requests accounted for the two incomplete edits in the combined table. All 336 public tool and resource calls returned successful replies, and all 46 Unity skill reads matched the pinned bundle. The Full host also recorded six successful inventory operations for resources and templates.

Both Russian tooltip turns now saved the exact requested text `Opens the handover log`. They dispatched the full `follow_up.next_call` including the source hash and Editor binding, then read back the saved file. Independent checks confirmed the exact source change, retained label, and unchanged metadata. The Vietnamese UXML turns also followed the full continuation and saved the requested label while retaining the tooltip, protected source, and open scene. The verification covered source and native state.

Both Korean turns obtained a successful preview with the same Editor execution provenance and an exact apply call containing the requested final question mark. Both stopped before dispatching it. Their finals reported a read-only workspace. The original tooltip, label, metadata, and native state remained unchanged. The wire and host evidence contains no apply call or approval denial from either turn. [Frozen Korean request](korean_edit_request.json)

The Core Japanese Canvas turn reached sorting order `-12`, saved the scene, and verified the reference Canvas and hierarchy. Its Codex host logged an error installing built-in system skills while removing the existing directory (Windows error 145). The turn continued, and its Unity guide reads matched the pinned bundle. The error is counted as host stderr.

## Host permission context

Windows host diagnostics recorded a disabled sandbox backend within one second of each Korean submission. The records were matched by time and carried no process or thread ID. Codex CLI 0.172.0 maps `workspace-write` to a local `ReadOnly` profile on Windows when its backend is disabled. Its permissions template describes that filesystem scope to the model. The isolated invocation used `--ignore-user-config`, so it did not inherit the user's backend setting. An earlier successful Vietnamese source edit had the same observed backend state. The reason for the different model decisions remains unresolved. [Windows fallback](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/config/src/config_toml.rs#L799-L812) · [Read-only permissions template](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/prompts/templates/permissions/sandbox_mode/read_only.md) · [CLI startup options](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/cli/src/main.rs#L3121-L3155)

## Commissioning and cleanup

The four separate SDK controls passed across Core and Full under the legacy and `2026-07-28` protocol modes. They verified the route provenance for UXML and scene continuations, exact Korean and Russian disk writes, and rejection of stale source hashes and wrong Editor bindings. The capacity gate completed in about 46 seconds on nine successful probes. Available journal capacity rose from 82 to 128 as released records expired. These probes preceded actor submission. Both Turkish turns kept their Box Collider changes in the open scene and preserved the scene file on disk. Both owned Editors exited and all 130 shared preference values matched their starting fingerprints.

## Replay

Public and private evidence replays pass. The primary `--require-perfect` replay returns exit code 1 for the Core host error. The combined `--require-perfect` replay also returns exit code 1 and records both incomplete Korean edits.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/apply_route_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_apply_route_confirmation_results.json --include-primary
```
