---
title: apply_physics_force
sidebar_label: apply_physics_force
description: "Apply forces, torque or explosions to rigidbodies."
---

# `apply_physics_force`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Apply forces, torque or explosions to rigidbodies.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['apply_force']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `target` | `str \| None` | — | Target GameObject name or instance ID. |
| `search_method` | `str \| None` | — | Search method for target resolution. |
| `position` | `list[float] \| None` | — | Overlap position [x,y,z] or [x,y]. |
| `force` | `list[float] \| None` | — | Force vector [x,y,z] or [x,y] for apply_force. |
| `force_mode` | `str \| None` | — | Force mode: Force, Impulse, Acceleration, VelocityChange (3D); Force, Impulse (2D). |
| `force_type` | `str \| None` | — | Force type: 'normal' (default) or 'explosion' (3D only). |
| `torque` | `list[float] \| None` | — | Torque vector [x,y,z] (3D) or [z] (2D). |
| `explosion_position` | `list[float] \| None` | — | Explosion center [x,y,z]. |
| `explosion_radius` | `float \| None` | — | Explosion radius. |
| `explosion_force` | `float \| None` | — | Explosion force magnitude. |
| `upwards_modifier` | `float \| None` | — | Explosion upwards modifier. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

