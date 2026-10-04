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
- Editable mesh topology with installed ProBuilder: [probuilder-guide.md](references/probuilder-guide.md).

Load only the reference needed for the request. The live tool schema wins over a sample; examples use logical tool names, not client-specific prefixes or executable Python SDK calls.

## Target And Act

1. Discover the available tools, resources and resource templates using the client's supported inventory mechanism. Resolve the Unity instance when selection is ambiguous; verify the project before mutation.
2. Inspect the specific object, asset or state needed for the task. Use returned IDs and exact asset paths rather than ambiguous names. Check readiness for operations affected by compilation, refresh or a running test job.
3. Apply the requested change with the narrowest suitable tool. A missing optional package is a capability limit, not permission to install it. Enabling a tool group or executing code does not bypass authorization.
4. Verify the resulting state. For scripts, wait for compilation and inspect diagnostics. For visual work, inspect an image when capture is available. For jobs, poll the returned job ID and distinguish completion from success.

Do not create a fresh scene, switch Play mode, save unrelated scenes or reset project settings simply to satisfy a template.

## Boundaries That Matter

- Explicit consent is enforced for `execute_code`, `manage_script`, `execute_menu_item`, `manage_packages`, `manage_build` and `batch_execute`. Script wrappers route through `manage_script`; asset operations also gate script/compiler/plugin inputs. If blocked, describe the requested action and ask the user to enable the relevant consent in Unity. Do not alter preferences or use another handler to evade it.
- Asset writes/imports stay within permitted `Assets/` paths. Traversal, symlinks and junctions are rejected. Registered package assets may be readable where the tool explicitly allows it, not general write targets. Keep GUIDs and unrelated assets intact.
- Batches and many property updates are best-effort, not transactions. Read per-command results; earlier successful changes may remain after a later failure.
- After a timeout or reload, inspect the target/job before repeating a mutation. Retry a read after transient readiness recovery; stop persistent authentication, consent, path or schema failures and report the actionable cause.
