---
title: manage_tools
sidebar_label: manage_tools
description: "Find Unity tools, actions and readable resources for a task; inspect schemas or manage visible groups."
---

# `manage_tools`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_tools`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Find Unity tools, actions and readable resources for a task; inspect schemas or manage visible groups. Actions: list_groups (show all groups and their status), activate/deactivate (select exactly one group or tool), sync (refresh availability from Unity Editor's toggle states), reset (restore configured profile), describe (full schema for one tool, optionally bind operation), catalog (choose a domain by purpose, then browse tools, actions or readable resources), search (match declared observation_source and required_values directly from a task in any language, or search by text). Selection changes server tools/list; it does not confirm the client's callable inventory. Use sync after toggling tools in the Unity Editor GUI.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_groups', 'activate', 'deactivate', 'sync', 'reset', 'describe', 'search', 'catalog']` | yes | Action to perform. |
| `group` | `str \| None` | — | Group name for activate/deactivate/catalog. Catalog also supports resources for fixed URIs and resource_templates for parameterized Unity observations. Valid groups: animation, asset_gen, assets, camera, core, docs, graphics, legacy, physics, prefabs, probuilder, profiling, scene, scripting_ext, testing, ui, vfx |
| `tool` | `str \| None` | — | Single tool name for activate/deactivate/describe/catalog. Discover names with catalog. |
| `query` | `str \| None` | — | Optional original task text when searching by observation, or a lexical query (1..512 characters). |
| `limit` | `int` | — | Maximum search or catalog page entries (1..20). |
| `include_schema` | `bool` | — | On single-tool activation, attach its schema and readiness lookup. |
| `catalog_query` | `str \| None` | — | Optional alternate terms for literal search, such as a known API identifier. The complete catalog is available for requests in any language. |
| `offset` | `int` | — | Catalog page offset for a selected group or tool. |
| `operation` | `str \| None` | — | For describe, select one exact action from catalog. The returned full schema binds that action. |
| `intent` | `Literal['observe', 'interact', 'edit'] \| None` | — | Optional purpose interpreted from any language. For search or catalog with a tool/group: observe state, interact with the running game/editor, or edit project content. Classified routes only; inspect action effects separately. |
| `observation_source` | `Literal['editor_live', 'asset_file', 'imported_asset', 'compile_job'] \| None` | — | For search, interpret the request in its own language. Select where the evidence must originate: current Editor state (editor_live), saved file contents (asset_file), or Unity import results (imported_asset). |
| `required_values` | `list[Literal['render_size.width', 'render_size.height', 'current_size', 'view_instance_id', 'presets', 'rendered_image', 'pointer_hit_order', 'active_scene.name', 'active_scene.path', 'compilation.is_compiling', 'compile_job.outcome', 'nodeCount', 'edgeCount', 'nodes', 'edges', 'properties', 'keywords', 'unsaved_graph_structure', 'property_values', 'shader_source', 'contents', 'encodedContents', 'nextOffset', 'shader_graph_structure', 'unsaved_source', 'compilation', 'passes', 'subshader_count']] \| None` | — | For search, select every result field the request needs. Game View pixels use render_size.width and render_size.height; saved graph totals use nodeCount and edgeCount. A route must declare all selected values. No query or translation is needed. |
| `route_surfaces` | `Literal['both', 'tools', 'resources'] \| None` | — | For search, choose both (default), tools, or resources for route_groups. Use tools for a tools-only client. Each group offers a representative and available alternatives. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

