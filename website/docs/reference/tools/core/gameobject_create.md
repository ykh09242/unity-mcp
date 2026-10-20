---
title: gameobject_create
sidebar_label: gameobject_create
description: "Create a GameObject with transform, components and optional prefab output."
---

# `gameobject_create`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create a GameObject with transform, components and optional prefab output.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create']` | yes | Feature action to perform. |
| `name` | `str \| None` | — | GameObject name for 'create' (initial name) and 'modify' (rename) actions. |
| `tag` | `str \| None` | — | Tag name - used for both 'create' (initial tag) and 'modify' (change tag) |
| `parent` | `str \| None` | — | Parent GameObject reference - used for both 'create' (initial parent) and 'modify' (change parent) |
| `position` | `list[float] \| dict[str, float] \| str \| None` | — | Position as [x, y, z] array, {x, y, z} object, or JSON string |
| `rotation` | `list[float] \| dict[str, float] \| str \| None` | — | Rotation as [x, y, z] euler angles array, {x, y, z} object, or JSON string |
| `scale` | `list[float] \| dict[str, float] \| str \| None` | — | Scale as [x, y, z] array, {x, y, z} object, or JSON string |
| `space` | `Literal['local', 'world'] \| None` | — | Position/rotation space; local default. Scale is local only. |
| `world_position_stays` | `bool \| str \| None` | — | Preserve world TRS when parenting; true default. |
| `sibling_index` | `int \| None` | — | Zero-based final sibling index, validated before mutation. |
| `components_to_add` | `list[str \| dict[str, Any]] \| dict[str, Any] \| str \| None` | — | List of components to add during 'create' or 'modify'. Each entry is either                                  a plain type name string (e.g. "BoxCollider") or an object                                  {"typeName": "BoxCollider", "properties": {"size": [2, 2, 2]}} that adds the                                  component with initial properties applied in the same call. Mixing both forms                                  in one list is fine. |
| `primitive_type` | `str \| None` | — | Primitive type for 'create' action |
| `save_as_prefab` | `bool \| str \| None` | — | If True, saves the created GameObject as a prefab (accepts true/false or 'true'/'false') |
| `prefab_path` | `str \| None` | — | Path for prefab creation |
| `prefab_folder` | `str \| None` | — | Folder for prefab creation |
| `set_active` | `bool \| str \| None` | — | If True, sets the GameObject active (accepts true/false or 'true'/'false') |
| `layer` | `str \| None` | — | Layer name |
| `component_properties` | `dict[str, dict[str, Any]] \| str \| None` | — | Dictionary of component names to their properties to set. Works for both                                     'create' (applied to components already present on the new GameObject - add                                     them via components_to_add first) and 'modify'. For example:                                     `{"MyScript": {"otherObject": {"find": "Player", "method": "by_name"}}}` assigns GameObject                                     `{"MyScript": {"playerHealth": {"find": "Player", "component": "HealthComponent"}}}` assigns Component                                     Example set nested property:                                     - Access shared material: `{"MeshRenderer": {"sharedMaterial.color": [1, 0, 0, 1]}}` |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

