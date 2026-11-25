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

Find Unity tools, actions and readable resources for a task; inspect schemas or manage visible groups. Actions: list_groups (show all groups and their status), activate/deactivate (select exactly one group or tool), sync (refresh availability from Unity Editor's toggle states), reset (restore configured profile), describe (full schema for one tool, optionally bind operation), catalog (choose a domain by purpose, then browse tools, actions or readable resources), search (discover routes for a task in any language), inspect_inventory (compare server advertisement with this host's exact-name evidence). Use selection=agent with the original query to discover public families. Pass a returned family and follow next_offset to compare every binding before choosing. Ranked search also accepts intent and catalog_query, or observation_source and required_values for evidence. Selection changes server tools/list; it does not confirm the client's callable inventory. Use sync after toggling tools in the Unity Editor GUI.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_groups', 'activate', 'deactivate', 'sync', 'reset', 'describe', 'search', 'catalog', 'inspect_inventory']` | yes | Action to perform. |
| `group` | `str \| None` | — | Group name for activate/deactivate/catalog. Catalog also supports resources for fixed URIs and resource_templates for parameterized Unity observations. Valid groups: animation, asset_gen, assets, camera, core, docs, graphics, legacy, physics, prefabs, probuilder, profiling, scene, scripting_ext, testing, ui, vfx |
| `tool` | `str \| None` | — | Single exact tool name for activate/deactivate/describe/catalog/inspect_inventory. |
| `query` | `str \| None` | — | The original user request in its own language. Keep names, paths and text verbatim. Optional when selecting evidence fields or an evidence source. |
| `limit` | `int` | — | Maximum search or catalog page entries (1..20). |
| `include_schema` | `bool` | — | On single-tool activation attach schema and readiness. On inspect_inventory include the advertised inputSchema and its SHA-256. |
| `catalog_query` | `str \| None` | — | Optional English action and target terms or an exact tool/action identifier for lexical search. Keep the original request in query. Use with intent for action searches without evidence constraints. |
| `offset` | `int` | — | Catalog page offset for a selected group or tool. |
| `operation` | `str \| None` | — | For describe, select one exact action from catalog. The returned full schema binds that action. |
| `intent` | `Literal['observe', 'interact', 'edit'] \| None` | — | Choose the requested purpose by meaning in any language: observe for reading state, interact for operating the Editor or running game, edit for changing project content. Use for search or catalog with a tool/group. Applies to classified routes. |
| `observation_source` | `Literal['editor_live', 'asset_file', 'imported_asset', 'compile_job', 'scenario_job'] \| None` | — | Where required evidence must come from: the current Editor (editor_live), a saved file (asset_file), import results (imported_asset), a compilation job (compile_job), or a Play Scenario job (scenario_job). Omit for an action-only search. |
| `required_values` | `list[Literal['unityCameras', 'cinemachineCameras', 'cinemachineInstalled', 'render_size.width', 'render_size.height', 'current_size', 'view_instance_id', 'presets', 'rendered_image', 'pointer_hit_order', 'console.entries', 'menu.declared_paths', 'active_scene.name', 'active_scene.path', 'compilation.is_compiling', 'compile_job.outcome', 'scenario_job.outcome', 'nodeCount', 'edgeCount', 'nodes', 'edges', 'properties', 'keywords', 'unsaved_graph_structure', 'property_values', 'shader_source', 'contents', 'encodedContents', 'nextOffset', 'shader_graph_structure', 'unsaved_source', 'compilation', 'passes', 'subshader_count', 'navigation.module_available', 'navigation.package_available', 'project_auditor.api_supported', 'project_auditor.rules_available']] \| None` | — | Choose every declared result field the route must supply as evidence. Use with observation_source when provenance matters. Omit for an action-only search. |
| `route_surfaces` | `Literal['both', 'tools', 'resources'] \| None` | — | For search, choose both (default), tools, or resources for route_groups. Use tools for a tools-only client. Each group offers a representative and available alternatives. |
| `host_tool_names` | `list[str] \| None` | — | For search or inspect_inventory, supply this host's complete callable tool names. Omit if unknown. |
| `host_tool_evidence` | `HostToolEvidence \| None` | — | For search or inspect_inventory, check exact candidate names on this host's same connection and target. Use instead of host_tool_names. Incomplete misses stay unknown. |
| `selection` | `Literal['ranked', 'agent']` | — | Use agent for a public family directory and complete operation pages; ranked returns approximate matches. |
| `family` | `str \| None` | — | Exact family ID from the agent selection directory. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
List available tool groups and their activation state. Check for the `testing` group before calling a Play Scenario or verification tool.

```json title="MCP arguments"
{
  "action": "list_groups"
}
```
<!-- examples:end -->

