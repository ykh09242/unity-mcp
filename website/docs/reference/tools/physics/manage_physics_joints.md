---
title: manage_physics_joints
sidebar_label: manage_physics_joints
description: "Add, configure or remove 2D/3D joints."
---

# `manage_physics_joints`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Add, configure or remove 2D/3D joints.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['add_joint', 'configure_joint', 'remove_joint']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `target` | `str \| None` | — | Target GameObject name or instance ID. |
| `search_method` | `str \| None` | — | Search method for target resolution. |
| `joint_type` | `str \| None` | — | Joint type: fixed, hinge, spring, character, configurable (3D); distance, fixed, friction, hinge, relative, slider, spring, target, wheel (2D). |
| `connected_body` | `str \| None` | — | Connected body target for joints. |
| `motor` | `dict[str, Any] \| None` | — | Motor config: {targetVelocity, force, freeSpin}. |
| `limits` | `dict[str, Any] \| None` | — | Limits config: {min, max, bounciness}. |
| `spring` | `dict[str, Any] \| None` | — | Spring config: {spring, damper, targetPosition}. |
| `drive` | `dict[str, Any] \| None` | — | Drive config for ConfigurableJoint. |
| `properties` | `dict[str, Any] \| None` | — | Direct property dict for joints or materials. |
| `component_index` | `int \| None` | — | Zero-based index to select which component when multiple of the same type exist (e.g., multiple HingeJoints or BoxColliders). If omitted, targets the first instance. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

