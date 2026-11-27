---
title: manage_tools
sidebar_label: manage_tools
description: "Find Unity tools and resources."
---

# `manage_tools`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_tools`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Find Unity tools and resources. If the guide is listed, call manage_tools(action="read_skill") without a document selector. When unavailable, use the host's installed skill loader. For another published skill file, follow read_tool from manage_tools(action="catalog",group="skills").
- read_resource: Read a published observation using its catalog tool binding. Use native resources/read for other providers and evaluate_getters=true.
Actions: list_instances (list connected Editors on this connection, even without a resource browser), list_groups (show all groups and their status), activate/deactivate (select exactly one group or tool), sync (refresh availability from Unity Editor's toggle states), reset (restore configured profile), describe (start with tool alone and follow a returned operation_choices call for its inputs), prepare (return schema-checked arguments and current public permission evidence without executing the call), catalog (browse or filter by group or tool, or search by query alone), search (discover routes for a task in any language), inspect_inventory (compare server advertisement with this host's exact-name evidence). For exhaustive browsing, pass "selection": "agent" and "query": the original text. Supply all search fields together at the top level or in one query object. To open a family, set "family" to its returned ID in open_family.arguments. Follow next_offset. For lexical or required evidence searches, use selection=ranked. catalog_query, intent, observation_source and required_values are ranked only. Selection changes server tools/list; it does not confirm the client's callable inventory. Use sync after toggling tools in the Unity Editor GUI.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_groups', 'list_instances', 'activate', 'deactivate', 'sync', 'reset', 'describe', 'search', 'catalog', 'inspect_inventory', 'prepare', 'read_skill', 'read_resource']` | yes | Action to perform. |
| `group` | `str \| None` | — | Group name for activate/deactivate/catalog. Catalog also supports resources for fixed Unity URIs, resource_templates for parameterized observations and skills for published skill files. Valid groups: animation, asset_gen, assets, camera, core, docs, graphics, legacy, physics, prefabs, probuilder, profiling, scene, scripting_ext, testing, ui, vfx |
| `tool` | `str \| None` | — | Single exact tool name for activate/deactivate/describe/prepare/catalog/inspect_inventory. |
| `query` | `str \| None` | — | Original user request. Keep its language, names, paths and text verbatim. Required for the agent directory. Optional for an exact agent family or ranked evidence search. |
| `limit` | `int` | — | Requested entries per page for search, catalog or a tool's operation choices. At most 20 per page. |
| `include_schema` | `bool` | — | Describe always includes the schema. On single-tool activation attach schema and readiness. On inspect_inventory include the advertised inputSchema and its SHA-256. |
| `catalog_query` | `str \| None` | — | Ranked search only. Optional English action and target terms or exact tool/action identifier for lexical matching. Keep the original request in query. |
| `offset` | `int` | — | Page offset for catalog, tool-only describe or selection=agent search. |
| `operation` | `str \| None` | — | Omit when only the tool name is known. Copy a returned operation_choices or describe_call binding to select an action or a unique advertised input choice. For an ambiguous choice, draft the actual input field without operation. |
| `intent` | `Literal['observe', 'interact', 'edit'] \| None` | — | Choose the requested purpose by meaning in any language: observe for reading state, interact for operating the Editor or running game, edit for changing project content. Use with selection=ranked for search or with a tool/group in catalog. |
| `observation_source` | `Literal['editor_live', 'asset_file', 'imported_asset', 'compile_job', 'scenario_job'] \| None` | — | Ranked search only. Require evidence from the current Editor (editor_live), a saved file (asset_file), import results (imported_asset), a compilation job (compile_job), or a Play Scenario job (scenario_job). |
| `required_values` | `list[Literal['unityCameras', 'cinemachineCameras', 'cinemachineInstalled', 'render_size.width', 'render_size.height', 'current_size', 'view_instance_id', 'presets', 'rendered_image', 'pointer_hit_order', 'console.entries', 'menu.declared_paths', 'active_scene.name', 'active_scene.path', 'compilation.is_compiling', 'compile_job.outcome', 'scenario_job.outcome', 'nodeCount', 'edgeCount', 'nodes', 'edges', 'properties', 'keywords', 'unsaved_graph_structure', 'property_values', 'shader_source', 'contents', 'encodedContents', 'sourceFileSha256', 'nextOffset', 'shader_graph_structure', 'unsaved_source', 'compilation', 'passes', 'subshader_count', 'navigation.module_available', 'navigation.package_available', 'project_auditor.api_supported', 'project_auditor.rules_available']] \| None` | — | Ranked search only. Require every listed result field as evidence. Combine with observation_source to require its provenance. |
| `route_surfaces` | `Literal['both', 'tools', 'resources'] \| None` | — | For either search mode, show tools, resources or both (default). |
| `host_tool_names` | `list[str] \| None` | — | For search, prepare or inspect_inventory, supply the host's complete callable tool names. Omit if unknown. |
| `host_tool_evidence` | `HostToolEvidence \| None` | — | For search, prepare or inspect_inventory, check exact names on this host's same connection and target. Use instead of host_tool_names. Incomplete misses stay unknown. |
| `selection` | `Literal['ranked', 'agent']` | — | Choose agent for exhaustive browsing or ranked for lexical and required evidence search. |
| `family` | `str \| None` | — | Agent browse only. Provide an exact family ID. query is optional. |
| `arguments` | `dict[str, Any] \| None` | — | Prepare only: proposed input object for the exact tool. Pass the original user text in query when needed. |
| `exact_text` | `list[ExactTextAssertion] \| None` | — | For user-specified exact text, add exact_text and the unchanged original query alongside arguments on prepare. Use an array of {path, expected} objects. Each path is an array of exact keys or zero-based array indices within arguments. Copy expected verbatim from query. |
| `skill_uri` | `str \| None` | — | For read_skill, supply a listed skill:// URI. Omit to read the packaged guide. |
| `uri` | `str \| None` | — | For read_resource, follow a catalog observation binding. Use native resources/read for getter execution. |
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

