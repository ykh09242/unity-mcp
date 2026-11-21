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

Read ordered native uGUI hits for a scene RectTransform and current bottom-left screen pixel coordinates. Requires manage_ugui, compatible official uGUI APIs and supported registered components. Reports unknown when safety checks fail. Sends no input, requests no layout refresh and never opts into project callbacks.

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
Inspect the ordered uGUI hits over a Start button using Game view coordinates. Replace the RectTransform and bottom-left pixel position with the target and current coordinates in your project. This inspection sends no pointer input.

```json title="MCP arguments"
{
  "target": "Canvas/Start",
  "screen_position": [
    320,
    180
  ]
}
```
<!-- examples:end -->

