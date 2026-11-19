---
title: inspect_ugui_hit_order
sidebar_label: inspect_ugui_hit_order
description: "Read ordered native uGUI hits for a scene RectTransform and current bottom-left screen pixel coordinates."
---

# `inspect_ugui_hit_order`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.tools.editor_observations`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read ordered native uGUI hits for a scene RectTransform and current bottom-left screen pixel coordinates. Refuses unsupported queries before dispatch; never sends input, refreshes layout or opts into project callbacks. Requires manage_ugui.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `target` | `str \| int` | yes | Scene RectTransform name, path or instance ID. |
| `screen_position` | `list[float]` | yes | Current Game View pixel coordinates [x,y] from bottom left. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

