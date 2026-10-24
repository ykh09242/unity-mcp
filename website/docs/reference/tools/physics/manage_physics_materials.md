---
title: manage_physics_materials
sidebar_label: manage_physics_materials
description: "Create, configure and assign physics materials."
---

# `manage_physics_materials`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create, configure and assign physics materials.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create_physics_material', 'configure_physics_material', 'assign_physics_material']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `name` | `str \| None` | — | Name for new physics material. |
| `path` | `str \| None` | — | Asset path for materials. |
| `dynamic_friction` | `float \| None` | — | Dynamic friction (0-1). |
| `static_friction` | `float \| None` | — | Static friction (0-1). |
| `bounciness` | `float \| None` | — | Bounciness (0-1). |
| `friction` | `float \| None` | — | Friction for 2D materials. |
| `friction_combine` | `str \| None` | — | Friction combine mode: Average, Minimum, Multiply, Maximum. |
| `bounce_combine` | `str \| None` | — | Bounce combine mode: Average, Minimum, Multiply, Maximum. |
| `material_path` | `str \| None` | — | Path to physics material asset for assign. |
| `target` | `str \| None` | — | Target GameObject name or instance ID. |
| `collider_type` | `str \| None` | — | Specific collider type to target. |
| `search_method` | `str \| None` | — | Search method for target resolution. |
| `properties` | `dict[str, Any] \| None` | — | Direct property dict for joints or materials. |
| `component_index` | `int \| None` | — | Zero-based index to select which component when multiple of the same type exist (e.g., multiple HingeJoints or BoxColliders). If omitted, targets the first instance. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

