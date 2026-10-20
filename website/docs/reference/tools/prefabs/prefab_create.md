---
title: prefab_create
sidebar_label: prefab_create
description: "Create a prefab asset from an existing GameObject."
---

# `prefab_create`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create a prefab asset from an existing GameObject.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create_from_gameobject']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). |
| `target` | `str \| int \| None` | — | For create_from_gameobject: instance ID (integer or numeric string), unique name, or full hierarchy path (e.g. 'Parent/Child' or '/Parent/Child') in the active scene/current prefab stage. Stage names/paths take precedence. Numeric targets are IDs; ambiguous names/paths fail. For modify_contents: saved prefab object name or child path; omit to target the root. Use returned rootObjectName/rootObjectPath, which may differ from the source instance name. IDs do not apply to isolated prefab contents. |
| `allow_overwrite` | `bool \| None` | — | Allow replacing existing prefab. |
| `search_inactive` | `bool \| None` | — | Include inactive GameObjects in search. |
| `unlink_if_instance` | `bool \| None` | — | Unlink from existing prefab before creating new one. |
| `name` | `str \| None` | — | New name for the target object in modify_contents. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

