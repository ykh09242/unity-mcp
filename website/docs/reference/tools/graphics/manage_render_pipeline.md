---
title: manage_render_pipeline
sidebar_label: manage_render_pipeline
description: "Inspect the render pipeline or change quality and pipeline settings."
---

# `manage_render_pipeline`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `graphics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect the render pipeline or change quality and pipeline settings.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'pipeline_get_info', 'pipeline_get_diagnostics', 'pipeline_set_quality', 'pipeline_get_settings', 'pipeline_set_settings']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target object name or instance ID. |
| `settings` | `dict[str, Any] \| None` | — | Dict of settings (bake/pipeline). |
| `level` | `str \| None` | — | Quality level name or index. |
| `renderer_index` | `int \| None` | — | Pipeline renderer index; -1 or omitted uses its default. Distinct from feature index. Mutually exclusive with target. Alias: rendererIndex. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

