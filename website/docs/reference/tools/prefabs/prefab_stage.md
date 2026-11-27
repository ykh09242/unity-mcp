---
title: prefab_stage
sidebar_label: prefab_stage
description: "Open, save or close the Prefab Stage."
---

# `prefab_stage`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Open, save or close the Prefab Stage.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['open_prefab_stage', 'save_prefab_stage', 'close_prefab_stage']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). For save/close, when supplied it must match the current Prefab Stage. |
| `refresh_if_dirty` | `bool` | — | Set false to skip optional asset refreshes before this command. True preserves the operation's existing refresh policy and permissions. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Open a project prefab in Prefab Mode for inspection. Replace the path with the prefab to inspect and account for any existing unsaved stage edits before changing stages.

```json title="MCP arguments"
{
  "action": "open_prefab_stage",
  "prefab_path": "Assets/Prefabs/Player.prefab"
}
```
<!-- examples:end -->

