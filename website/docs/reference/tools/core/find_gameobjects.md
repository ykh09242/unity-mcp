---
title: find_gameobjects
sidebar_label: find_gameobjects
description: "Search GameObjects by name, tag, layer, component, path or ID; defaults to IDs only."
---

# `find_gameobjects`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.find_gameobjects`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Search GameObjects by name, tag, layer, component, path or ID; defaults to IDs only. Optional projection fields name/active/transform/components/handle describe the same returned IDs, capped at 64 rows, 16 KiB and 16 component names per row; projection_verification reports incomplete coverage. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded covers loaded ordinary scenes. exact requires scene_path and reports resultScene. Scoped modes exclude preview/prefab stages. Omit filters to list objects in an explicit scene scope. Supplied filters are ANDed (at most eight predicates total). Scoped pages use opaque cursors and require identical options and unchanged scenes. Empty continuation pages are valid: hasMore means remaining scan work, not more matches. totalCount is unknown until complete and then exact only through max_depth (default64, max128). Limits: 256 scene entries, 250000 roots/scene, one root enumeration/page, 4096 managed traversal steps/page, 500 IDs and 16384 output bytes. Root preparation is accounted separately; native Unity calls are synchronous and not time-bounded. Restart after edits, expiry or reload. Use mcpforunity://scene/gameobject/{id} for details or gameobject_edit for edits.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `search_term` | `str \| None` | — | Search value. Required in legacy mode. Omit filters to list an explicit scene scope. |
| `search_method` | `Literal['by_name', 'by_tag', 'by_layer', 'by_component', 'by_path', 'by_id']` | — | How to search for GameObjects |
| `include_inactive` | `bool \| str \| None` | — | Include inactive objects across the selected scope |
| `page_size` | `int \| str \| None` | — | Default50; scoped mode requires integer1..500; legacy keeps coercion/clamping |
| `cursor` | `int \| str \| None` | — | Legacy numeric offset or scoped opaque32-hex continuation token |
| `scene_scope` | `Literal['legacy', 'active', 'all_loaded', 'exact']` | — | Explicit scene selection; legacy preserves existing method-dependent scope |
| `scene_path` | `str \| None` | — | Exact loaded Assets/ or Packages/ .unity path for scene_scope=exact |
| `conditions` | `list[dict[str, Any]] \| None` | — | AND predicates with exactly search_method and search_term; max8 including top-level search |
| `max_depth` | `int \| None` | — | Scoped inclusive depth: root0, default64, maximum128 |
| `projection` | `list[Literal['name', 'active', 'transform', 'components', 'handle']] \| None` | — | Optional same-ID projection; handles use existing session-scoped lifetime |
| `refresh_if_dirty` | `bool` | — | Set false to search the current scene without a preflight asset refresh. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Find objects with a `Camera` component in the active scene. Include inactive cameras and ask for a small page of names, activation state, and handles. If `hasMore` is true, reuse the returned cursor with the same search options.

```json title="MCP arguments"
{
  "search_term": "Camera",
  "search_method": "by_component",
  "scene_scope": "active",
  "include_inactive": true,
  "page_size": 20,
  "projection": [
    "name",
    "active",
    "handle"
  ],
  "refresh_if_dirty": false
}
```
<!-- examples:end -->

