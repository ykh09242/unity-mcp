---
id: first-prompt
slug: /getting-started/first-prompt
title: Your First Prompt
sidebar_label: First Prompt
description: Discover the right Editor, inspect a saved scene, make one small change, and verify it.
---

# Your First Prompt

Finish [Install And Connect](./install.md), then open a disposable or saved scene. Start with inspection rather than code execution or a large generated scene.

## 1. Confirm the project

> Read `mcpforunity://instances` and show the Editor IDs. Use the ID I choose on every subsequent tool call and resource read. Read its project info and active scene; do not modify anything yet.

Confirm that the project and scene are the ones you intend. Tool calls use `unity_instance`; resource reads use `_meta.unity_instance`. These selectors apply to the current request. See [routing](../guides/multi-instance.md) if several Editors are open.

## 2. Make one small change

> In that Editor, create one GameObject named `MCP_FirstCube` using the Cube primitive at position (0, 0, 0). Do not create scripts, change packages, enter Play mode, or save the scene. Return its instance ID and hierarchy path.

The assistant should call `manage_gameobject` with the chosen target. Confirm the cube appears in Unity's Hierarchy and Scene view. A tool response is useful evidence, but inspect the Editor before repeating a timed-out write.

## 3. Verify the result

> Read back `MCP_FirstCube` by the returned instance ID and confirm its name, transform and components. Report any mismatch; do not create another cube.

Keep the object, delete it through an explicit request, or revert the disposable scene. Do not assume every operation provides a complete rollback; review changes before saving.

## Continue deliberately

- Add a material using the project's actual rendering pipeline instead of assuming the Standard shader.
- Enable an optional group only when needed: [Tool Groups](../guides/tool-groups.md).
- Before script, package, build, menu, code or batch work, review [Security And Consent](../guides/security.md).
- For scripting, request read-only edit preparation first when reviewing a candidate; native-file application needs separate local provenance and exact-byte checks.

## Diagnose failures

| Result | What to do |
|---|---|
| No Editors | Check Unity compile errors and bridge/server status. |
| Multiple Editors | Supply a returned `Name@hash`; do not repeatedly call session selection from a sessionless client. |
| Missing/disabled tool | Inspect `manage_tools(action="list_groups")` and the Editor tool controls. |
| Consent denied | Review and grant only the capability you intend in the Editor. |
| Timeout after mutation dispatch | Inspect actual state before retrying. |

See [Troubleshooting](../guides/troubleshooting.md) and the [generated tool reference](../reference/tools/index.md) for exact parameters. No fixed execution-time guarantee is implied.
