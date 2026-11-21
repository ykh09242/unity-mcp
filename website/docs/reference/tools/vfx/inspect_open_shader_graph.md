---
title: inspect_open_shader_graph
sidebar_label: inspect_open_shader_graph
description: "Read nodeCount and edgeCount, including unsaved edits, from one already-open Shader Graph window matching a project-owned Assets/*.shadergraph path."
---

# `inspect_open_shader_graph`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `vfx` &nbsp;·&nbsp; **Module:** `services.tools.inspect_open_shader_graph`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read nodeCount and edgeCount, including unsaved edits, from one already-open Shader Graph window matching a project-owned Assets/*.shadergraph path. Check status for open_model, no_matching_window, open_model_unavailable or window_lookup_unavailable. Requires manage_shader. Leaves the graph, its window and the asset file untouched.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `str` | yes | Full project-owned Assets/*.shadergraph path. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Check the current node and edge counts for a Shader Graph that is already open. Use its full Assets path and examine the returned status before comparing counts to the saved graph.

```json title="MCP arguments"
{
  "path": "Assets/Shaders/Water.shadergraph"
}
```
<!-- examples:end -->

