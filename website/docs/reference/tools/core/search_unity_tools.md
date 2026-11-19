---
title: search_unity_tools
sidebar_label: search_unity_tools
description: "Search Unity tools, actions and readable resources without changing selection or invoking Unity."
---

# `search_unity_tools`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_tools`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Search Unity tools, actions and readable resources without changing selection or invoking Unity. Choose observation_source and required_values by meaning in any language, or search catalog text. Inspect route_groups for invocations and evidence limits.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | `str \| None` | — | Optional original task or lexical query. |
| `limit` | `int` | — | Maximum results per page. |
| `catalog_query` | `str \| None` | — | Optional alternate catalog terms for a lexical query. |
| `intent` | `Literal['observe', 'interact', 'edit'] \| None` | — | Requested operation purpose. |
| `observation_source` | `Literal['editor_live', 'asset_file', 'imported_asset'] \| None` | — | Origin of the required evidence. |
| `required_values` | `list[Literal['render_size.width', 'render_size.height', 'current_size', 'view_instance_id', 'presets', 'rendered_image', 'pointer_hit_order', 'nodeCount', 'edgeCount', 'nodes', 'edges', 'properties', 'keywords', 'unsaved_graph_structure', 'property_values', 'shader_source', 'contents', 'encodedContents', 'nextOffset', 'shader_graph_structure', 'unsaved_source', 'compilation', 'passes', 'subshader_count']] \| None` | — | Select every result field the observation must supply. |
| `route_surfaces` | `Literal['both', 'tools', 'resources'] \| None` | — | Group tools, resources or both (default). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

