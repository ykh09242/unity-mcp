---
title: scene_lifecycle
sidebar_label: scene_lifecycle
description: "Create, load, save or close scenes and organize loaded scene objects."
---

# `scene_lifecycle`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create, load, save or close scenes and organize loaded scene objects.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create', 'load', 'save', 'close_scene', 'set_active_scene', 'move_to_scene']` | yes | Feature action to perform. |
| `name` | `str \| None` | — | Scene name. |
| `path` | `str \| None` | — | Scene path, under Assets/ or Packages/. A bare path is treated as relative to Assets/. |
| `build_index` | `int \| str \| None` | — | Unity build index (quote as string, e.g., '0'). |
| `scene_name` | `str \| None` | — | Scene name for multi-scene operations. |
| `scene_path` | `str \| None` | — | Full scene path (e.g. 'Assets/Scenes/Level2.unity'). |
| `target` | `str \| int \| None` | — | GameObject reference (name, path, or instanceID) for move_to_scene. |
| `remove_scene` | `bool \| str \| None` | — | For close_scene: true to fully remove, false to just unload. |
| `additive` | `bool \| str \| None` | — | For load: true to open scene additively (keeps current scene). |
| `template` | `str \| None` | — | For create: scene template ('empty', 'default', '3d_basic', '2d_basic'). Omit for empty scene. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

