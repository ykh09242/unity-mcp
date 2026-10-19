---
title: manage_animation
sidebar_label: manage_animation
description: "Manage Unity animation: Animator control and AnimationClip creation."
---

# `manage_animation`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `animation` &nbsp;·&nbsp; **Module:** `services.tools.manage_animation`

## Description

Manage Unity animation: Animator control and AnimationClip creation. Action prefixes: animator_* (play, crossfade, set parameters, get info), controller_* (create AnimatorControllers, add states/transitions/parameters), clip_* (create clips, add keyframe curves, assign to GameObjects). Action-specific parameters go in `properties` (keys match ManageAnimation.cs). Round trips: clip_get_curves lists binding_id/kind/path/property/type; clip_get_curve returns paged full float keys (tangents, weights, tangent modes) or exact asset object-reference keys {guid,file_id}. clip_replace_curve replaces one existing binding with 1-1024 ordered keys; clip_delete_curve removes that binding. Both require binding_id and expected_revision. clip_file_id selects an exact clip subasset; imported clips are read-only. controller_get_graph pages nested state machines, states and transition identities. controller_modify_state/delete_state require state_id; controller_modify_transition/delete_transition require transition_id. Writes require expected_revision and modify uses changes. State changes support name/speed/exact motion refs; transition changes support duration/exit_time/offset/flags, conditions and exact destination_state_id. State deletion refuses default or referenced states. Pages use offset/page_size (1-100), with expected_revision required after offset zero. New actions are bounded to contained Assets sources, support native Undo and preserve GUID/file IDs; no arbitrary callback/filesystem transaction is promised.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | Action to perform (prefix: animator_, controller_, clip_). |
| `target` | `str \| None` | — | Target GameObject (name/path/id). |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer'] \| None` | — | How to find the target GameObject. |
| `clip_path` | `str \| None` | — | Asset path for AnimationClip (e.g. 'Assets/Animations/Walk.anim'). |
| `controller_path` | `str \| None` | — | Asset path for AnimatorController (e.g. 'Assets/Animators/Player.controller'). |
| `properties` | `dict[str, Any] \| str \| None` | — | Action-specific parameters (dict or JSON object string). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

