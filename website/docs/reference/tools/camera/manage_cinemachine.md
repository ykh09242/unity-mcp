---
title: manage_cinemachine
sidebar_label: manage_cinemachine
description: "Configure Cinemachine brains, tracking, extensions and camera blends."
---

# `manage_cinemachine`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `camera` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Configure Cinemachine brains, tracking, extensions and camera blends.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'ensure_brain', 'get_brain_status', 'set_body', 'set_aim', 'set_noise', 'add_extension', 'remove_extension', 'set_blend', 'force_camera', 'release_override']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target camera (name, path, or instance ID). |
| `search_method` | `Literal['by_id', 'by_name', 'by_path'] \| None` | — | How to find target. |
| `properties` | `dict[str, Any] \| str \| None` | — | Action-specific parameters (dict or JSON string). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

