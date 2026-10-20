---
title: manage_render_environment
sidebar_label: manage_render_environment
description: "Inspect the environment or configure skybox, ambient light and sun."
---

# `manage_render_environment`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect the environment or configure skybox, ambient light and sun.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['skybox_get', 'skybox_set_material', 'skybox_set_properties', 'skybox_set_ambient', 'skybox_set_sun']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target object name or instance ID. |
| `parameters` | `dict[str, Any] \| None` | — | Dict of parameter values. |
| `properties` | `dict[str, Any] \| None` | — | Dict of properties to set. |
| `name` | `str \| None` | — | Name for created objects. |
| `path` | `str \| None` | — | Asset path for volume_create_profile. |
| `mode` | `str \| None` | — | Probe mode or debug mode. |
| `material` | `str \| None` | — | Material asset path for feature. |
| `color` | `list[float] \| None` | — | Color [r,g,b,a] for ambient/fog. |
| `intensity` | `float \| None` | — | Intensity value (ambient/reflection). |
| `ambient_mode` | `str \| None` | — | Ambient mode: Skybox, Trilight, Flat, Custom. |
| `equator_color` | `list[float] \| None` | — | Equator color [r,g,b,a] (Trilight mode). |
| `ground_color` | `list[float] \| None` | — | Ground color [r,g,b,a] (Trilight mode). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

