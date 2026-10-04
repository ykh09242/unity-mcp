---
id: ugui
slug: /guides/ugui
title: Canvas-based uGUI
sidebar_label: uGUI
description: Create and edit Canvas UI, inspect layout and text, and assess layout candidates across screen sizes.
---

# Canvas-based uGUI

[`manage_ugui`](../reference/tools/ui/manage_ugui.md) belongs to the optional `ui` group. Enable it through the Editor tool controls and inspect visibility with `manage_tools(action="list_groups")`; [Tool groups](./tool-groups.md) explains session and hosted-client behavior. Use [`manage_ui`](../reference/tools/ui/manage_ui.md) for UI Toolkit documents and stylesheets.

## Inspect before editing

```text
manage_ugui(action="ping")
manage_ugui(action="get_hierarchy", target="MainCanvas", include_inactive=True, max_nodes=200)
```

`ping` reports capabilities without waiting for compilation. Hierarchy queries require a target GameObject name, full hierarchy path, or instance ID. Prefer the returned instance ID when names are ambiguous. Results are bounded by `max_nodes` (1–1000, default 200); check truncation before treating them as complete.

Canvas and RectTransform inspection uses Unity's built-in types. Image, Button, CanvasScaler, layout components and legacy Text require uGUI types in the project. Text creation uses TextMeshProUGUI and requires an available TMP default font asset. Missing dependencies produce actionable errors; this tool does not import TMP resources or change package dependencies.

## Create a menu

Create a Canvas, then place elements beneath its RectTransform hierarchy:

```text
manage_ugui(action="create", element_type="canvas", name="MainCanvas")
manage_ugui(action="create", element_type="panel", parent="MainCanvas", name="Menu",
  properties={"anchorMin":[0,0], "anchorMax":[1,1], "offsetMin":[24,24], "offsetMax":[-24,-24], "color":[0.1,0.1,0.15,1]})
manage_ugui(action="create", element_type="text", parent="MainCanvas/Menu", name="Title",
  properties={"text":"Main menu", "fontSize":32, "sizeDelta":[480,64]})
```

Non-Canvas elements require a parent RectTransform under a Canvas. Creating a Canvas requires uGUI CanvasScaler and GraphicRaycaster support. The new Canvas uses Screen Space Overlay with a 1920×1080 reference resolution and a width/height match of 0.5. A button creates its UI components and requires an EventSystem and input module to receive input; configure application callbacks separately.

Creation and setter requests use a `properties` dictionary, also accepted as a JSON object string. Keys use Unity's camelCase names. Invalid requests are rejected before edits; successful mutations participate in Editor Undo and dirty-state handling. These edits affect the selected open scene or prefab stage and do not save the scene automatically.

## Set anchors, layout, text and scaling

```text
manage_ugui(action="set_rect", target="MainCanvas/Menu/Title",
  properties={"anchorMin":[0.5,1], "anchorMax":[0.5,1], "pivot":[0.5,1], "anchoredPosition":[0,-16], "sizeDelta":[480,64]})
manage_ugui(action="set_layout", target="MainCanvas/Menu",
  properties={"type":"vertical", "spacing":12, "padding":{"left":24,"right":24,"top":24,"bottom":24}, "childControlHeight":False, "childForceExpandHeight":False})
manage_ugui(action="set_text", target="MainCanvas/Menu/Title",
  properties={"text":"Choose a level", "fontSize":28, "color":[1,1,1,1], "raycastTarget":False})
manage_ugui(action="set_canvas", target="MainCanvas",
  properties={"uiScaleMode":"ScaleWithScreenSize", "referenceResolution":[1920,1080], "screenMatchMode":"MatchWidthOrHeight", "matchWidthOrHeight":0.5})
```

RectTransform keys include `anchorMin`, `anchorMax`, `pivot`, `anchoredPosition`, `sizeDelta`, `offsetMin`, `offsetMax`, `localScale`, and `localEulerAngles`. Vectors accept arrays or coordinate dictionaries. Use offsets together, or position/size fields together: a request cannot mix offsets with `anchoredPosition` or `sizeDelta`. Colors accept an RGBA array or dictionary with channels in 0–1.

`set_layout` selects a component with `properties.type`:

| Type | Properties |
| --- | --- |
| `vertical`, `horizontal` | `padding`, `spacing`, `childAlignment`, `childControlWidth`, `childControlHeight`, `childForceExpandWidth`, `childForceExpandHeight`, `childScaleWidth`, `childScaleHeight`, `reverseArrangement` |
| `grid` | `padding`, `childAlignment`, `cellSize`, `spacing`, `startCorner`, `startAxis`, `constraint`, `constraintCount` |
| `layout_element` | `ignoreLayout`, `minWidth`, `minHeight`, `preferredWidth`, `preferredHeight`, `flexibleWidth`, `flexibleHeight`, `layoutPriority` |
| `content_size_fitter` | `horizontalFit`, `verticalFit` |

Text properties include `text`, `fontSize`, `color`, `alignment`, `raycastTarget`, and TMP-specific `enableAutoSizing`, `fontSizeMin`, `fontSizeMax`. Existing legacy Text components accept their supported shared properties. Enum values use named strings and are case-insensitive; unsupported names are errors.

Canvas properties include `renderMode`, `sortingOrder`, `overrideSorting`, `pixelPerfect`, `worldCamera`, `planeDistance`, `scaleFactor`, and `referencePixelsPerUnit`. CanvasScaler properties include `uiScaleMode`, `referenceResolution`, `screenMatchMode`, and `matchWidthOrHeight`. `worldCamera` resolves a scene camera by GameObject name/path/ID or accepts null to clear the assignment.

Layout groups drive child RectTransforms. Inspect and edit the controlling layout component when a manual child size is overridden after rebuilding.

## Diagnose screen-size candidates

```text
manage_ugui(action="diagnose", target="MainCanvas", include_inactive=False, max_nodes=200,
  resolutions=[{"width":1920,"height":1080},{"width":1280,"height":720},{"width":1080,"height":1920}])
```

`target` must be a RectTransform under a Canvas. Supply 1–8 resolutions with integer dimensions from 64 to 8192, either as a list or JSON array string. Omit the list to use the current Canvas size, with a reference-size fallback when needed. The first implementation supports Screen Space Overlay and Constant Pixel Size or Scale With Screen Size. Screen Space Camera, World Space and Constant Physical Size return explicit unsupported results.

Diagnostics evaluate a sanitized, disposable layout preview using supported built-in layout components. They rebuild the preview rather than the original scene and report `evaluation="sanitized_layout_preview"`. Findings are candidates for overflow, clipping, overlap and blocked interaction, with severity, target IDs, paths and resolution context. `rects` contains evaluated and visible rectangles; per-resolution summaries and counts describe how much was evaluated. Inspect `truncated` and `limitations` in every report.

A candidate does not prove a rendered defect or a runtime input failure. Arbitrary scripts, custom layout controllers, material effects, transparent graphic pixels, runtime animation, camera projection and device-specific safe areas can change the result. Preview text/layout measurements also depend on available component and font support. Use an actual Game View capture and the application's interaction flow to confirm consequential findings. These diagnostics do not generate screenshots or modify the Game View resolution.

For instance selection, see [Multiple Unity instances](./multi-instance.md). For generic tool invocation, see the [CLI guide](./cli.md); a dedicated uGUI CLI command is not required.
