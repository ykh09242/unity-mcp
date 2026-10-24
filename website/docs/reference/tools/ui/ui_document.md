---
title: ui_document
sidebar_label: ui_document
description: "Attach UI documents and configure Panel Settings."
---

# `ui_document`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Attach UI documents and configure Panel Settings.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['attach_ui_document', 'detach_ui_document', 'create_panel_settings', 'update_panel_settings']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Assets-relative path (e.g., 'Assets/UI/MainMenu.uxml' or 'Assets/UI/Styles.uss'). For render_ui: optional UXML path to render directly without a scene GameObject. |
| `target` | `str \| None` | — | Target GameObject name or path for attach_ui_document / get_visual_tree / render_ui. |
| `component_type` | `Literal['auto', 'ui_document', 'panel_renderer'] \| None` | — | UI component for attach/detach, get_visual_tree, modify_visual_element and render_ui. Default auto preserves an existing UIDocument, otherwise resolves PanelRenderer; new attachments prefer PanelRenderer when available and fall back to UIDocument. panel_renderer requires Unity 6.5+ API support. |
| `source_asset` | `str \| None` | — | Path to UXML VisualTreeAsset (e.g., 'Assets/UI/MainMenu.uxml'). |
| `panel_settings` | `str \| None` | — | Path to PanelSettings asset. Auto-creates default if omitted. |
| `sort_order` | `int \| None` | — | UI component sort order (default 0). |
| `scale_mode` | `Literal['ConstantPixelSize', 'ConstantPhysicalSize', 'ScaleWithScreenSize'] \| None` | — | Panel scale mode. Legacy shorthand; prefer using 'settings' dict. |
| `reference_resolution` | `dict[str, int] \| None` | — | Reference resolution as {width, height}. Legacy shorthand; prefer using 'settings' dict. |
| `settings` | `dict[str, Any] \| None` | — | Generic PanelSettings properties dict for create_panel_settings. Keys: scaleMode (ConstantPixelSize\|ConstantPhysicalSize\|ScaleWithScreenSize), referenceResolution ({width,height}), screenMatchMode (MatchWidthOrHeight\|ShrinkToFit\|ExpandToFill), match (0-1 float), referenceDpi, fallbackDpi, sortingOrder, targetDisplay, clearColor (bool), colorClearValue (#RRGGBB or {r,g,b,a}), clearDepthStencil, themeStyleSheet (asset path), dynamicAtlasSettings ({minAtlasSize,maxAtlasSize,maxSubTextureSize,activeFilters}). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

