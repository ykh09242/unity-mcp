---
title: manage_ugui
sidebar_label: manage_ugui
description: "Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings."
---

# `manage_ugui`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.tools.manage_ugui`

## Description

Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings. Read-only actions: ping, get_hierarchy, diagnose. Mutating actions: create, set_rect, set_layout, set_text, set_canvas. Optional uGUI and TextMeshPro support is detected in the Editor. Diagnostics evaluate bounded screen-size candidates and report limitations; they do not render screenshots.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'get_hierarchy', 'create', 'set_rect', 'set_layout', 'set_text', 'set_canvas', 'diagnose']` | yes | Action to perform. |
| `target` | `str \| int \| None` | — | GameObject name, hierarchy path, or instance ID. Required for get_hierarchy, setters and diagnose. |
| `parent` | `str \| int \| None` | — | Parent GameObject for create, resolved by name, hierarchy path, or instance ID. |
| `name` | `str \| None` | — | Name of the created GameObject. |
| `element_type` | `Literal['canvas', 'panel', 'image', 'button', 'text'] \| None` | — | Element to create: canvas, panel, image, button, or text. |
| `properties` | `dict[str, JsonValue] \| str \| None` | — | Action-specific camelCase properties, as a dict or JSON object string. Rect: anchorMin, anchorMax, pivot, anchoredPosition, sizeDelta, offsetMin, offsetMax, localScale, localEulerAngles. Create: all elements accept Rect properties; panel/image/button also accept color; text also accepts Text properties. Image raycastTarget requires manage_components after creation. Layout: type must be vertical, horizontal, grid, layout_element or content_size_fitter, plus that component's properties. Text: text, fontSize, color, alignment, enableAutoSizing, fontSizeMin, fontSizeMax, raycastTarget. Canvas: Canvas and CanvasScaler properties via set_canvas. |
| `include_inactive` | `bool` | — | Include inactive GameObjects in hierarchy and diagnostics. |
| `max_nodes` | `int` | — | Maximum hierarchy nodes to inspect, 1 to 1000. |
| `resolutions` | `list[dict[str, int]] \| str \| None` | — | For diagnose: 1 to 8 {width,height} objects, or a JSON array string. Integer dimensions 64 to 8192. Omit to use Editor diagnostic defaults. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Inspect a Canvas hierarchy, including inactive elements:

```json
{"action":"get_hierarchy","target":"MainCanvas","include_inactive":true,"max_nodes":200}
```

Create a Canvas and a TMP label (the label needs TextMeshPro and a default font asset):

```json
{"action":"create","element_type":"canvas","name":"MainCanvas"}
```

```json
{"action":"create","element_type":"text","parent":"MainCanvas","name":"Title","properties":{"text":"Main menu","fontSize":32,"sizeDelta":[480,64]}}
```

Set a RectTransform and configure a layout group:

```json
{"action":"set_rect","target":"MainCanvas/Title","properties":{"anchorMin":[0.5,1],"anchorMax":[0.5,1],"pivot":[0.5,1],"anchoredPosition":[0,-16],"sizeDelta":[480,64]}}
```

```json
{"action":"set_layout","target":"MainCanvas","properties":{"type":"vertical","spacing":12,"childControlHeight":false,"childForceExpandHeight":false}}
```

Change text and CanvasScaler settings:

```json
{"action":"set_text","target":"MainCanvas/Title","properties":{"text":"Choose a level","fontSize":28,"raycastTarget":false}}
```

```json
{"action":"set_canvas","target":"MainCanvas","properties":{"uiScaleMode":"ScaleWithScreenSize","referenceResolution":[1920,1080],"matchWidthOrHeight":0.5}}
```

Evaluate Screen Space Overlay layout candidates at two screen sizes:

```json
{"action":"diagnose","target":"MainCanvas","resolutions":[{"width":1920,"height":1080},{"width":1080,"height":1920}],"max_nodes":200}
```

Check report truncation and limitations. Findings are preview candidates, not screenshots or confirmed runtime input failures. See the [uGUI guide](../../../guides/ugui.md) for component properties, dependencies and diagnostic scope, and [Tool groups](../../../guides/tool-groups.md) for visibility.
<!-- examples:end -->

