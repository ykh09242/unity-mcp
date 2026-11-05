---
title: manage_scene_edits
sidebar_label: manage_scene_edits
description: "Preview or apply 1-1000 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene."
---

# `manage_scene_edits`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_scene_edits`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Preview or apply 1-1000 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene. Preview returns before/after values and a one-attempt token valid for 10 minutes. Apply accepts preview_id, or edits with expected_old for every field. Rejects drift before mutation; one named Undo restores supported recorded component values. No arbitrary transaction or filesystem, build, package, custom callback, dirty-flag rollback is promised. preview_structure takes an integer scene-root target scope and 1-256 ordered create/delete/reparent operations. Only ordinary Transform-only subtrees are supported; prefab instances and custom/native component subtrees are refused. create assigns a $temporary ID that later operations may reference. reparent preserves local transforms. apply_structure requires the preview token and rejects hierarchy/state drift before its one Undo group.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['preview', 'apply', 'preview_structure', 'apply_structure']` | yes |  |
| `edits` | `list[SceneEdit] \| None` | — |  |
| `preview_id` | `str \| None` | — |  |
| `undo_name` | `str` | — |  |
| `target` | `int \| None` | — |  |
| `operations` | `list[StructureCreate \| StructureReparent \| StructureDelete] \| None` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
See the [scene and prefab editing guide](../../../guides/scene-and-prefab-edits.md) for
scope, drift checks and recovery. These are tool arguments; replace paths and IDs with
verified project values. Run in a stable Edit-mode scene.

Preview an existing object's local position:

```json
{
  "action": "preview",
  "edits": [
    {
      "target": "Player",
      "search_method": "by_path",
      "component_type": "Transform",
      "property": "localPosition",
      "value": [1, 2, 3]
    }
  ]
}
```

Review the returned before/after values, then replace `REPLACE_WITH_RETURNED_PREVIEW_ID`
with that response's `preview_id`. Do not also send `edits`:

```json
{
  "action": "apply",
  "preview_id": "REPLACE_WITH_RETURNED_PREVIEW_ID",
  "undo_name": "Move reviewed player"
}
```

For structural edits, replace `-123` with the current ID of an ordinary Transform-only
scene root. Temporary IDs can refer only to an earlier create operation in this request:

```json
{
  "action": "preview_structure",
  "target": -123,
  "operations": [
    {"op": "create", "temp_id": "$group", "name": "Props", "parent": -123},
    {"op": "create", "temp_id": "$child", "name": "Marker", "parent": "$group"}
  ]
}
```

Review the hierarchy impact, then use the token returned by that structural preview:

```json
{
  "action": "apply_structure",
  "preview_id": "REPLACE_WITH_RETURNED_PREVIEW_ID",
  "undo_name": "Create reviewed markers"
}
```

Each token permits one attempt within ten minutes. Re-preview after expiry or drift.
Successful apply records one Undo group; scene saving remains explicit.
<!-- examples:end -->

