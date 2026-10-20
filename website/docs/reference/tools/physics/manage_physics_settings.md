---
title: manage_physics_settings
sidebar_label: manage_physics_settings
description: "Inspect or change 2D/3D physics settings and layer collision rules."
---

# `manage_physics_settings`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect or change 2D/3D physics settings and layer collision rules.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'get_settings', 'set_settings', 'get_collision_matrix', 'set_collision_matrix']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `settings` | `dict[str, Any] \| None` | — | Key-value settings for set_settings. |
| `layer_a` | `str \| None` | — | Layer name or index for collision matrix. |
| `layer_b` | `str \| None` | — | Layer name or index for collision matrix. |
| `collide` | `bool \| None` | — | Whether layers should collide (set_collision_matrix). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

