---
title: find_gameobjects
sidebar_label: find_gameobjects
description: "Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded co…"
---

# `find_gameobjects`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.find_gameobjects`

## Description

Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. active uses SceneManager's reported active scene; all_loaded covers loaded ordinary scenes, excluding preview/prefab stages. Scoped conditions are ANDed (at most eight predicates total). Scoped pages use opaque cursors and require identical options and unchanged scenes. Empty continuation pages are valid: hasMore means remaining scan work, not more matches. totalCount is unknown until complete and then exact only through max_depth (default64, max128). Limits: 32 scene entries, 4096 roots/scene, one root enumeration/page, 4096 managed traversal steps/page, 500 IDs and 16384 output bytes. Root preparation is accounted separately; native Unity calls are synchronous and not time-bounded. Restart after edits, expiry or reload. Use mcpforunity://scene/gameobject/{id} for details or manage_gameobject for edits.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `search_term` | `str \| None` | — | Search value; optional for scoped conditions-only queries |
| `search_method` | `Literal['by_name', 'by_tag', 'by_layer', 'by_component', 'by_path', 'by_id']` | — | How to search for GameObjects |
| `include_inactive` | `bool \| str \| None` | — | Include inactive objects across the selected scope |
| `page_size` | `int \| str \| None` | — | Default50; scoped mode requires integer1..500; legacy keeps coercion/clamping |
| `cursor` | `int \| str \| None` | — | Legacy numeric offset or scoped opaque32-hex continuation token |
| `scene_scope` | `Literal['legacy', 'active', 'all_loaded']` | — | Explicit scene selection; legacy preserves existing method-dependent scope |
| `conditions` | `list[dict[str, Any]] \| None` | — | AND predicates with exactly search_method and search_term; max8 including top-level search |
| `max_depth` | `int \| None` | — | Scoped inclusive depth: root0, default64, maximum128 |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

