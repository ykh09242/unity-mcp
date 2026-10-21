---
title: manage_input
sidebar_label: manage_input
description: "Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycas…"
---

# `manage_input`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_input`

## Description

Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycast occlusion). key/mouse/touch/gamepad/wheel require com.unity.inputsystem 1.7+ and Input System or Both handling. gamepad control is a named stick, trigger or button; sticks use value [x,y] in -1..1, triggers use scalar value 0..1 and buttons omit value. Release omits value and returns neutral. wheel delta [x,y] in -10000..10000 is dispatched once, preserving held mouse buttons. player_status(target) reads actual PlayerInput action/device resolution. pair_player(target) adds only a neutral MCP-owned gamepad to one selected player; existing paired devices and control scheme remain. release_player(target) ends that owned device lease. press holds for 1..36000 game input updates then releases; use release for an earlier release. touch_id 1..10 permits concurrent contacts across calls; move preserves the original release deadline. release_all removes only MCP virtual devices. Active input updates renew a 30-second inactivity lease up to 1 hour; play exit/reload cleans devices. Pause also cleans pairing; release_player/release_all remain callable while paused. PlayerInput eligibility does not guarantee gameplay handling. Legacy UnityEngine.Input and OS input are unsupported.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['status', 'player_status', 'pair_player', 'release_player', 'ui_click', 'key', 'mouse', 'touch', 'gamepad', 'wheel', 'release_all']` | yes | Read status/player_status; explicitly pair/release a player or simulate bounded input |
| `target` | `int \| str \| None` | — | ui_click scene instance ID or exact root hierarchy path |
| `key` | `str \| None` | — | Input System Key enum name, for example A, Space or LeftShift |
| `state` | `Literal['press', 'release', 'move']` | — | press or release; mouse/touch also support move |
| `frames` | `int` | — | Hold for this many game Input System updates (dynamic or fixed) |
| `position` | `tuple[float, float] \| None` | — | Screen pixel x,y; required for mouse move and touch press/move |
| `button` | `Literal['left', 'right', 'middle']` | — | Mouse button |
| `touch_id` | `int` | — | Independent touch contact ID (1..10) |
| `control` | `Literal['leftStick', 'rightStick', 'leftTrigger', 'rightTrigger', 'buttonSouth', 'buttonNorth', 'buttonEast', 'buttonWest', 'leftShoulder', 'rightShoulder', 'leftStickPress', 'rightStickPress', 'start', 'select', 'dpadUp', 'dpadDown', 'dpadLeft', 'dpadRight'] \| None` | — | Named gamepad stick, trigger or button |
| `value` | `float \| tuple[float, float] \| None` | — | Stick [x,y] or trigger scalar; omit for buttons and release |
| `delta` | `tuple[float, float] \| None` | — | One-shot wheel delta [x,y] |
| `device` | `Literal['gamepad'] \| None` | — | Explicit pairing device; defaults to owned gamepad |

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

