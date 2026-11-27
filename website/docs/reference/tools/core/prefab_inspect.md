---
title: prefab_inspect
sidebar_label: prefab_inspect
description: "Inspect prefab metadata or a bounded hierarchy page."
---

# `prefab_inspect`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect prefab metadata or a bounded hierarchy page. Inspection may load assets or invoke user-defined callbacks.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['get_info', 'get_hierarchy']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). For save/close, when supplied it must match the current Prefab Stage. |
| `page_size` | `int \| None` | — | Page size 1-500. get_hierarchy defaults to 50; list_overrides defaults to 100. |
| `cursor` | `str \| None` | — | get_hierarchy opaque cursor; keep identical page_size/max_depth. |
| `max_depth` | `int` | — | get_hierarchy inclusive depth scope, root=0; default 64, maximum 128. |
| `refresh_if_dirty` | `bool` | — | Set false to skip optional asset refreshes before this command. True preserves the operation's existing refresh policy and permissions. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Read the first page of a prefab hierarchy before opening the prefab for editing. Replace the example path with the target prefab. Use the reported cursor if more nodes are available.

```json title="MCP arguments"
{
  "action": "get_hierarchy",
  "prefab_path": "Assets/Prefabs/Player.prefab",
  "page_size": 25
}
```
<!-- examples:end -->

