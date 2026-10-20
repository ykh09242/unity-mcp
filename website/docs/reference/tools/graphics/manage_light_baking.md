---
title: manage_light_baking
sidebar_label: manage_light_baking
description: "Control light baking and configure light or reflection probes."
---

# `manage_light_baking`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Control light baking and configure light or reflection probes.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['bake_start', 'bake_cancel', 'bake_status', 'bake_clear', 'bake_reflection_probe', 'bake_get_settings', 'bake_set_settings', 'bake_create_light_probe_group', 'bake_create_reflection_probe', 'bake_set_probe_positions']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target object name or instance ID. |
| `settings` | `dict[str, Any] \| None` | — | Dict of settings (bake/pipeline). |
| `name` | `str \| None` | — | Name for created objects. |
| `position` | `list[float] \| None` | — | Position [x,y,z]. |
| `grid_size` | `list[int] \| None` | — | Probe grid size [x,y,z]. |
| `spacing` | `float \| None` | — | Probe grid spacing. |
| `size` | `list[float] \| None` | — | Probe/volume size [x,y,z]. |
| `resolution` | `int \| None` | — | Probe resolution. |
| `mode` | `str \| None` | — | Probe mode or debug mode. |
| `hdr` | `bool \| None` | — | HDR for reflection probes. |
| `box_projection` | `bool \| None` | — | Box projection for reflection probes. |
| `positions` | `list[list[float]] \| None` | — | Probe positions array. |
| `async_bake` | `bool \| None` | — | Async bake (default true). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

