---
title: manage_shader
sidebar_label: manage_shader
description: "Manages shader scripts in Unity (create, read, update, delete)."
---

# `manage_shader`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `vfx` &nbsp;·&nbsp; **Module:** `services.tools.manage_shader`

## Description

Manages shader scripts in Unity (create, read, update, delete). Read-only: read, inspect_graph. inspect_graph takes a full Assets/*.shadergraph path and returns bounded, paged serialized nodes, edges, properties and keywords (modern MultiJSON GraphData v0-3; 4 MiB files; at most 100 results per collection per page). This is structural inspection, not package validation or graph editing. Modifying actions: create, update, delete.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create', 'read', 'update', 'delete', 'inspect_graph']` | yes | Shader CRUD or read-only serialized Shader Graph inspection. |
| `name` | `str \| None` | — | Shader name without extension; omitted for inspect_graph. |
| `path` | `str` | — | Shader directory for CRUD; full .shadergraph asset path for inspect_graph. |
| `contents` | `str \| None` | — | Shader code for 'create'/'update' |
| `page_size` | `int \| str \| None` | — | inspect_graph page size, 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | inspect_graph page number, 1-based. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Inspect a project-owned Shader Graph without modifying it:

```json
{"action":"inspect_graph","path":"Assets/Shaders/Surface.shadergraph","page_size":50,"page_number":1}
```

Each page contains separate node, edge, property and keyword lists. Increase
`page_number` while `hasMore` is true. Only modern MultiJSON GraphData versions 0–3
with object references are supported; legacy embedded serialization returns an error.
This reads structure and does not compile or validate the graph against the installed
Shader Graph package. Ordinary shader CRUD still uses `name` and a directory `path`.
<!-- examples:end -->

