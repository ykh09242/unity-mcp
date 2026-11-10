---
title: query_physics
sidebar_label: query_physics
description: "Run ray, shape and overlap queries in 2D or 3D. overlap/raycast_all use bounded native buffers: max_results is an integer 1-1000 (default 200). buffer_saturated means completeness is unknown, including exact capacity."
---

# `query_physics`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Run ray, shape and overlap queries in 2D or 3D. overlap/raycast_all use bounded native buffers: max_results is an integer 1-1000 (default 200). buffer_saturated means completeness is unknown, including exact capacity. Ray results are distance-sorted within the returned subset; saturated queries do not guarantee globally nearest hits. Overlap ordering is unspecified. Limits bound materialization, not native traversal time. In 2D, Collide requires Physics2D.queriesHitTriggers=true; otherwise returns unsupported_2d_trigger_override without querying or changing settings. Read-only inspection; results can change with Unity state.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['raycast', 'raycast_all', 'linecast', 'shapecast', 'overlap']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `origin` | `list[float] \| None` | — | Ray origin [x,y,z] or [x,y]. |
| `direction` | `list[float] \| None` | — | Ray direction [x,y,z] or [x,y]. |
| `max_distance` | `float \| None` | — | Max raycast distance. |
| `max_results` | `int \| None` | — | overlap/raycast_all: native result capacity, integer 1-1000 (default 200). Response adds max_results, returned_count, buffer_saturated, ordering; raycast_all also adds nearest_hits_guaranteed. A full buffer may omit results even when returned hits are distance-sorted. |
| `layer_mask` | `str \| None` | — | Layer mask for queries (name or int). |
| `query_trigger_interaction` | `str \| None` | — | Trigger interaction: UseGlobal, Ignore, Collide. In 2D, Collide requires Physics2D.queriesHitTriggers=true; otherwise fails with unsupported_2d_trigger_override without running the query. |
| `shape` | `str \| None` | — | Overlap shape: sphere, box, capsule (3D); circle, box, capsule (2D). |
| `position` | `list[float] \| None` | — | Overlap position [x,y,z] or [x,y]. |
| `size` | `Any \| None` | — | Overlap size: float (radius) or [x,y,z] (half-extents). |
| `start` | `list[float] \| None` | — | Linecast start point [x,y,z] or [x,y]. |
| `end` | `list[float] \| None` | — | Linecast end point [x,y,z] or [x,y]. |
| `point1` | `list[float] \| None` | — | Capsule shapecast point1 [x,y,z]. |
| `point2` | `list[float] \| None` | — | Capsule shapecast point2 [x,y,z]. |
| `height` | `float \| None` | — | Capsule height for shapecast. |
| `capsule_direction` | `int \| None` | — | Capsule direction: 0=X, 1=Y (default), 2=Z. |
| `angle` | `float \| None` | — | Rotation angle for 2D shape casts. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

