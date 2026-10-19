---
title: manage_scene_edits
sidebar_label: manage_scene_edits
description: "Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene."
---

# `manage_scene_edits`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_scene_edits`

## Description

Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene. Preview returns before/after values and a one-attempt token valid for 10 minutes. Apply accepts preview_id, or edits with expected_old for every field. Rejects drift before mutation; one named Undo restores supported recorded component values. No arbitrary transaction or filesystem, build, package, custom callback, dirty-flag rollback is promised. preview_structure takes an integer scene-root target scope and 1-32 ordered create/delete/reparent operations. Only ordinary Transform-only subtrees are supported; prefab instances and custom/native component subtrees are refused. create assigns a $temporary ID that later operations may reference. reparent preserves local transforms. apply_structure requires the preview token and rejects hierarchy/state drift before its one Undo group.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['preview', 'apply', 'preview_structure', 'apply_structure']` | yes |  |
| `edits` | `list[SceneEdit] \| None` | — |  |
| `preview_id` | `str \| None` | — |  |
| `undo_name` | `str` | — |  |
| `target` | `int \| None` | — |  |
| `operations` | `list[StructureCreate \| StructureReparent \| StructureDelete] \| None` | — |  |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

