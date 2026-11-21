---
title: manage_render_fog
sidebar_label: manage_render_fog
description: "Enable or configure scene fog."
---

# `manage_render_fog`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

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
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Enable linear fog in a test scene, fading between 25 and 100 world units. This changes the scene environment. Inspect its appearance in the Scene or Game view before saving.

```json title="MCP arguments"
{
  "action": "skybox_set_fog",
  "fog_enabled": true,
  "fog_mode": "Linear",
  "fog_color": [
    0.6,
    0.67,
    0.75,
    1
  ],
  "fog_start": 25,
  "fog_end": 100
}
```
<!-- examples:end -->

