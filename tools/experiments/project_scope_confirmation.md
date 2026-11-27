# Project-scoped edit confirmation

The two runs used package `21ef2c610`, server `b721756f3`, skill `3dace93bf`, and the same installed Codex CLI, model, reasoning effort, and 13 sealed requests per catalog. The project-scoped cohort used the owned disposable Unity project as the actor's only writable workspace with the Windows elevated sandbox backend. The preceding run used a separate empty actor directory and observed the Windows fallback to local read-only mode. [Preceding run](apply_route_confirmation.md) · [Primary results](project_scope_confirmation_results.json) · [Korean results](korean_project_scope_confirmation_results.json)

| Run | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Pre-dispatch host errors | Other host stderr errors |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Separate actor directory | Core | 12/13 | 12/13 | 0/170 | 0/0 | 0/24 | 0 | 1 |
| Separate actor directory | Full | 12/13 | 12/13 | 0/162 | 0/4 | 0/22 | 0 | 0 |
| Owned project scope | Core | 13/13 | 13/13 | 0/147 | 0/10 | 0/21 | 0 | 0 |
| Owned project scope | Full | 13/13 | 13/13 | 0/138 | 0/0 | 0/19 | 1 | 0 |

All 26 originals reached the model once, and each requested edit used Unity MCP. The host records show zero shell or direct file edits. All 295 public requests received successful replies and all 40 skill reads matched the pinned bundle. Core also completed one resource enumeration.

Both Korean turns dispatched the complete `follow_up.next_call` from a successful preview, including the source hash and exact Editor binding. Each then read back the saved tooltip including the final question mark. The visible label, protected source bytes, asset metadata, and open scene matched their starting state. The Korean turns in the preceding run stopped at the previews. Both Russian tooltip turns and both Vietnamese label turns also applied the exact continuation and saved only their requested field. The checks covered saved source and native state. [Frozen Korean request](korean_edit_request.json)

The installed sandbox probe verified an actual write to a marker inside each owned project and denied a sibling file write. It used the pinned executable without a model turn. The actual hosted launch records bind the same workspace evidence, request, Editor instance, profile, and exact arguments by SHA-256. The replay checks the disabled host features, the Unity-only proxy binding, and successful reviewed tool evidence. Existing Windows host logs near both Korean launch times also recorded selection of the elevated backend. Those records had no process or thread identifier. The same-version comparison shows a difference in outcomes under the two host configurations. The cause of individual model decisions remains unresolved.

The Full Turkish collider turn recorded one client error for a missing `server` argument about five seconds before its first Unity tool call. The client recorded no tool name for that error, and the wire trace contains no corresponding request. The installed client's cross-server resource reader requires both `server` and `uri`. A connection-bound MCP `resources/read` request carries the URI. The available records do not identify which operation the model attempted. All 12 subsequent public tool calls succeeded. They set the trigger, verified the collider size, and confirmed that the scene remained open. Independent checks confirmed that the disk copy stayed intact. The preceding Japanese Core host installation error did not recur. [Installed client reader schema](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/codex-rs/core/src/tools/handlers/mcp_resource_spec.rs#L72-L105) · [MCP resource read](https://modelcontextprotocol.io/specification/2025-11-25/server/resources#reading-resources)

## Commissioning and cleanup

The four SDK controls passed across the two catalogs under legacy and `2026-07-28` protocol modes. Nine successful capacity probes observed the Editor reservation journal rise from 82 to 128 available records in 46 seconds as released records expired.

After the initial readiness probe passed, an operating-system permission error reading the owned Editor's status file interrupted the Full French Canvas setup before submission. The archived raw directory contains five files and records zero hosted submissions, wire requests, or host events. The harness switched its identity check to a native ping that validates the owned Editor's PID, project, and matching instance. The French Full request was submitted once after the main panels. The saved scene reached sorting order `18` and preserved the target render mode, hierarchy, and reference Canvas settings.

Both owned Editors exited. All 130 shared preference values matched their starting fingerprints.

## Replay

Public and private replays pass. The primary and combined `--require-perfect` checks return exit code 1 for the Full host router error. The Korean-only strict replay passes.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/project_scope_confirmation_results.json --require-perfect
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_project_scope_confirmation_results.json --include-primary --require-perfect
```
