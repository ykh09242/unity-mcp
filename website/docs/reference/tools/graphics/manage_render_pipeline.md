---
title: manage_render_pipeline
sidebar_label: manage_render_pipeline
description: "Inspect the render pipeline or change quality and pipeline settings."
---

# `manage_render_pipeline`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect the render pipeline or change quality and pipeline settings.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'pipeline_get_info', 'pipeline_set_quality', 'pipeline_get_settings', 'pipeline_set_settings']` | yes | Feature action to perform. |
| `settings` | `dict[str, Any] \| None` | — | Dict of settings (bake/pipeline). |
| `level` | `str \| None` | — | Quality level name or index. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

