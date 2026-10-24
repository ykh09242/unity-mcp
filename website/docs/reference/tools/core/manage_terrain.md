---
title: manage_terrain
sidebar_label: manage_terrain
description: "Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays."
---

# `manage_terrain`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_terrain`

## Description

Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays. Regions are at most32x32 and4096 scalars. get_trees/set_trees use1..64 exact existing indices; get_layer/set_layer replace one existing layer slot, preserving count and weights within a65536-scalar full-alphamap verification budget. Writes require expected_data_guid and the exact scoped expected_revision from the corresponding read. Shared loaded users and boundary patches require explicit acknowledgements; neighboring terrains are never changed. Authoring refuses native Undo snapshot estimates above16777216 scalar units. Native Undo is registered; failures use conditional compensation and preserve external writers. Edits mark TerrainData dirty; no global asset save is performed. No terrain creation/resolution change, tree/prototype insertion or whole-terrain dump.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | Terrain action. |
| `target` | `str` | yes | Existing Terrain GameObject id/name/path. |
| `search_method` | `Literal['by_id', 'by_name', 'by_path'] \| None` | — |  |
| `channel` | `str \| None` | — |  |
| `region` | `dict[str, Any] \| None` | — |  |
| `values` | `list[Any] \| None` | — |  |
| `indices` | `list[int] \| None` | — |  |
| `detail_layer` | `int \| None` | — |  |
| `index` | `int \| None` | — |  |
| `layer_path` | `str \| None` | — |  |
| `layer_guid` | `str \| None` | — |  |
| `expected_data_guid` | `str \| None` | — |  |
| `expected_revision` | `str \| None` | — |  |
| `allow_shared_data` | `bool` | — |  |
| `allow_boundary` | `bool` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

