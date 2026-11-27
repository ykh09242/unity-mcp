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

Search Unity tools, actions and readable resources without changing selection or invoking Unity. For exhaustive browsing, pass "selection": "agent" and "query": the original text. Supply all search fields together at the top level or in one query object. To open a family, set "family" to its returned ID in open_family.arguments. Follow next_offset. For lexical or required evidence searches, use selection=ranked. catalog_query, intent, observation_source and required_values are ranked only. Inspect route_groups for invocations and evidence limits, or no_match for scoped alternatives.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | `str \| None` | — | Original user request. Keep its language, names, paths and text verbatim. Required for the agent directory. Optional for an exact agent family or ranked evidence search. |
| `limit` | `int` | — | Requested entries per page. Search and catalog return at most 20. Follow next_offset where available. |
| `catalog_query` | `str \| None` | — | Ranked search only. Optional English action and target terms or exact tool/action identifier for lexical matching. Keep the original request in query. |
| `intent` | `Literal['observe', 'interact', 'edit'] \| None` | — | Ranked search only. Choose the requested purpose by meaning in any language: observe for reading state, interact for operating the Editor or running game, edit for changing project content. |
| `observation_source` | `Literal['editor_live', 'asset_file', 'imported_asset', 'compile_job', 'scenario_job'] \| None` | — | Ranked search only. Require evidence from the current Editor (editor_live), a saved file (asset_file), import results (imported_asset), a compilation job (compile_job), or a Play Scenario job (scenario_job). |
| `required_values` | `list[Literal['unityCameras', 'cinemachineCameras', 'cinemachineInstalled', 'render_size.width', 'render_size.height', 'current_size', 'view_instance_id', 'presets', 'rendered_image', 'pointer_hit_order', 'console.entries', 'menu.declared_paths', 'active_scene.name', 'active_scene.path', 'compilation.is_compiling', 'compile_job.outcome', 'scenario_job.outcome', 'nodeCount', 'edgeCount', 'nodes', 'edges', 'properties', 'keywords', 'unsaved_graph_structure', 'property_values', 'shader_source', 'contents', 'encodedContents', 'sourceFileSha256', 'nextOffset', 'shader_graph_structure', 'unsaved_source', 'compilation', 'passes', 'subshader_count', 'navigation.module_available', 'navigation.package_available', 'project_auditor.api_supported', 'project_auditor.rules_available']] \| None` | — | Ranked search only. Require every listed result field as evidence. Combine with observation_source to require its provenance. |
| `route_surfaces` | `Literal['both', 'tools', 'resources'] \| None` | — | For either mode, show tools, resources or both (default). |
| `host_tool_names` | `list[str] \| None` | — | Complete callable tool names exposed by this host for this connection. Omit if unknown. |
| `host_tool_evidence` | `HostToolEvidence \| None` | — | Bounded exact-name checks from this host on the same connection and target. Use instead of host_tool_names. Incomplete misses stay unknown. |
| `selection` | `Literal['ranked', 'agent']` | — | Choose agent for exhaustive browsing or ranked for lexical and required evidence search. |
| `family` | `str \| None` | — | Agent browse only. Provide an exact family ID. query is optional. |
| `offset` | `int` | — | Agent browse only. Follow next_offset. |
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

