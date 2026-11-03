---
title: gameobject_edit
sidebar_label: gameobject_edit
description: "Modify a GameObject or delete all selector matches, including inactive objects, in the active scene or current prefab stage."
---

# `gameobject_edit`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Modify a GameObject or delete all selector matches, including inactive objects, in the active scene or current prefab stage. Use by_id with a current instance ID to delete one object.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['modify', 'delete']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | GameObject identifier by name, path, or instance ID for modify/delete/duplicate actions |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer', 'by_component'] \| None` | — | How to resolve 'target'. If omitted, Unity infers: instance ID -> by_id, path (contains '/') -> by_path, otherwise by_name. Delete removes all matches, including inactive objects; use by_id with a current instance ID to delete one object. |
| `name` | `str \| None` | — | GameObject name for 'create' (initial name) and 'modify' (rename) actions. |
| `tag` | `str \| None` | — | Tag name - used for both 'create' (initial tag) and 'modify' (change tag) |
| `parent` | `str \| None` | — | Parent GameObject reference - used for both 'create' (initial parent) and 'modify' (change parent) |
| `position` | `list[float] \| dict[str, float] \| str \| None` | — | Position as [x, y, z] array, {x, y, z} object, or JSON string |
| `rotation` | `list[float] \| dict[str, float] \| str \| None` | — | Rotation as [x, y, z] euler angles array, {x, y, z} object, or JSON string |
| `scale` | `list[float] \| dict[str, float] \| str \| None` | — | Scale as [x, y, z] array, {x, y, z} object, or JSON string |
| `space` | `Literal['local', 'world'] \| None` | — | Position/rotation space; local default. Scale is local only. |
| `world_position_stays` | `bool \| str \| None` | — | Preserve world TRS when parenting; true default. |
| `detach` | `bool \| str \| None` | — | Modify: explicitly remove parent; conflicts with a nonempty parent. |
| `sibling_index` | `int \| None` | — | Zero-based final sibling index, validated before mutation. |
| `components_to_add` | `list[str \| dict[str, Any]] \| dict[str, Any] \| str \| None` | — | List of components to add during 'create' or 'modify'. Each entry is either                                  a plain type name string (e.g. "BoxCollider") or an object                                  {"typeName": "BoxCollider", "properties": {"size": [2, 2, 2]}} that adds the                                  component with initial properties applied in the same call. Mixing both forms                                  in one list is fine. |
| `set_active` | `bool \| str \| None` | — | If True, sets the GameObject active (accepts true/false or 'true'/'false') |
| `layer` | `str \| None` | — | Layer name |
| `is_static` | `bool \| str \| None` | — | Set the GameObject's static flag. true = all StaticEditorFlags, false = none (accepts true/false or 'true'/'false') |
| `components_to_remove` | `list[str] \| str \| None` | — | List of component names to remove |
| `component_properties` | `dict[str, dict[str, Any]] \| str \| None` | — | Dictionary of component names to their properties to set. Works for both                                     'create' (applied to components already present on the new GameObject - add                                     them via components_to_add first) and 'modify'. For example:                                     `{"MyScript": {"otherObject": {"find": "Player", "method": "by_name"}}}` assigns GameObject                                     `{"MyScript": {"playerHealth": {"find": "Player", "component": "HealthComponent"}}}` assigns Component                                     Example set nested property:                                     - Access shared material: `{"MeshRenderer": {"sharedMaterial.color": [1, 0, 0, 1]}}` |
| `new_name` | `str \| None` | — | New name for the duplicated object (default: SourceName_Copy) |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Delete one specific object by its instance ID (obtain it from a current search):

```json
{"action":"delete","target":"12345","search_method":"by_id"}
```

Name, path, tag, layer, and component selectors can match multiple objects. Delete removes all matches in the active scene or current prefab stage, including inactive objects. For example, this intentionally deletes every object named `Crate` in that scope:

```json
{"action":"delete","target":"Crate","search_method":"by_name"}
```

The legacy `manage_gameobject` delete action has the same bulk selection behavior. Use `by_id` when only one object should be deleted. Instance IDs are session-local; search again after an Editor restart.
<!-- examples:end -->

