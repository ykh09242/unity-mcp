---
title: manage_render_volumes
sidebar_label: manage_render_volumes
description: "Create volumes and profiles, then inspect or configure post-processing effects."
---

# `manage_render_volumes`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create volumes and profiles, then inspect or configure post-processing effects.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['volume_create', 'volume_add_effect', 'volume_set_effect', 'volume_remove_effect', 'volume_get_info', 'volume_set_properties', 'volume_list_effects', 'volume_create_profile']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target object name or instance ID. |
| `effect` | `str \| None` | — | Effect type name (e.g., 'Bloom', 'Vignette'). |
| `parameters` | `dict[str, Any] \| None` | — | Dict of parameter values. |
| `properties` | `dict[str, Any] \| None` | — | Dict of properties to set. |
| `name` | `str \| None` | — | Name for created objects. |
| `is_global` | `bool \| None` | — | Whether Volume is global (default true). |
| `weight` | `float \| None` | — | Volume weight (0-1). |
| `priority` | `float \| None` | — | Volume priority. |
| `profile_path` | `str \| None` | — | Asset path for VolumeProfile. |
| `effects` | `list[dict[str, Any]] \| None` | — | Effect definitions for volume_create. |
| `path` | `str \| None` | — | Asset path for volume_create_profile. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

