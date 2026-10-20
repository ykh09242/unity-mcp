---
title: gameobject_duplicate
sidebar_label: gameobject_duplicate
description: "Duplicate a GameObject with optional name, parent and position changes."
---

# `gameobject_duplicate`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Duplicate a GameObject with optional name, parent and position changes.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['duplicate']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | GameObject identifier by name, path, or instance ID for modify/delete/duplicate actions |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer', 'by_component'] \| None` | — | How to resolve 'target'. If omitted, Unity infers: instance ID -> by_id, path (contains '/') -> by_path, otherwise by_name. |
| `name` | `str \| None` | — | GameObject name for 'create' (initial name) and 'modify' (rename) actions. |
| `parent` | `str \| None` | — | Parent GameObject reference - used for both 'create' (initial parent) and 'modify' (change parent) |
| `position` | `list[float] \| dict[str, float] \| str \| None` | — | Position as [x, y, z] array, {x, y, z} object, or JSON string |
| `new_name` | `str \| None` | — | New name for the duplicated object (default: SourceName_Copy) |
| `offset` | `list[float] \| str \| None` | — | Offset from original/reference position as [x, y, z] array (list or JSON string) |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

