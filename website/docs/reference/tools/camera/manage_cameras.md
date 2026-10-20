---
title: manage_cameras
sidebar_label: manage_cameras
description: "Create or list cameras, select targets and configure lenses or priority."
---

# `manage_cameras`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `camera` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create or list cameras, select targets and configure lenses or priority.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create_camera', 'list_cameras', 'set_target', 'set_priority', 'set_lens']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target camera (name, path, or instance ID). |
| `search_method` | `Literal['by_id', 'by_name', 'by_path'] \| None` | — | How to find target. |
| `properties` | `dict[str, Any] \| str \| None` | — | Action-specific parameters (dict or JSON string). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

