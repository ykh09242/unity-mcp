# Unity MCP release notes

Changes in the ykh09242 fork of Unity MCP. Open a version heading for its installation URL, downloads, and verification results. [GitHub Releases](https://github.com/ykh09242/unity-mcp/releases)

## [2.5.0 — Discover tool actions and verify what Unity changed](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.5.0)

Run `unity-mcp tool catalog --tool manage_shader` to see the shader operations in the offline catalog. In a connected MCP client, call `manage_tools` with `action="describe"`, `tool="manage_shader"` and `operation="diagnostics"` for the exact operation schema. The same catalog powers multilingual tool search. [Tool discovery reference](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.5.0/website/docs/reference/tools/core/manage_tools.md)

- **Verify the source you edited.** `manage_verification` offers `script-check`, `editmode-check` and `playmode-check`. Capture a pre-edit baseline with `prepare`, run the check after editing, and inspect `report` for diagnostics, source freshness and gaps in test coverage. [Verification profiles](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.5.0/docs/verification-profiles.md)
- **Inspect the result of an edit.** Supported authoring tools attach scoped observations to `data.verification`, such as retained prefab overrides or values read back from a UI target.
- **Diagnose a shader in place.** Read its cached compiler messages with `diagnostics`, or use `compile_variant` in idle Edit Mode with a specific pass, platform and keyword set.

After upgrading, refresh the bundled client skills and reconnect.

## [2.4.1 — Preview UI prefabs at the scale of a scene Canvas](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.4.1)

Capture a popup at phone resolution using the Canvas it will live under. Resolve the intended parent with `find_gameobjects` in the same Editor, then pass its GameObject instance ID to `capture_prefab` as `ui_parent`. Use an active screen-space Canvas or a RectTransform under one, and replace `12345` with its live ID.

```json
{
  "prefab_path": "Assets/UI/ConfirmPopup.prefab",
  "width": 1080,
  "height": 1920,
  "ui_parent": 12345
}
```

- The preview recalculates the parent's CanvasScaler for the requested resolution and carries over the static parent layout, CanvasGroup alpha, rectangular clipping and nested Canvas boundaries. The capture leaves scene objects in place.
- `data.ui_context` reports the effective scale, its source and preview limits. Without a parent, the tool uses the prefab's authored Canvas or reports a 1× fallback.
- Compare static placement and clipping in the PNG. Use a running Game View for Safe Area scripts, sibling-dependent layout and interactions. [Capture parameters and examples](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.4.1/website/docs/reference/tools/prefabs/capture_prefab.md)

## [2.4.0 — Edit against a known script snapshot and recover partial work](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.4.0)

Read the lines you plan to edit with `unity-mcp --format json script read "Assets/Scripts/Player.cs" --start-line 10 --line-count 20`. Pass the returned `sha256` to `script edit --expected-sha` with your edits. Unity rejects the edit if the file changed since the read. Calls that omit the flag still fetch a hash at edit time.

- **Recover after a partial edit.** Mixed script edits, UI source changes and model imports report which files were already published if a later step fails or is canceled.
- **Target the right Editor.** A local HTTP launch that loses a listener race identifies the ownership conflict and the recovery action. On a local MCP server, set `UNITY_MCP_REQUIRE_EXPLICIT_TARGET=true` and restart the server to require an explicit Unity target for edits, code execution and tests. [Explicit target setup](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.4.0/docs/explicit-local-target-policy.md)
- **Search across loaded scenes.** Try `unity-mcp gameobject find "Player" --scene-scope all_loaded` and pass the returned cursor to continue. Component and material-slot selectors support precise edits when an object has multiple candidates.
- **Work with large reference graphs.** Capture and event pages are bounded by bytes, and collection can continue past an oversized asset.

## [2.3.4 — Speed up cached reference queries in large projects](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.3.4)

When you open a cached incoming or outgoing page of direct Unity asset references, Unity MCP can deliver it with two graph database connections on a supported Editor. The previous release used 11 with file watching and 15 without it. An older Editor without file watching now uses three.

- In a Windows fixture with 35,804 assets and an in-memory Editor response, median outgoing server time with file watching fell from 29.89 to 7.08 ms. The query returned two edges in both versions.
- Mixed pages still check manual, external and runtime references. Concurrent validation preserves newer exclusion observations made by another server.

## [2.3.3 — Prioritize the asset you’re inspecting in the reference graph](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.3.3)

To bring one prefab's dependencies forward while the graph is still collecting, call `manage_reference_graph` with `action="query"`, `node="asset:<guid>"` and `direction="outgoing"`. On a supported Editor, the collector can prioritize that pending asset and continues its regular work between requests. Incoming pages show their progress hint until collection finishes.

Warm queries spend less time verifying and delivering cached results. Median outgoing server time fell from 49.66 to 29.79 ms in a Windows fixture with 35,804 assets and an in-memory Editor response. For the incoming case without a file watcher on a supported Editor, verification went from four Editor requests to two.

## [2.3.2 — Reuse verified references after restarting Unity](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.3.2)

Reopen Unity and continue exploring references in the same project. The collector can reuse static evidence for the same user and project after checking current assets, dependencies and exclusions. Runtime captures, external grants and authored declarations stay scoped to the original Editor session.

- While collection runs, cached queries expose verified direct Unity dependencies after checking the inventory and exclusions. `graph_status.partial_static` marks the partial page.
- Saving the same collection policy preserves the active index. Use `rebuild` to request a fresh collection.
- Unreadable optional source files get a diagnostic while direct Unity dependencies remain queryable.

[Reference graph guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.3.2/docs/REFERENCE_GRAPH.md)

## [2.3.1 — Resume interrupted reference graph collection](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.3.1)

Inspect references while Unity MCP inventories a large project. Collection can continue beyond 128 pages and resume from the last committed page after a transient connection or database failure. An expired or inconsistent snapshot starts a fresh inventory.

- The Editor explorer and graph queries show when more references may appear, even on an empty page. The hint clears when collection finishes.
- Graph collection and reflection reads leave execution journal capacity available for commands that need duplicate-execution protection. If a full journal refuses a command before execution, the response identifies that refusal.

[Reference graph guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.3.1/docs/REFERENCE_GRAPH.md)

## [2.3.0 — Find Unity tools by task and trace URP routing](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.3.0)

When a camera renders with an unexpected URP feature, start with `unity-mcp tool search "URP renderer diagnostics"`. The offline CLI search shows matching tools, support status, and relevant official Unity skill links. [Task discovery guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.3.0/docs/task-tool-discovery.md)

- **Trace the camera's renderer.** The experimental `graphics pipeline-diagnostics --target MainCamera` shows the effective renderer, feature state, and observed Render Graph settings. Use a frame capture to confirm execution. [Graphics diagnostics guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.3.0/docs/GRAPHICS_DIAGNOSTICS.md)
- **Read more without rerunning the job.** Follow the cursor through a large prefab hierarchy. For an oversized MCP result with a retained handle, use `read_response` to retrieve the original output in pages. [Response delivery guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.3.0/docs/response-delivery.md)
- **Trace unexpected configuration.** Run `unity-mcp doctor --config` for redacted CLI configuration sources, or read `mcpforunity://doctor/config` for the serving process.
- **Preserve concurrent script edits.** Publishing detects source conflicts, and recovery checks that an existing script matches the requested contents before reporting a successful creation.
- **Inspect project issues.** Experimental Project Auditor reporting requires an already installed `com.unity.project-auditor` 1.0.1. Starting analysis requires edit and run permissions. [Project Auditor guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.3.0/docs/PROJECT_AUDITOR.md)

## [2.2.1 — Recover after interrupted Unity compilation](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.2.1)

If Unity cancels a compilation and omits its completion event, Unity MCP can now clear the recovery lock when it observes that the supported Bee task terminated and the Editor later settles.

- **Carry the evidence through reload.** The recorded cancellation or fault survives domain reload so the next independent compilation can proceed after the Editor settles.
- **Keep the interrupted job queryable.** It retains its `compile_id` and reports an unknown outcome with `compilation_canceled` or `compilation_faulted` when observed.
- **Check whether your Editor can use this path.** The fallback recognizes a specific Bee task shape, exercised on Unity 6000.0.69f1. Other task shapes still rely on Unity's completion events. [Native job coordination guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.2.1/docs/NATIVE_JOB_COORDINATION.md)

## [2.2.0 — Keep queued work moving through Editor reloads](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.2.0)

A script change can reload Unity while a test run or build is waiting in the queue. With both components on 2.2.0, eligible queued requests can continue after the same Editor reconnects, keeping the original request identity and deadline.

- **Keep an accepted job on track.** Queued builds retain their profiles and effective scene lists, and queued tests retain their filters. Look up an accepted job with its original ID.
- **Continue preparation when the inputs still match.** Queued navigation bakes recheck the saved scene and settings. Eligible asset generation jobs recheck authorization and inputs before making their first provider submission.
- **Recover the original outcome when possible.** A validated stdio or WebSocket reconnect can return a retained result. When started work has an uncertain outcome, inspect the job or the affected state before retrying. Package jobs distinguish the installed state from confirmation that a particular request succeeded.

Continuation requires the original Editor process and current permissions. An Editor or MCP server restart ends continuation for pending commands. [Reload continuation guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.2.0/docs/reload-continuation.md)

## [2.1.9 — Capture prefab previews in an isolated scene](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.9)

To review a UI prefab or model in Edit Mode, call `capture_prefab` for a PNG. With a graphics-capable Editor, it renders a root RectTransform UI or a front view of a model in a temporary preview scene. Edit-time callbacks may run. [Prefab capture reference](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.1.9/website/docs/reference/tools/prefabs/capture_prefab.md)

- **Keep prefab relationships when authoring.** Duplicating instances preserves their prefab connection, direct component edits record prefab overrides, and a prefab save protects an occupied destination.
- **Pick up a test investigation after reload.** Job status retains complete and partial results, and threaded Play Scenario messages stay with the iteration that produced them.
- **Keep client settings during setup.** Reconfiguration preserves owned startup options and tool profiles. Bulk setup skips manual-only clients, and the Editor surfaces disabled Codex stdio entries and configuration errors.
- **Check current reference results.** Reference analysis checks freshness before delivery and applies bounds to parsing and external source collection.

Remote callers need compiler authority for affected asset and build changes. Observing retained test jobs requires a current read grant.

## [2.1.8 — Identify the job blocking your next Editor command](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.8)

When a refresh has been acknowledged and your next tool call is still blocked, check Editor state or doctor for the native operation and recovery action.

- **See the same readiness decision across tools.** Editor state, capability discovery, and doctor reflect work Unity MCP is still tracking even when Unity's compiling and updating flags appear idle. The server and CLI refresh waits return `recovery_required` promptly when work needs inspection, retaining the refresh and compilation evidence they have.
- **Check the last completed test run at a glance.** Editor state retains the observed result and total, passed, failed, and skipped counts until a new run begins.
- **Inspect the current Play session.** The shorter startup guide links to focused instructions for live checks. If a dedicated tool lacks the action, Execute Code can help when it is exposed, enabled, and consented to. The guide covers how to scope changes and clean up temporary objects without restarting Play Mode. [Authoring and recovery guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.1.8/docs/UNITY_AUTHORING_RECOVERY.md)

## [2.1.7 — Keep watching test and compile jobs after reload](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.7)

If Unity reloads while you're waiting on a test run or checking a compilation, continue with the same job ID after the same project reconnects under the same credentials.

- **Keep your place in the results.** Status reads retain the requested detail flags and result cursor. Test waits follow the new connection within the original deadline. If observation expires, the last known status is marked stale or the job status is reported as unknown. Retry the status read with the original ID.
- **Give a busy Editor time to answer.** HTTP `editor/state` now waits up to 30 seconds by default. Use `UNITY_MCP_STATE_TIMEOUT` to adjust that budget up to 300 seconds. Heartbeats keep their short timeout.
- **Understand empty Console locations.** Structured entries and tail results provide diagnostics about source locations and stack traces Unity supplied, including whether a stack trace was requested. Omit `fields` to see these diagnostics. [Authoring and recovery guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.1.7/docs/UNITY_AUTHORING_RECOVERY.md)

## [2.1.6 — Preview serialized prefab edits before saving](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.6)

When adjusting a prefab's UI anchors, use `prefab_contents` with `action="modify_contents"` and `preview_only=true` to see the serialized diff before saving.

- **Edit a Vector2 in one call.** Fields such as `m_AnchorMin` and `m_AnchoredPosition` accept a pair of finite numeric `x` and `y` values. Invalid vectors are rejected before saving, and existing per-axis edits still work.
- **Review indirect changes.** The preview compares isolated contents with the imported prefab and shows observed layout changes alongside your requested edits. Check its coverage and truncation indicators, then review the final asset diff after saving. Edit-time callbacks can still have external effects.
- **Recover from a reconnect or Play Mode stop.** `operation_target_changed` distinguishes pre-dispatch refusal from an unknown outcome that needs inspection before retrying. If Console access is deferred during a Play transition, the response points you to `wait_for_editor_ready` within the original deadline. [Authoring and recovery guide](https://github.com/ykh09242/unity-mcp/blob/ykh09242-v2.1.6/docs/UNITY_AUTHORING_RECOVERY.md)

## [2.1.5 — See when the Editor is ready after a refresh](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.5)

When `refresh_unity` waits for a reload, its `resulting_state` now reflects the latest observed Editor phase. Once Unity reports readiness, the response updates its follow-up guidance while preserving the original dispatch details.

- **Read the compiler outcome directly.** `compilation_goal_satisfied` means Unity reported a pipeline cycle associated with the request. Check `compilation.outcome` and its diagnostics for success or failure. `source_revision_confirmed` requires evidence linking the source to the loaded assembly.
- **Keep readiness evidence when the final check times out.** A final coordination read uses the remaining budget and preserves known results, snapshot age, and any observation error. The original deadline still applies.

## [2.1.4 — See what is keeping Unity busy](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.4)

An idle Editor can now report itself ready while an external-file scan is still in progress. Scan coverage appears in `advice.warnings`. Compilation, imports, reloads, and test cleanup still hold readiness until Unity can accept work.

- **Keep compiler evidence when readiness takes longer.** `refresh_unity` observes compilation while it waits. A timeout returns the latest compiler snapshot and its age alongside the outstanding readiness blockers.
- **Wait until tests release native resources.** A positive `get_test_job.wait_timeout` also waits for cleanup when the package reports it. Immediate or expired polls keep their results and pages and report `native_cleanup_pending` so you can resume polling the same job.

## [2.1.3 — Follow the same compilation through a domain reload](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.3)

When `get_compile_job` receives the full `Name@hash` over HTTP, it can now wait for that Editor to reconnect through a reload. Polling stays with the same Editor and keeps the original request deadline. Idle stdio sockets also reconnect before capturing the identity of the next operation.

- **Assign component lists directly.** Pass integer Unity instance IDs in serialized arrays and lists, including custom component references. Invalid assignments preserve the prior values.
- **Follow tests before results are ready.** Paged test queries return progress while artifacts are being prepared and honor the requested wait timeout.
- **Refresh Inspectors after prefab edits.** Switching or closing Prefab Mode through MCP synchronizes pending Inspector rebuilds so stale targets clear while unrelated Inspector locks and selections are preserved. Older Editors may use a best-effort shared Inspector refresh.

## [2.1.2 — Keep ignored files out of the reference graph](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.2)

Generated and temporary files can now stay out of your reference graph automatically. Collection respects root and nested `.gitignore` rules by default, and **References > Rules & source configuration > Collection policy** accepts graph-only exclusions. The graph reconciles existing evidence when rules change and preserves authored declarations.

- **Keep long-running project evidence intact.** Graph snapshots share immutable evidence, and a fresh graph store can open safely from cooperating processes. Asset generation and capture check resource limits before admitting large jobs.
- **Use compatible Addressables 1.x through 4.x.** Inspection and editing check the installed package for the APIs and profile schema each action requires. Layout analysis checks the report against the installed package and refuses reports with Content Directory data.
- **Finish recording and test runs predictably.** Recording uses the default scope when callers omit it. Background tests release native ownership after cleanup and keep completed results available during the next run.
- **Protect shared assets and project settings.** Guarded prefab and graphics edits preserve borrowed assets. Package writes require both **EDIT** and **RUN**, and explicit Play-only Animator writes preserve controller defaults.

## [2.1.0 — Coordinate multiple agents in one Editor](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.1.0)

When two agents share a Unity Editor, they can now inspect compatible state concurrently while conflicting changes wait their turn. Unity enforces this across MCP connections and server processes, with independent coordination for each Editor.

- **Keep ownership until Unity finishes.** A build, compile, or test job can keep its native operation slot after the first response and through cancellation and cleanup. Status and cancellation have dedicated calls.
- **Retry only a command proven never to have started.** Request-bound receipts let the server distinguish admission refusals from uncertain outcomes. Retries stay within the original deadline and apply to the rejected command, preserving any earlier batch work.
- **Recover receipts across restarts.** Receipt timestamps remain consistent across coarse system-clock ticks. New private journals use schema 3. Earlier servers refuse to read that format.
- **Check fixed TMP layouts across device profiles.** Supported fixed rectangles now produce device-profile results. Diagnostics flag layouts that depend on metrics or custom providers as partial.

Update the Unity package, restart the MCP connection, and review any protected local skill changes before syncing the pinned skill.

## [2.0.2 — Record scenarios with legacy Input](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.0.2)

Projects using Old Input Handling can now observe legacy input and record it into scenario drafts without changing project settings. Recorded `UnityEngine.Input` events are observation-only. Use the target-based uGUI clicks to replay those flows with **Old**, **New**, or **Both**.

- **Use the Addressables version your project already has.** Compatible stable versions from 1.28 through 2.x are checked against the requested action and stored schema. Undo and Redo refresh stale Addressables 1.x entry lookups.
- **Diagnose what a UI control actually receives.** TMP caret and text graphics retain native raycast behavior. Device-profile diagnostics identify unsupported layout providers while still reporting available results.
- **Follow jobs through completion.** Package waits preserve job identity and failure results. Compilation reports an unknown outcome when Unity supplies no assembly events, and background tests keep reporting progress through native cleanup.
- **Preserve Editor state during automation.** Transient physics stepping preserves clean settings, and desktop focus recovery requires opt-in.

After updating the package, restart the affected MCP server and reconnect the client.

## [2.0.1 — Keep automation moving in an unfocused Editor](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.0.1)

A queued tool call or Play startup can now wake an unfocused Unity Editor on demand. External-change scans also advance in the background between client requests.

- **Wait for the actual job outcome.** HTTP waits follow native progress and finalization within one request budget and preserve failures, cancellations, and unknown outcomes.
- **Edit inactive objects and build TMP controls.** Delete inactive objects by ID, name, or hierarchy path with Undo. Toggle and TMP Input creation supports cached glyphs in compatible fonts and reports when a control would require new glyphs.
- **Start with the tools a fixed-inventory client needs.** Choose **Tools > Advanced connection options > Startup tools**. Select **Full** when needed, restart local HTTP or regenerate the stdio configuration, then reconnect the client. The server also accepts `--tool-profile full`.
- **Resolve Skill Sync conflicts with context.** A matching skill reports that it is already up to date. Protected conflicts show how to compare against the pinned source before reconciling local changes.

## [2.0.0 — Trace asset dependencies and replay gameplay checks](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v2.0.0)

Before changing a shared asset, trace what depends on it. After editing the scene, replay a saved gameplay check and compare the result. Unity MCP 2.0.0 adds both workflows to the Editor.

- **Investigate changes with reference evidence.** Follow Unity asset links, supported C# loading patterns, and authorized configuration sources in a persistent graph. Inspect where a link came from, save a snapshot, and compare it with a later revision.
- **Turn Play Mode checks into repeatable runs.** Review a recording before replay, save scenarios and suites, and run suites in supported standalone Players. Retain assertions, screenshots, and per-run results for comparison.
- **Preview supported edits before applying them.** Review scene, prefab, script, and batch changes, then check which steps applied or need attention. Long-running operations have job status and evidence you can inspect after a disconnect.
- **Find controls and evidence in the Editor.** Search settings, review skill changes, and open operation details on demand. The authoring tools cover workflows such as sprite-sheet animation and guarded Addressables edits.

When switching from upstream, remove `com.coplaydev.unity-mcp` before installing the fork because they share assembly names and asset GUIDs. Reconfigure clients to use the pinned server and enable the original tool IDs you need under **Compatibility**. [Migration guide](https://github.com/ykh09242/unity-mcp/blob/12de216410c854500985e8e6d25f5969844ed334/website/docs/getting-started/migrate.md)

## [1.2.1 — Refresh local HTTP tokens in Codex and Claude Code](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.2.1)

Connect Codex and Claude Code to local HTTP without copying a fresh token after every server restart. When the installed CLI supports it, **Configure** sets up Codex's `http_headers_helper` or Claude Code's `headersHelper` to read the current launch token on connection.

- Claude Code's automatic refresh baseline is **2.1.193**. For older or unrecognized CLIs, use stdio or update the client. Codex is checked for helper support when you configure it.
- **Configure** now handles transport switches and catches stale managed HTTP headers. OpenCode and OpenClaw configurations are read back after saving.
- Custom authentication helpers and Claude OAuth registrations require manual review.

After updating the Unity package, select your transport, click **Configure** once, then restart or reconnect the client. Clients using static headers still need **Configure** after token rotation. The Python server remains at 1.2.0.

## [1.2.0 — Preserve Unity edit order through reconnects](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.2.0)

Transport retries no longer replay a mutation whose outcome is uncertain. An in-flight edit must settle before a later mutation starts, including when the connection is replaced. Large WebSocket results can use bounded binary chunks when both peers support them.

- `manage_prefabs` accepts an instance ID, unique name, or full hierarchy path for `create_from_gameobject`. Use the returned `rootObjectName` or `rootObjectPath` for subsequent prefab edits.
- `read_console` can return selected `fields` in JSON or detailed output. Include `type` and `message`, and set `include_stacktrace=true` to request `stackTrace`.
- Blender Bridge operations require explicit `blender_bridge` consent in the Editor, including from batches and direct panel actions.

**Upgrade the Unity package and Python server together.** The internal stdio bridge uses an authenticated v2 handshake, so regenerate stdio client configurations and clear unintended server overrides. Custom tools should return bounded JSON-compatible data. Unsupported custom model serialization hooks can trigger `response_payload_limit`. Python 3.11 or newer is required.

## [1.1.4 — Select Python 3.11+ for server startup](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.4)

**Python 3.11 or newer is required.** Generated HTTP and stdio commands now explicitly select a supported interpreter. This prevents a dependency import failure that could surface during tool registration even after `--help` succeeded on Python 3.10.

- Server 1.1.4 updates FastMCP and MCP and uses the standard-library `tomllib` for version discovery.
- The Windows long-path fix continues to install the server from a commit-pinned GitHub archive, with uv caching it for later launches.

If needed, install Python with `uv python install 3.11`. Clear any unintended **Server Source Override**, regenerate your stdio client configuration, and update hand-written launch commands that select Python 3.10.

## [1.1.3 — Start the server on Windows without a long Git checkout](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.3)

Windows users who hit `Filename too long` during Python server setup can use the default install without changing Git or system settings. Unity now gives uv a GitHub source archive pinned to the same commit, so uv can build `Server` without checking out the repository. The Python server stays at 1.1.1.

After updating, clear any unintended **Server Source Override** and regenerate stdio client configurations that still point to the old Git source. The archive gets its own uv cache entry, so the first install may need network access. An intentional Git override still uses a checkout.

## [1.1.2 — Install client skills from a tagged Unity package](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.2)

**Install Skills** now uses the same branch selection as package update checks. Tagged and commit-pinned installs use the fork's `beta` branch, fixing the HTTP 404 caused by looking for the missing `main` branch. Explicit `main` installs still request `main`.

The **Script Validation** dropdown returns to a compact height, and the MCP windows follow Unity's light and dark theme colors.

Update the Unity package's Git revision to get the fixes. The pinned Python server remains at 1.1.1.

## [1.1.1 — Keep connections running after closing the MCP window](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.1)

Set up Unity MCP when you need it, then close the redesigned window while the server and Editor bridge continue running. Open **Local Setup Window** from **Window > Unity MCP (ykh09242)**. **Start HTTP on Editor Startup** remains an optional background preference.

- Search the tool and resource catalogs without losing saved foldout state. Preferences keep unsaved edits while you search or save another setting.
- Unity Package Manager and frame waits that use Editor Coroutines can continue when their window closes. Unity Package Manager resolves the `com.unity.editorcoroutines` 1.0.1 dependency during import.
- Supported mutation tools reject malformed inputs before waiting for the Editor to become ready. Code execution also reuses recent successful compilations while running each invocation against the current Editor state.

Regenerate existing client configurations after installing to pick up the 1.1.1 server source. Unity 2021.3 remains the declared minimum, and the server requires Python 3.10 or newer.

## [1.1.0 — Build and diagnose Canvas UI](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.0)

Build a Canvas-based interface and check how it lays out across screen sizes with `manage_ugui` in the optional `ui` tool group.

- Inspect Canvas hierarchies and create canvases, panels, images, buttons, and TextMeshPro text. Edit layout or CanvasScaler settings by GameObject name, hierarchy path, or instance ID. Changes participate in Unity Undo.
- Run `diagnose` at up to eight resolutions. It uses a disposable preview to report possible clipping, overflow, overlap, and blocked interactions with the affected objects.
- Play-mode screenshots incorporate CoplayDev's asynchronous composited capture. When a camera fallback is needed, the result includes `captureSource="camera_fallback"` and explains why. Numeric parsing and built-in serialized property resolution also incorporate upstream fixes. [Upstream screenshot change](https://github.com/CoplayDev/unity-mcp/pull/1428)

After updating, regenerate the MCP client configuration so it uses the 1.1.0 server pin. Local HTTP clients need the current launch token after a server restart.

## [1.0.0 — Preview C# edits before applying them in Unity](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.0.0)

Review a proposed C# edit before it touches your Unity project. The first independently versioned release of the ykh09242 fork of [CoplayDev's Unity MCP](https://github.com/CoplayDev/unity-mcp) introduces its own Unity package and a Python server pinned to a source commit. The fork retains the original MIT license, authorship, assembly names, and asset GUIDs.

- Pass `options.preview=true` to `script_apply_edits` to get proposed contents and hashes without writing the file or scheduling a refresh.
- Play or stop an existing scene `AudioSource` in Play mode with `manage_audio`.
- Local HTTP and WebSocket control use a per-launch `X-Unity-MCP-Token`. The loopback CLI discovers the token automatically. High-impact tools such as `execute_code` and `manage_script` require explicit Editor consent.
- On MCP 2 sessionless connections, select the Editor for each call with `unity_instance` on tools or `_meta.unity_instance` on resources. Failed Unity commands now return a nonzero CLI exit status.

If you are moving from the upstream package, remove `com.coplaydev.unity-mcp` before installing `com.ykh09242.unity-mcp`, then regenerate your MCP client configuration. The declared Unity minimum is 2021.3 and the pinned server requires Python 3.10 or newer.
