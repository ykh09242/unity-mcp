---
title: manage_vfx
sidebar_label: manage_vfx
description: "Manage Unity VFX components (ParticleSystem, VisualEffect, LineRenderer, TrailRenderer)."
---

# `manage_vfx`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `vfx` &nbsp;·&nbsp; **Module:** `services.tools.manage_vfx`

## Description

Manage Unity VFX components (ParticleSystem, VisualEffect, LineRenderer, TrailRenderer). Action prefixes: particle_*, vfx_*, line_*, trail_*. Action-specific parameters go in `properties` (keys match ManageVFX.cs). vfx_get_exposed_properties reads bounded exposed schema/current values with exact parameter filter, page_size/page_number, max_curve_keys/max_gradient_keys/max_characters. Texture values are identities, curves/gradients are complete or explicitly unavailable under budgets; override state is unavailable. No simulation or writes.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes | Action to perform (prefix: particle_, vfx_, line_, trail_). |
| `target` | `str \| None` | — | Target GameObject (name/path/id). |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer'] \| None` | — | How to find the target GameObject. |
| `properties` | `dict[str, Any] \| str \| None` | — | Action-specific parameters (dict or JSON object string). |
| `component_index` | `int \| None` | — | Zero-based index to select which component when multiple of the same type exist (e.g., multiple ParticleSystems). If omitted, targets the first instance. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

