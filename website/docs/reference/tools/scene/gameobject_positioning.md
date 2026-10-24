---
title: gameobject_positioning
sidebar_label: gameobject_positioning
description: "Move a GameObject relative to a reference or orient it toward a target."
---

# `gameobject_positioning`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Move a GameObject relative to a reference or orient it toward a target.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['move_relative', 'look_at']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | GameObject identifier by name, path, or instance ID for modify/delete/duplicate actions |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer', 'by_component'] \| None` | — | How to resolve 'target'. If omitted, Unity infers: instance ID -> by_id, path (contains '/') -> by_path, otherwise by_name. |
| `name` | `str \| None` | — | GameObject name for 'create' (initial name) and 'modify' (rename) actions. |
| `offset` | `list[float] \| str \| None` | — | Offset from original/reference position as [x, y, z] array (list or JSON string) |
| `reference_object` | `str \| None` | — | Reference object for relative movement (required for move_relative) |
| `direction` | `Literal['left', 'right', 'up', 'down', 'forward', 'back', 'front', 'backward', 'behind'] \| None` | — | Direction for relative movement (e.g., 'right', 'up', 'forward') |
| `distance` | `float \| None` | — | Distance to move in the specified direction (default: 1.0) |
| `world_space` | `bool \| str \| None` | — | If True (default), use world space directions; if False, use reference object's local directions |
| `look_at_target` | `list[float] \| str \| None` | — | World position [x,y,z] or GameObject name/path/ID to look at (for look_at action). |
| `look_at_up` | `list[float] \| str \| None` | — | Optional up vector [x,y,z] for look_at. Defaults to [0,1,0]. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

