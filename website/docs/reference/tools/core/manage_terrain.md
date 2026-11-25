---
title: manage_terrain
sidebar_label: manage_terrain
description: "Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays."
---

# `manage_terrain`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_terrain`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect and edit existing TerrainData with bounded region/index requests. get_info lists identities/resolutions/layers/prototypes; get_region/set_region use heights, alphamaps, holes (true=surface) or details with exact [y][x] arrays. Regions are at most 32x32 and 4096 scalars. get_trees/set_trees use 1..64 exact existing indices; get_layer/set_layer replace one existing layer slot, preserving count and weights within a 65536-scalar full-alphamap verification budget. Writes require expected_data_guid and the exact scoped expected_revision from the corresponding read. Shared loaded users and boundary patches require explicit acknowledgements; neighboring terrains are never changed. Authoring refuses native Undo snapshot estimates above 16777216 scalar units. Native Undo is registered; failures use conditional compensation and preserve external writers. Edits mark TerrainData dirty; no global asset save is performed. No terrain creation/resolution change, tree/prototype insertion or whole-terrain dump.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | Terrain operation. Use get_region and set_region for grid cells. |
| `target` | `str` | yes | Existing Terrain GameObject id/name/path. |
| `search_method` | `Literal['by_id', 'by_name', 'by_path'] \| None` | — |  |
| `channel` | `str \| None` | — | Region channel: heights, alphamaps, holes or details. |
| `region` | `dict[str, Any] \| None` | — | Zero-based cell rectangle for get_region and set_region. Read the exact rectangle you intend to change. |
| `values` | `list[Any] \| None` | — | Replacement values for set_region or set_trees. Region rows are [y][x]. For holes, true means surface and false means a hole. |
| `indices` | `list[int] \| None` | — | Ordered list of 1 to 64 distinct existing tree indices for get_trees and set_trees. |
| `detail_layer` | `int \| None` | — | Existing detail layer index. Required for the details channel and included in the region revision. |
| `index` | `int \| None` | — | Existing TerrainLayer slot for get_layer and set_layer. |
| `layer_path` | `str \| None` | — | Asset path of the replacement TerrainLayer for set_layer. |
| `layer_guid` | `str \| None` | — | GUID of the replacement asset at layer_path for set_layer. |
| `expected_data_guid` | `str \| None` | — | Required for writes. Copy data.data_guid from the corresponding read of the target. |
| `expected_revision` | `str \| None` | — | Required for writes. Copy data.revision from the read of the same channel, rectangle and detail layer, ordered tree indices, or layer slot. |
| `allow_shared_data` | `bool` | — |  |
| `allow_boundary` | `bool` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
To inspect a cell, read a one-cell rectangle. The returned `data.values[0][0]` is `true` for surface and `false` for a hole.

```json title="MCP arguments"
{
  "action": "get_region",
  "target": "Terrain",
  "search_method": "by_name",
  "channel": "holes",
  "region": {"x": 4, "y": 9, "width": 1, "height": 1}
}
```

To restore the surface, send the same target, channel, and region with `action="set_region"` and `values=[[true]]`. Set `expected_data_guid` to the returned `data.data_guid` and `expected_revision` to `data.revision`. Reading a larger rectangle produces a different revision. A fresh read can confirm the surrounding cells afterwards.
<!-- examples:end -->

