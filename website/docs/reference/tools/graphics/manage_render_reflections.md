---
title: manage_render_reflections
sidebar_label: manage_render_reflections
description: "Configure environment reflection mode, intensity, bounces and resolution."
---

# `manage_render_reflections`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Configure environment reflection mode, intensity, bounces and resolution.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['skybox_set_reflection']` | yes | Feature action to perform. |
| `resolution` | `int \| None` | — | Probe resolution. |
| `mode` | `str \| None` | — | Probe mode or debug mode. |
| `intensity` | `float \| None` | — | Intensity value (ambient/reflection). |
| `bounces` | `int \| None` | — | Reflection bounces. |
| `reflection_mode` | `str \| None` | — | Default reflection mode: Skybox, Custom. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

