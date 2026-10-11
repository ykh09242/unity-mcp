---
title: manage_scene_edits
sidebar_label: manage_scene_edits
description: "Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, or Rigidbody serialized property edits in a stable Edit-mode scene."
---

# `manage_scene_edits`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_scene_edits`

## Description

Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, or Rigidbody serialized property edits in a stable Edit-mode scene. Preview returns before/after values and a one-attempt token valid for 10 minutes. Apply accepts preview_id, or edits with expected_old for every field. Rejects drift before mutation; one named Undo restores supported recorded component values. No arbitrary transaction or filesystem, build, package, custom callback, dirty-flag rollback is promised.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['preview', 'apply']` | yes |  |
| `edits` | `list[SceneEdit] \| None` | — |  |
| `preview_id` | `str \| None` | — |  |
| `undo_name` | `str` | — |  |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

