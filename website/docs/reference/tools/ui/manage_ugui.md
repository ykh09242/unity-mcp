---
title: manage_ugui
sidebar_label: manage_ugui
description: "Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings."
---

# `manage_ugui`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.tools.manage_ugui`

## Description

Inspect and edit Canvas-based uGUI hierarchies, RectTransforms, layout, text and CanvasScaler settings. Read-only actions: ping, get_hierarchy, diagnose, diagnose_projection, diagnose_text, diagnose_raycast. Mutating actions: create, set_rect, set_layout, set_text, set_canvas, set_image. Optional uGUI and TextMeshPro support is detected in the Editor. diagnose evaluates bounded Overlay screen-size candidates. diagnose_projection reads current mono camera geometry for Screen Camera/World Space without rendering or forcing layout; it is not rendered visibility. diagnose_text observes existing generated TMP layout only; dirty/uninitialized data is unknown, with no rebuild or hypothetical sizes. diagnose_raycast reports one current native hit order and separate module/handler facts, not event receipt. Custom filters are refused by default; explicit allow_custom_filters may run user code without rollback or a hard deadline. The package dispatches no pointer/selection events or explicit source edits.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'get_hierarchy', 'create', 'set_rect', 'set_layout', 'set_text', 'set_canvas', 'set_image', 'diagnose', 'diagnose_projection', 'diagnose_text', 'diagnose_raycast']` | yes | Action to perform. |
| `target` | `str \| int \| None` | — | GameObject name, hierarchy path, or instance ID. Required for get_hierarchy, setters and diagnose. |
| `parent` | `str \| int \| None` | — | Parent GameObject for create, resolved by name, hierarchy path, or instance ID. |
| `name` | `str \| None` | — | Name of the created GameObject. |
| `element_type` | `Literal['canvas', 'panel', 'image', 'button', 'text'] \| None` | — | Element to create: canvas, panel, image, button, or text. |
| `properties` | `dict[str, JsonValue] \| str \| None` | — | Action-specific camelCase properties, as a dict or JSON object string. Rect: anchorMin, anchorMax, pivot, anchoredPosition, sizeDelta, offsetMin, offsetMax, localScale, localEulerAngles. Create: all elements accept Rect properties; panel/image/button also accept color; text also accepts Text properties. set_image edits an existing Image: sprite=null/path/{path,name}, type, fillMethod, fillOrigin, fillAmount, fillClockwise, preserveAspect, fillCenter, color and raycastTarget. Fill fields require final Filled type; origin is validated against final fillMethod. Sliced requires a bordered base sprite. No importer edit, explicit import/refresh or component creation occurs. Layout: type must be vertical, horizontal, grid, layout_element or content_size_fitter, plus that component's properties. Text: text, fontSize, color, alignment, enableAutoSizing, fontSizeMin, fontSizeMax, raycastTarget. Canvas: Canvas and CanvasScaler properties via set_canvas. |
| `include_inactive` | `bool` | — | Include inactive GameObjects in hierarchy and diagnostics. |
| `max_nodes` | `int` | — | Maximum hierarchy nodes to inspect, 1 to 1000. |
| `camera` | `str \| int \| None` | — | For diagnose_projection: explicit view camera required for World Space; Screen Camera uses only its assigned camera. Current scene GameObject name/path/Int32 ID; no camera fallback. |
| `max_depth` | `int \| None` | — | For diagnose_projection or diagnose_text: physical transform depth; for diagnose_raycast: filter ancestry only. Default 10, range 0..24. |
| `max_evaluations` | `int \| None` | — | For diagnose_projection, diagnose_text or diagnose_raycast: shared precharged package work, default 20000, range 1..200000. Raycast includes selector/handler work; native callbacks have no hard deadline. |
| `max_characters` | `int \| None` | — | For diagnose_projection, diagnose_text or diagnose_raycast: shared output metadata UTF-16 budget, default 65536, range 1..262144. |
| `resolutions` | `list[dict[str, int]] \| str \| None` | — | For diagnose: 1 to 8 {width,height} objects, or a JSON array string. Integer dimensions 64 to 8192. Omit to use Editor diagnostic defaults. |
| `screen_position` | `list[float] \| None` | — | For diagnose_raycast: required [x,y] in current-screen pixels from the bottom-left; no camera/resolution edit. |
| `allow_custom_filters` | `bool` | — | For diagnose_raycast only: explicitly permit native ICanvasRaycastFilter user code. Default false refuses before applicable custom callbacks; opt-in is not transactional or interruptible. |

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

