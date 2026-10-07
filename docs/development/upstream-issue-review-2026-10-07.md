# Upstream issue review — 2026-10-07

Review of the 49 open [CoplayDev/unity-mcp issues](https://github.com/CoplayDev/unity-mcp/issues)
against this fork. An open upstream issue does not establish that the same defect exists
here. Decisions below distinguish implemented changes, existing coverage, and proposals
without enough evidence for a compatible local change.

The broad formatting baseline is commit `27d703b3`. Functional changes described here
are committed separately by behavior. Persistent style rules live in [FORMATTING.md](../FORMATTING.md).

## Changes selected for this fork

| Upstream issue | Implementation and boundary |
| --- | --- |
| [#1439](https://github.com/CoplayDev/unity-mcp/issues/1439) CodeDom Object alias | `ExecuteCode` adds the `UnityEngine.Object` alias and a Roslyn installation hint on fallback compilation failure. Existing execution restrictions remain enforced. |
| [#1438](https://github.com/CoplayDev/unity-mcp/issues/1438) Build output deletion | Dedicated `manage_build` cleanup for descendants of `Builds/`, with dry run by default, explicit deletion, link/traversal checks and bounded work. Does not relax arbitrary `execute_code` file deletion. |
| [#1437](https://github.com/CoplayDev/unity-mcp/issues/1437) Play startup readiness | Persistent readiness jobs survive domain reload; support scene-load/first-simulation-frame milestones, polling, bounded wait and cancellation. Does not promise rendered output or completion of user async initialization. |
| [#1436](https://github.com/CoplayDev/unity-mcp/issues/1436) Game View size | Get/set fixed resolution or aspect/preset and restore the previous selection with a bounded token. Resolution and pixel budgets apply. |
| [#1435](https://github.com/CoplayDev/unity-mcp/issues/1435) Prefab overrides | Paged inspection and explicit apply/revert selectors. Exact destination checks, Undo, nested/structural guards and unsafe dependency rejection preserve prefab ownership. |
| [#1434](https://github.com/CoplayDev/unity-mcp/issues/1434) Console transport noise | Exclude recognized MCP transport prefixes before pagination; `include_mcp_logs` opts back in. User log messages are not removed by a broad substring match. |
| [#1411](https://github.com/CoplayDev/unity-mcp/issues/1411), [#891](https://github.com/CoplayDev/unity-mcp/issues/891) Reload dialog stalls | Heartbeat age, activity duration, stalled-state diagnostics and actionable preflight errors. Native modal state remains unknown; no dialog dismissal or scene discard. |
| [#1408](https://github.com/CoplayDev/unity-mcp/issues/1408) Input simulation | Opt-in `manage_input`: targeted uGUI click and optional Input System keyboard/mouse/touch simulation in Play Mode. Owned devices, bounded duration/update counts and pause/exit/reload cleanup; no OS input. |
| [#1390](https://github.com/CoplayDev/unity-mcp/issues/1390) Linux/Vulkan test hang | Test-job initialization/age/graphics diagnostics and reproduction guidance. Linux/Vulkan native cause is unverified; a diagnostic improvement is not an OS hang fix. |
| [#1299](https://github.com/CoplayDev/unity-mcp/issues/1299), [#1210](https://github.com/CoplayDev/unity-mcp/issues/1210) Shared server lifetime | Per-project launch ownership and keep-running preference, enabled by default. Authenticated graceful shutdown atomically closes admission only with no admitted/registered peers. No PID-kill fallback. |
| [#1283](https://github.com/CoplayDev/unity-mcp/issues/1283) View recording | Bounded asynchronous `manage_recording` Game/Scene MP4 jobs, status/stop, output containment and interruption cleanup. Silent variable-frame-rate output; Linux and headless capture are unsupported. |
| [#1278](https://github.com/CoplayDev/unity-mcp/issues/1278) Stop Server failure | Explain ownership/authentication/unsupported-runner refusals. Retain launch identity until exit is observed. A lost acknowledgement is reported as an unknown outcome. |
| [#1265](https://github.com/CoplayDev/unity-mcp/issues/1265) Background reload recovery | Expose pending/wait/retry/failure states and retain recovery attempts. Native macOS background behavior still needs platform reproduction. |
| [#1261](https://github.com/CoplayDev/unity-mcp/issues/1261) Dependency versions | Show installed versions, allow explicit `package@version`, and exclude already installed packages from Install All. Changing an installed version is explicit. |
| [#1258](https://github.com/CoplayDev/unity-mcp/issues/1258) PanelRenderer | Reflection adapter supports optional PanelRenderer while preserving existing UIDocument. Native Unity 6.5 attachment/reload/rendering remains unverified. |
| [#1255](https://github.com/CoplayDev/unity-mcp/issues/1255) Shader Graph access | Bounded read-only serialized graph inspection through `manage_shader`. Supported serialization is explicit; graph editing, compilation and arbitrary type construction are outside this capability. |
| [#1248](https://github.com/CoplayDev/unity-mcp/issues/1248) Manual stdio config overwritten | Fingerprint ownership is recorded only by explicit Configure. Startup rewriting requires an unchanged recognized generated entry and auto-registration; manual commands/pins and legacy entries survive. |
| [#1238](https://github.com/CoplayDev/unity-mcp/issues/1238) Focus-time freeze | Async, cached and deduplicated uv validation with stale UI result guards replaces the remaining blocking validation path. |
| [#1129](https://github.com/CoplayDev/unity-mcp/issues/1129) Bundle explorer | Read-only assigned bundle names, assets and dependencies through `manage_asset`, with bounded pages. Does not load/extract arbitrary external bundle binaries. |
| [#1090](https://github.com/CoplayDev/unity-mcp/issues/1090) Old stdio process after update | Freeze Python build identity at process startup and observe sanitized metadata on actual stdio commands. Compare immutable commits only; independent Unity/Python version numbers do not imply mismatch. No external client restart or config rewrite. |
| [#828](https://github.com/CoplayDev/unity-mcp/issues/828) Custom prompts | Explicit `--instructions-file` appends bounded UTF-8 project instructions at startup. Default instructions and authorization enforcement remain; this is not a dynamic MCP prompts registry. |

## Existing coverage; no duplicate implementation

| Upstream issue | Current evidence and qualification |
| --- | --- |
| [#1430](https://github.com/CoplayDev/unity-mcp/issues/1430) Python 3.14 custom tools | `custom_tool_service.py` restores wrapped annotations after decorators, before FastMCP registration. Python 3.14 registration is part of local server tests. |
| [#1407](https://github.com/CoplayDev/unity-mcp/issues/1407) Wrong Windows focus | `focus_nudge.py` and `run_tests.py` already scope the process, restore focus, offer opt-out and bound attempts without progress. |
| [#1397](https://github.com/CoplayDev/unity-mcp/issues/1397) Claude configured status | `ClaudeCliMcpConfigurator` handles local/project/user/legacy locations, Windows path variants and linked worktrees. Exact reported client build was not reproduced. |
| [#1394](https://github.com/CoplayDev/unity-mcp/issues/1394) Test job reload loss | `TestJobManager` persists before reload, restores job state/callbacks and finalizes after the original task disappears. Existing lifecycle/reload tests cover this contract. |
| [#1326](https://github.com/CoplayDev/unity-mcp/issues/1326) Trae variants | `TraeCnConfigurator` already exists. A separate TraeWork product needs verified vendor configuration paths/schema and an installation to validate before adding a guessed writer. |
| [#1207](https://github.com/CoplayDev/unity-mcp/issues/1207) WebSocket reload churn | Background reconnect, 250 ms reachability probe, three-failure orphan gate and configurable reconnect window already exist. #1265 adds diagnostics for the distinct background-resume case. |
| [#1177](https://github.com/CoplayDev/unity-mcp/issues/1177) Asset search stalls | Search argument normalization, invalid-folder rejection and loading details only for the requested page already address the reported all-assets load. |
| [#1176](https://github.com/CoplayDev/unity-mcp/issues/1176) Missing screencapture module | `MCPForUnity/package.json` declares the screencapture dependency. Removing required package modules is outside the current package contract. |
| [#1172](https://github.com/CoplayDev/unity-mcp/issues/1172) Missing physics modules | The package explicitly depends on physics and physics2d. Making those modules optional is a separate architecture change. |
| [#1164](https://github.com/CoplayDev/unity-mcp/issues/1164) Reload orphan server | Current reload recovery reconnects the transport; it does not generally spawn a new Python server. New lifetime/ownership checks also prevent unsafe stop/start behavior. The report's proposed cause does not match the inspected path. |
| [#1023](https://github.com/CoplayDev/unity-mcp/issues/1023) Cross-project mutations | Request/session-scoped targets and ambiguous-target rejection already separate instance selection; an explicitly wrong client target is not automatically rewritten. |
| [#837](https://github.com/CoplayDev/unity-mcp/issues/837) Stdio custom discovery | Initial and reconnect synchronization already registers custom tools; `test_stdio_custom_tool_sync.py` covers the path. |

## Not selected without further evidence or a separate product decision

These are not forgotten implementation tasks. They lack a demonstrated defect in this
fork, a verified external contract, or the authorization needed for a release operation.

| Upstream issue | Reason |
| --- | --- |
| [#1402](https://github.com/CoplayDev/unity-mcp/issues/1402) WorkBuddy | CodeBuddy CLI has a configurator; equivalence to WorkBuddy is not established. Avoid guessing another application's config location or overwriting it. |
| [#1396](https://github.com/CoplayDev/unity-mcp/issues/1396) ZCode | Needs an authoritative MCP configuration contract and client validation. Existing manual configuration remains available. |
| [#1393](https://github.com/CoplayDev/unity-mcp/issues/1393), [#1115](https://github.com/CoplayDev/unity-mcp/issues/1115) DeepSeek | A model provider name alone does not identify an MCP client configuration format. A verified Harness/client integration is needed. |
| [#1215](https://github.com/CoplayDev/unity-mcp/issues/1215) Empty unsupported tool call | Report says unrelated shell/tool calls also fail with an empty name. No server-side dispatch payload or reproducible Unity defect is provided. |
| [#1102](https://github.com/CoplayDev/unity-mcp/issues/1102) Double-quoted argument values | Requires the actual OpenCode/llama.cpp payload and current reproduction. Globally stripping quotes would corrupt legitimate strings and menu paths. |
| [#1064](https://github.com/CoplayDev/unity-mcp/issues/1064) Unity 2020 support | Below the fork's Unity 2021.3 support floor; requires a deliberate compatibility policy and separate version matrix. |
| [#1052](https://github.com/CoplayDev/unity-mcp/issues/1052) Missing HTTP session ID | Use the negotiated MCP transport/session contract or the existing local CLI route. Do not add URL/cookie token workarounds to compensate for incomplete client initialization. |
| [#1050](https://github.com/CoplayDev/unity-mcp/issues/1050) Semantic sidecar | New external service/dependency, ownership and distribution decisions exceed a local bug fix. Existing custom tools can host a deliberately selected integration. |
| [#1019](https://github.com/CoplayDev/unity-mcp/issues/1019) Client tool names/invalid JSON | Current schemas and routing docs use discovery rather than guessed client prefixes. Parsing errors before dispatch need the actual client payload; server permissiveness is not a fix for invalid JSON. |
| [#946](https://github.com/CoplayDev/unity-mcp/issues/946) Collection annotations | Current FastMCP/MCP versions differ from the report. Keep typed schemas until a current client rejection is reproduced; do not discard type information speculatively. |
| [#940](https://github.com/CoplayDev/unity-mcp/issues/940) VS Code Claude disconnect | Insufficient connection-stage evidence for a fork change. Diagnose the client executable, environment, auth and transport using current troubleshooting guidance. |
| [#867](https://github.com/CoplayDev/unity-mcp/issues/867) UPM signature | Package signing is a release/distribution operation requiring signing material and separate authorization. No fabricated signature or release was produced. |
| [#817](https://github.com/CoplayDev/unity-mcp/issues/817) VS2026 registry install | Needs the actual registry listing and client metadata that trigger `Unsupported registry name: other`. No evidence that changing the server runtime fixes the installation manager. |

## Verification boundaries

The final review also reproduced and fixed three edge cases: pausing with held synthetic
input, oversized fixed-resolution presets selected by name, and a second server start
while an earlier launch is still alive before binding its port. Ownership remains intact
when process exit is unknown. The shipped server source pin is updated separately to
the reviewed functional commit so default installations receive the same implementation.

Performance work removes shell commands from process-liveness checks, materializes
Shader Graph edge summaries only for the requested page while validating all edges,
and repaints a recording Scene View only when a capture is due and none is queued.
In five local Windows Editor samples, median process lookup time changed from about
512 ms to 0.015 ms; an 8,192-edge graph inspection changed from about 413 ms to 338 ms.
These are fixture timings, not cross-platform guarantees. The Unity Mono allocation
counter was unavailable, so no measured allocation reduction is claimed.

Adjacent regressions found during validation were also corrected: omitted asset paths
cannot fall through to a mutating `Assets/` default, explicit null shader encoding flags
and numeric ScriptableObject values are rejected, and linked ScriptableObject targets
return a structured error. Existing strict integer parsing remains in force.

Use [Testing and Evidence](../../website/docs/contributing/testing.md) for the distinction
between source contracts, reference compilation and native Editor execution. Detailed
run artifacts for this task live under `reports/CS-20261007-upstream-runtime/` and the
task handoff; those local outputs are not shipped as product source.

Windows Unity 6000.0.69f1 exercised the new prefab, input, Game View, readiness and
recording paths. Recording QA uses the public start/status/stop flow in a non-batch
isolated Editor and separately decodes the MP4, including a Screen Space Overlay marker.
Native macOS/Linux behavior and Unity 2021.3/6.5 remain unverified in this environment.
The presence of a compatibility branch or a passing reference compile does not imply
that those native scenarios passed.

Shutdown verification exercises the real authentication and ASGI route with controlled
Uvicorn owners, including admission races and lost acknowledgements. It does not establish
that an actual managed server process exited; no live server was started or terminated.
