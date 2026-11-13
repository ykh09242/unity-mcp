---
title: prefab_create
sidebar_label: prefab_create
description: "Create a prefab asset from an existing GameObject."
---

# `prefab_create`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Create a prefab asset from an existing GameObject.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create_from_gameobject']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). For save/close, when supplied it must match the current Prefab Stage. |
| `target` | `str \| int \| None` | — | For create_from_gameobject: instance ID (integer or numeric string), unique name, or full hierarchy path (e.g. 'Parent/Child' or '/Parent/Child') in the active scene/current prefab stage. Stage names/paths take precedence. Numeric targets are IDs; ambiguous names/paths fail. For modify_contents: saved prefab object name or child path; omit to target the root. Use returned rootObjectName/rootObjectPath, which may differ from the source instance name. IDs do not apply to isolated prefab contents. |
| `allow_overwrite` | `bool \| None` | — | Allow replacing existing prefab. |
| `search_inactive` | `bool \| None` | — | Include inactive GameObjects in search. |
| `unlink_if_instance` | `bool \| None` | — | Unpack the outermost prefab instance root before creating a new prefab; child targets are rejected. A failed save may leave the scene unpacked. |
| `name` | `str \| None` | — | New name for the target object in modify_contents. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
See the [scene and prefab editing guide](../../../guides/scene-and-prefab-edits.md)
and [returned-root targeting examples](../legacy/manage_prefabs.md#examples).
Replace the target with a verified scene/stage object ID or unique hierarchy path,
and choose a new asset path:

```json
{
  "action": "create_from_gameobject",
  "target": "Props/Crate",
  "prefab_path": "Assets/Prefabs/Crate.prefab",
  "allow_overwrite": false,
  "unlink_if_instance": false
}
```

Existing destinations require explicit `allow_overwrite: true`. For a prefab instance,
review the unpack scope before setting `unlink_if_instance: true`: the target must be
its outermost instance root, not a nested child. On failure, inspect the error and
scene/destination state before retrying; unpacking can require Undo or manual restoration.
Keep `rootObjectName`/`rootObjectPath` from the create result
for subsequent asset edits; they can differ from the source instance name.
<!-- examples:end -->

