---
name: unity-mcp-orchestrator
description: Inspect and edit a connected Unity Editor through Unity MCP tools and resources, including scene objects, assets, scripts and test jobs. Use for live Editor work, not ordinary repository-only code edits.
---

# Unity MCP Operator

Operate the user's selected Unity project through the capabilities actually exposed by the client. This skill is distributed with **Unity MCP (ykh09242)**; tool names, C# namespaces and the `mcpforunity://` resource scheme remain compatible.

## Choose The Relevant Reference

- Connection, instance routing, missing tools, authentication or installation: [connection.md](references/connection.md).
- Picking a tool and adapting payloads: [tools-reference.md](references/tools-reference.md).
- Readiness, project configuration, object/component inspection or pagination: [resources-reference.md](references/resources-reference.md).
- C# edits, read-only proposals and native-file handoff: [scripts.md](references/scripts.md).
- Object/material/UI edits, visual checks or asynchronous tests: [workflows.md](references/workflows.md).
- Tests that must preserve an already-running game: [current Play state](references/workflows.md#tests-in-the-current-play-state).
- Editable mesh topology with installed ProBuilder: [probuilder-guide.md](references/probuilder-guide.md).

Load only the reference needed for the request. The live tool schema wins over a sample; examples use logical tool names, not client-specific prefixes or executable Python SDK calls.

## Keep Context Bounded

- Use already-advertised capabilities; discover a missing tool/group when needed instead of repeatedly loading the full catalog or all schemas. Read `mcpforunity://custom-tools` for project-specific functionality, not as mandatory setup for every operation.
- Start with small pages and metadata-only component queries. Fetch values, full stacks or individual test failures only when needed; stop paging once the target is resolved.
- Keep job IDs/cursors and concise observations for follow-up. Do not repeatedly fetch unchanged full results, execution history or base64 previews. Request images when visual evidence is needed and inspect the image itself.

## Target And Act

1. Use the client's supported inventory to resolve missing capabilities or ambiguous Unity instances; verify the project before mutation.
2. Inspect the specific object, asset or state needed for the task. Use returned IDs and exact asset paths rather than ambiguous names. Check readiness for operations affected by compilation, refresh or a running test job.
3. Apply the requested change with the narrowest suitable tool. A missing optional package is a capability limit, not permission to install it. Enabling a tool group or executing code does not bypass authorization.
4. Verify the resulting state. For scripts, wait for compilation and inspect diagnostics. For visual work, inspect an image when capture is available. For jobs, poll the returned job ID and distinguish completion from success.

Independent observations may run concurrently after their inputs and Editor target are fixed. Supported connections coordinate same-instance work automatically; keep create/edit/verify dependencies ordered. See [concurrent calls](references/workflows.md#concurrent-calls-and-coordination) for guarantees and limits.

Do not create a fresh scene, switch Play mode, save unrelated scenes or reset project settings simply to satisfy a template.

For an authorized test in the current Play state, proactively use `execute_code` for runtime inspection, setup or game-specific changes that a dedicated tool cannot express, when it is exposed, enabled in Unity and execution consent is granted. The user need not name the tool again. Preserve the session and verify the outcome; follow the [current-state workflow](references/workflows.md#tests-in-the-current-play-state) before choosing a scene-loading scenario or creating test scripts.

## Boundaries That Matter

- Explicit consent is enforced for `execute_code`, `manage_script`, `execute_menu_item`, `manage_packages`, `manage_build` and `batch_execute`. Script wrappers route through `manage_script`; asset operations also gate script/compiler/plugin inputs. If blocked, describe the requested action and ask the user to enable the relevant consent in Unity. Do not alter preferences or use another handler to evade it.
- Asset writes/imports stay within permitted `Assets/` paths. Traversal, symlinks and junctions are rejected. Registered package assets may be readable where the tool explicitly allows it, not general write targets. Keep GUIDs and unrelated assets intact.
- Batches and many property updates are best-effort, not transactions. Read per-command results; earlier successful changes may remain after a later failure.
- After a timeout or reload, inspect the target/job before repeating a mutation. Retry a read after transient readiness recovery; stop persistent authentication, consent, path or schema failures and report the actionable cause.
