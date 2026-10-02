---
title: find_gameobjects
sidebar_label: find_gameobjects
description: "Search for GameObjects in the scene by name, tag, layer, component type, or path."
---

# `find_gameobjects`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.find_gameobjects`

## Description

Search for GameObjects in the scene by name, tag, layer, component type, or path. Returns instance IDs only (paginated). Then use mcpforunity://scene/gameobject/{id} resource for full data, or mcpforunity://scene/gameobject/{id}/components for component details. For CRUD operations (create/modify/delete), use manage_gameobject instead.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `search_term` | `str` | yes | The value to search for (name, tag, layer name, component type, or path) |
| `search_method` | `Literal['by_name', 'by_tag', 'by_layer', 'by_component', 'by_path', 'by_id']` | — | How to search for GameObjects |
| `include_inactive` | `bool \| str \| None` | — | Include inactive GameObjects in search |
| `page_size` | `int \| str \| None` | — | Number of results per page (default: 50, max: 500) |
| `cursor` | `int \| str \| None` | — | Pagination cursor (offset for next page) |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

