---
title: inspect_shader_graph
sidebar_label: inspect_shader_graph
description: "Read a saved Shader Graph file through the selected Unity Editor."
---

# `inspect_shader_graph`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `vfx` &nbsp;·&nbsp; **Module:** `services.tools.editor_observations`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read a saved Shader Graph file through the selected Unity Editor. Returns total nodeCount and edgeCount plus paged nodes, edges, properties and keywords. Unsaved graph-window edits and property values are unavailable. Supports project-owned MultiJSON GraphData v0-3 files up to 16 MiB. Requires manage_shader.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `str` | yes | Full project-owned Assets/*.shadergraph path. |
| `page_size` | `int` | — | Entries per collection on each page. |
| `page_number` | `int` | — | One-based page number. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Read the first 20 saved nodes, edges, properties, and keywords for a project Shader Graph. Replace the example path with the saved asset to inspect.

```json title="MCP arguments"
{
  "path": "Assets/Shaders/Water.shadergraph",
  "page_size": 20,
  "page_number": 1
}
```
<!-- examples:end -->

