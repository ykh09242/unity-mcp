---
title: manage_render_fog
sidebar_label: manage_render_fog
description: "Enable or configure scene fog."
---

# `manage_render_fog`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Enable or configure scene fog.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['skybox_set_fog']` | yes | Feature action to perform. |
| `mode` | `str \| None` | — | Probe mode or debug mode. |
| `color` | `list[float] \| None` | — | Color [r,g,b,a] for ambient/fog. |
| `fog_enabled` | `bool \| None` | — | Enable or disable fog. |
| `fog_mode` | `str \| None` | — | Fog mode: Linear, Exponential, ExponentialSquared. |
| `fog_color` | `list[float] \| None` | — | Fog color [r,g,b,a]. |
| `fog_density` | `float \| None` | — | Fog density (Exponential modes). |
| `fog_start` | `float \| None` | — | Fog start distance (Linear mode). |
| `fog_end` | `float \| None` | — | Fog end distance (Linear mode). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

