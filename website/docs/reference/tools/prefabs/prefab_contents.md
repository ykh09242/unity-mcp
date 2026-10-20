---
title: prefab_contents
sidebar_label: prefab_contents
description: "Edit prefab contents or preview and apply a guarded transform change."
---

# `prefab_contents`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Edit prefab contents or preview and apply a guarded transform change.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['modify_contents', 'preview_modify_contents', 'apply_preview']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). |
| `target` | `str \| int \| None` | — | For create_from_gameobject: instance ID (integer or numeric string), unique name, or full hierarchy path (e.g. 'Parent/Child' or '/Parent/Child') in the active scene/current prefab stage. Stage names/paths take precedence. Numeric targets are IDs; ambiguous names/paths fail. For modify_contents: saved prefab object name or child path; omit to target the root. Use returned rootObjectName/rootObjectPath, which may differ from the source instance name. IDs do not apply to isolated prefab contents. |
| `position` | `list[float] \| dict[str, float] \| str \| None` | — | New local position [x, y, z] or {x, y, z} for modify_contents. |
| `rotation` | `list[float] \| dict[str, float] \| str \| None` | — | New local rotation (euler angles) [x, y, z] or {x, y, z} for modify_contents. |
| `scale` | `list[float] \| dict[str, float] \| str \| None` | — | New local scale [x, y, z] or {x, y, z} for modify_contents. |
| `name` | `str \| None` | — | New name for the target object in modify_contents. |
| `tag` | `str \| None` | — | New tag for the target object in modify_contents. |
| `layer` | `str \| None` | — | New layer name for the target object in modify_contents. |
| `set_active` | `bool \| None` | — | Set active state of target object in modify_contents. |
| `parent` | `str \| None` | — | New parent object name/path within prefab for modify_contents. |
| `components_to_add` | `list[str] \| None` | — | Component types to add in modify_contents. |
| `components_to_remove` | `list[str] \| None` | — | Component types to remove in modify_contents. |
| `create_child` | `dict[str, Any] \| list[dict[str, Any]] \| None` | — | Create child GameObject(s) in the prefab. Single object or array of objects, each with: name (required), parent (optional, defaults to target), source_prefab_path (optional: asset path to instantiate as nested prefab, e.g. 'Assets/Prefabs/Bullet.prefab'), primitive_type (optional: Cube, Sphere, Capsule, Cylinder, Plane, Quad), position, rotation, scale, components_to_add, tag, layer, set_active. source_prefab_path and primitive_type are mutually exclusive. |
| `delete_child` | `str \| list[str] \| None` | — | Child name(s) or path(s) to remove from the prefab. Supports single string or array for batch deletion (e.g. 'Child1' or ['Child1', 'Child1/Grandchild']). |
| `component_properties` | `dict[str, dict[str, Any]] \| None` | — | Set properties on existing components in modify_contents. Keys are component type names, values are dicts of property name to value. Example: {"Rigidbody": {"mass": 5.0}, "MyScript": {"health": 100}}. Supports object references via {"guid": "..."}, {"path": "Assets/..."}, or {"instanceID": 123}. For Sprite sub-assets: {"guid": "...", "spriteName": "<name>"}. Single-sprite textures auto-resolve. |
| `preview_id` | `str \| None` | — | One-attempt token from preview_modify_contents, valid 10 minutes. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

