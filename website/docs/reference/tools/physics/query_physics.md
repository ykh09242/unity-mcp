---
title: query_physics
sidebar_label: query_physics
description: "Run ray, shape and overlap queries in 2D or 3D."
---

# `query_physics`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Run ray, shape and overlap queries in 2D or 3D.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['raycast', 'raycast_all', 'linecast', 'shapecast', 'overlap']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `origin` | `list[float] \| None` | — | Ray origin [x,y,z] or [x,y]. |
| `direction` | `list[float] \| None` | — | Ray direction [x,y,z] or [x,y]. |
| `max_distance` | `float \| None` | — | Max raycast distance. |
| `layer_mask` | `str \| None` | — | Layer mask for queries (name or int). |
| `query_trigger_interaction` | `str \| None` | — | Trigger interaction: UseGlobal, Ignore, Collide. |
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

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

