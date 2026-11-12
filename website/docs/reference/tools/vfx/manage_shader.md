---
title: manage_shader
sidebar_label: manage_shader
description: "Manages shader source, graph structure and compiler diagnostics. read returns a bounded source page (64 KiB by default); follow data.nextOffset with read_offset while hasMore is true."
---

# `manage_shader`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `vfx` &nbsp;·&nbsp; **Module:** `services.tools.manage_shader`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Manages shader source, graph structure and compiler diagnostics. read returns a bounded source page (64 KiB by default); follow data.nextOffset with read_offset while hasMore is true. Offsets and limits count source bytes after the BOM. inspect_graph takes a full Assets/*.shadergraph path and returns bounded, paged serialized nodes, edges, properties and keywords (modern MultiJSON GraphData v0-3; 16 MiB files; at most 100 results per collection per page). diagnostics reads cached messages for a full .shader/.shadergraph path. compile_variant compiles one explicit variant in idle Edit Mode and returns outcome, bytecode size and up to 100 bounded messages. Empty bytecode reports no_program. Platform/target support depends on the installed Editor.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create', 'read', 'update', 'delete', 'inspect_graph', 'diagnostics', 'compile_variant']` | yes | Shader CRUD, graph inspection, cached diagnostics or one explicit variant compilation. |
| `name` | `str \| None` | — | Shader name without extension; required only for CRUD. |
| `path` | `str` | — | Shader directory for CRUD; full asset path for inspect_graph/diagnostics/compile_variant. |
| `contents` | `str \| None` | — | Shader code for 'create'/'update' |
| `page_size` | `int \| str \| None` | — | inspect_graph page size, 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | inspect_graph page number, 1-based. |
| `read_offset` | `int \| str \| None` | — | read source byte offset after the BOM (default 0). |
| `read_limit` | `int \| str \| None` | — | read page byte limit, 4-1048576 (default 65536). |
| `read_encoding` | `Literal['text', 'base64'] \| None` | — | read output encoding; Base64 represents UTF-8 text. |
| `variant` | `ShaderVariantOptions \| None` | — | Required for compile_variant. Explicit stage, compiler platform, build target, pass and keywords. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

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

