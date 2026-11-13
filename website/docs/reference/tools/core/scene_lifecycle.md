---
title: scene_lifecycle
sidebar_label: scene_lifecycle
description: "Create, load, save or close scenes and organize loaded scene objects."
---

# `scene_lifecycle`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Create, load, save or close scenes and organize loaded scene objects.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create', 'load', 'save', 'close_scene', 'set_active_scene', 'move_to_scene']` | yes | Feature action to perform. |
| `name` | `str \| None` | — | Scene name. For Save As, the destination name. |
| `path` | `str \| None` | — | Scene path under Assets/ or Packages/. For Save As, the destination under Assets/. A bare path is relative to Assets/. |
| `build_index` | `int \| str \| None` | — | Unity build index (quote as string, e.g., '0'). |
| `allow_overwrite` | `bool \| str \| None` | — | Allow Save As to replace an existing scene. |
| `scene_name` | `str \| None` | — | Loaded scene name. For save, selects the source scene. |
| `scene_path` | `str \| None` | — | Exact loaded scene path (e.g. 'Assets/Scenes/Level2.unity'). For save, selects the source scene. |
| `target` | `str \| int \| None` | — | GameObject reference (name, path, or instanceID) for move_to_scene. |
| `remove_scene` | `bool \| str \| None` | — | For close_scene: true to fully remove, false to just unload. |
| `additive` | `bool \| str \| None` | — | For load: true to open scene additively (keeps current scene). |
| `template` | `str \| None` | — | For create: scene template ('empty', 'default', '3d_basic', '2d_basic'). Omit for empty scene. |
| `verify` | `bool \| None` | — | load/save only: observe the exact affected scene with one100-object/10ms validation slice |
| `scene_handle` | `int \| None` | — | validate/save: exact scene_handle from get_loaded_scenes. For save, all source selectors must match one loaded scene; omit them to save the active scene. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

