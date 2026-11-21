---
title: manage_animation
sidebar_label: manage_animation
description: "Manage Unity animation: Animator control and AnimationClip creation."
---

# `manage_animation`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `animation` &nbsp;·&nbsp; **Module:** `services.tools.manage_animation`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Manage Unity animation: Animator control and AnimationClip creation. Asset edits include bounded changed-binding/node observations and existing revision semantics; unsupported key storage remains incomplete. No playback/evaluation is added. Action prefixes: animator_* (play, crossfade, set parameters, get info), controller_* (create AnimatorControllers, add states/transitions/parameters), clip_* (create clips, add keyframe curves, assign to GameObjects). Action-specific parameters go in `properties` (keys match ManageAnimation.cs). animator_set_parameter and animator_set_enabled require edit+run by default. Top-level runtime_only=true requires run and a playing scene instance. Older peers reject the dedicated runtime action. clip_get_curves lists binding_id/kind/path/property/type. Supply target as the exact animation root to resolve each curve's animated object against the current hierarchy. clip_get_curve returns paged full float keys (tangents, weights, tangent modes) or exact asset object-reference keys {guid,file_id}. clip_replace_curve replaces one existing binding with 1-1024 ordered keys; clip_delete_curve removes that binding. Both require binding_id and expected_revision. clip_file_id selects an exact clip subasset; imported clips are read-only. controller_get_graph pages nested state machines, states and transition identities. controller_modify_state/delete_state require state_id; controller_modify_transition/delete_transition require transition_id. Writes require expected_revision and modify uses changes. State changes support name/speed/exact motion refs; transition changes support duration/exit_time/offset/flags, conditions and exact destination_state_id. State deletion refuses default or referenced states. Pages use offset/page_size (1-100), with expected_revision required after offset zero. New actions are bounded to contained Assets sources, support native Undo and preserve GUID/file IDs; no arbitrary callback/filesystem transaction is promised.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | Action to perform (prefix: animator_, controller_, clip_). |
| `target` | `str \| None` | — | Target GameObject (name/path/id). |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer'] \| None` | — | How to find the target GameObject. |
| `clip_path` | `str \| None` | — | Asset path for AnimationClip (e.g. 'Assets/Animations/Walk.anim'). |
| `controller_path` | `str \| None` | — | Asset path for AnimatorController (e.g. 'Assets/Animators/Player.controller'). |
| `properties` | `dict[str, Any] \| str \| None` | — | Action-specific parameters (dict or JSON object string). |
| `runtime_only` | `bool \| None` | — | For animator_set_parameter or animator_set_enabled, require a playing scene instance. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
List curve bindings on an existing walk animation. Replace the clip path, then choose a returned binding ID for `clip_get_curve`.

```json title="MCP arguments"
{
  "action": "clip_get_curves",
  "clip_path": "Assets/Animations/Walk.anim"
}
```
<!-- examples:end -->

