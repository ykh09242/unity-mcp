---
title: scene_inspect
sidebar_label: scene_inspect
description: "Inspect loaded scenes, hierarchy and build settings, or frame the Scene view."
---

# `scene_inspect`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect loaded scenes, hierarchy and build settings, or frame the Scene view.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['get_hierarchy', 'get_active', 'get_build_settings', 'get_loaded_scenes', 'scene_view_frame']` | yes | Feature action to perform. |
| `scene_view_target` | `str \| int \| None` | — | GameObject reference for scene_view_frame (name, path, or instance ID). |
| `parent` | `str \| int \| None` | — | Optional parent GameObject reference (name/path/instanceID) to list direct children. |
| `page_size` | `int \| str \| None` | — | Page size for get_hierarchy paging. |
| `cursor` | `int \| str \| None` | — | Opaque cursor for paging (offset). |
| `max_nodes` | `int \| str \| None` | — | Hard cap on returned nodes per request (safety). |
| `max_depth` | `int \| str \| None` | — | Accepted for forward-compatibility; current paging returns a single level. |
| `max_children_per_node` | `int \| str \| None` | — | Child paging hint (safety). |
| `include_transform` | `bool \| str \| None` | — | If true, include local transform in node summaries. |
| `refresh_if_dirty` | `bool` | — | Set false to skip optional asset refreshes before this command. True preserves the operation's existing refresh policy and permissions. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Read one level of the active scene hierarchy with local transforms. For a subsequent level, pass a returned object as `parent`.

```json title="MCP arguments"
{
  "action": "get_hierarchy",
  "page_size": 20,
  "include_transform": true
}
```
<!-- examples:end -->

