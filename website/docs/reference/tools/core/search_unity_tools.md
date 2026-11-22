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

Search Unity tools, actions and readable resources without changing selection or invoking Unity. Keep the original query. For required evidence, select observation_source and/or required_values. To perform an operation, choose intent and supply catalog_query when useful. Omit evidence selectors unless the route must return evidence. Inspect route_groups for invocations and evidence limits, or no_match for scoped alternatives.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | `str \| None` | — | The original user request in its own language. Keep names, paths and text verbatim. Optional when selecting evidence fields or an evidence source. |
| `limit` | `int` | — | Maximum results per page. |
| `catalog_query` | `str \| None` | — | Optional English action and target terms or an exact tool/action identifier for lexical search. Keep the original request in query. Use with intent for action searches without evidence constraints. |
| `intent` | `Literal['observe', 'interact', 'edit'] \| None` | — | Choose the requested purpose by meaning in any language: observe for reading state, interact for operating the Editor or running game, edit for changing project content. |
| `observation_source` | `Literal['editor_live', 'asset_file', 'imported_asset', 'compile_job', 'scenario_job'] \| None` | — | Where required evidence must come from: the current Editor (editor_live), a saved file (asset_file), import results (imported_asset), a compilation job (compile_job), or a Play Scenario job (scenario_job). Omit for an action-only search. |
| `required_values` | `list[Literal['unityCameras', 'cinemachineCameras', 'cinemachineInstalled', 'render_size.width', 'render_size.height', 'current_size', 'view_instance_id', 'presets', 'rendered_image', 'pointer_hit_order', 'console.entries', 'menu.declared_paths', 'active_scene.name', 'active_scene.path', 'compilation.is_compiling', 'compile_job.outcome', 'scenario_job.outcome', 'nodeCount', 'edgeCount', 'nodes', 'edges', 'properties', 'keywords', 'unsaved_graph_structure', 'property_values', 'shader_source', 'contents', 'encodedContents', 'nextOffset', 'shader_graph_structure', 'unsaved_source', 'compilation', 'passes', 'subshader_count', 'navigation.module_available', 'navigation.package_available', 'project_auditor.api_supported', 'project_auditor.rules_available']] \| None` | — | Choose every declared result field the route must supply as evidence. Use with observation_source when provenance matters. Omit for an action-only search. |
| `route_surfaces` | `Literal['both', 'tools', 'resources'] \| None` | — | Group tools, resources or both (default). |
| `host_tool_names` | `list[str] \| None` | — | Complete callable tool names exposed by this host for this connection. Omit if unknown. |
| `host_tool_evidence` | `HostToolEvidence \| None` | — | Bounded exact-name checks from this host on the same connection and target. Use instead of host_tool_names. Incomplete misses stay unknown. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Find up to five tool candidates for diagnosing why an object is missing from a loaded scene. Compare the returned action and parameter requirements before calling a candidate.

```json title="MCP arguments"
{
  "query": "find GameObjects across loaded scenes",
  "limit": 5
}
```
<!-- examples:end -->

