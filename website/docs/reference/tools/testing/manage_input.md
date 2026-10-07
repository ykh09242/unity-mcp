---
title: manage_input
sidebar_label: manage_input
description: "Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycas…"
---

# `manage_input`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_input`

## Description

Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycast occlusion). key/mouse/touch require com.unity.inputsystem 1.7+ and Active Input Handling Input System or Both. press holds for 1..600 game input updates then releases; use release for an earlier release. touch_id 1..10 permits concurrent contacts across calls; move preserves the original release deadline. release_all removes only MCP virtual devices. A hard 30-second lease and play exit/reload cleanup prevent stuck input. Paused Play Mode accepts release_all only. Device-paired PlayerInput actions may require pairing the virtual devices. Legacy UnityEngine.Input and OS input are unsupported.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['status', 'ui_click', 'key', 'mouse', 'touch', 'release_all']` | yes | status, ui_click, key, mouse, touch or release_all |
| `target` | `int \| str \| None` | — | ui_click scene instance ID or exact root hierarchy path |
| `key` | `str \| None` | — | Input System Key enum name, for example A, Space or LeftShift |
| `state` | `Literal['press', 'release', 'move']` | — | press or release; mouse/touch also support move |
| `frames` | `int` | — | Hold for this many game Input System updates (dynamic or fixed) |
| `position` | `tuple[float, float] \| None` | — | Screen pixel x,y; required for mouse move and touch press/move |
| `button` | `Literal['left', 'right', 'middle']` | — | Mouse button |
| `touch_id` | `int` | — | Independent touch contact ID (1..10) |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Inspect capabilities in the selected Unity instance before simulating input:

```json
{"action":"status"}
```

Dispatch a direct uGUI click to an inspected scene instance ID in running Play Mode:

```json
{"action":"ui_click","target":-19340}
```

Hold an Input System key for three game input updates, then let the tool release it:

```json
{"action":"key","key":"Space","state":"press","frames":3}
```

The key example requires the optional Input System package and compatible handling mode. A direct UI click bypasses physical hit testing; inspect the application's response separately. Use `release_all` to release MCP virtual devices early or while paused.
<!-- examples:end -->

