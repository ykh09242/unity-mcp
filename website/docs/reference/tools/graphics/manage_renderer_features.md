---
title: manage_renderer_features
sidebar_label: manage_renderer_features
description: "Add, configure, reorder or remove URP renderer features."
---

# `manage_renderer_features`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Add, configure, reorder or remove URP renderer features.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['feature_list', 'feature_add', 'feature_remove', 'feature_configure', 'feature_toggle', 'feature_reorder']` | yes | Feature action to perform. |
| `properties` | `dict[str, Any] \| None` | — | Dict of properties to set. |
| `settings` | `dict[str, Any] \| None` | — | Dict of settings (bake/pipeline). |
| `name` | `str \| None` | — | Name for created objects. |
| `index` | `int \| None` | — | Feature index. |
| `active` | `bool \| None` | — | Feature active state. |
| `order` | `list[int] \| None` | — | Feature reorder indices. |
| `feature_type` | `str \| None` | — | Renderer feature type name. |
| `material` | `str \| None` | — | Material asset path for feature. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

