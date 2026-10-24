---
title: manage_rendering_stats
sidebar_label: manage_rendering_stats
description: "Inspect rendering counters and memory, or select a scene debug view."
---

# `manage_rendering_stats`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect rendering counters and memory, or select a scene debug view.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['stats_get', 'stats_list_counters', 'stats_set_scene_debug', 'stats_get_memory']` | yes | Feature action to perform. |
| `mode` | `str \| None` | — | Probe mode or debug mode. |
| `category` | `str \| None` | — | Profiler counter category for stats_list_counters (default Render). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

