---
title: ui_capture
sidebar_label: ui_capture
description: "Start, poll or cancel a bounded UI Toolkit render capture."
---

# `ui_capture`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Start, poll or cancel a bounded UI Toolkit render capture.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['render_ui']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Assets-relative path (e.g., 'Assets/UI/MainMenu.uxml' or 'Assets/UI/Styles.uss'). For render_ui: optional UXML path to render directly without a scene GameObject. |
| `target` | `str \| None` | — | Target GameObject name or path for attach_ui_document / get_visual_tree / render_ui. |
| `component_type` | `Literal['auto', 'ui_document', 'panel_renderer'] \| None` | — | UI component for attach/detach, get_visual_tree, modify_visual_element and render_ui. Default auto preserves an existing UIDocument, otherwise resolves PanelRenderer; new attachments prefer PanelRenderer when available and fall back to UIDocument. panel_renderer requires Unity 6.5+ API support. |
| `capture_id` | `str \| None` | — | Deferred capture ID returned by render_ui. Supply this ID alone to poll the original request; optional target/output settings must match the start request. Omit to start a separate capture even with identical settings. Results expire after 30 seconds. |
| `cancel_capture` | `bool \| None` | — | Cancel the deferred capture owned by capture_id (default false). For render_ui. |
| `warmup_updates` | `int \| None` | — | Edit-mode panel readback delay in Editor updates, 0 to 4 (default 0). Positive values return capture_id for polling/cancellation; no repaint guarantee or automatic retries. Play mode requires 0 and already captures at end of frame. For render_ui. |
| `width` | `int \| None` | — | Render width in pixels, 1 to 8192 (default 1920). The frame may contain at most 33,554,432 pixels. For render_ui. |
| `height` | `int \| None` | — | Render height in pixels, 1 to 8192 (default 1080). For render_ui. |
| `include_image` | `bool \| None` | — | Return inline base64 PNG in the response (default false). For render_ui. |
| `max_resolution` | `int \| None` | — | Max resolution for inline base64 image, 1 to 8192 (default 640; 0 selects the default). For render_ui. |
| `screenshot_file_name` | `str \| None` | — | Custom file name for the render output (default: auto-generated). For render_ui. |
| `output_folder` | `str \| None` | — | Optional folder for the render output. Project-relative (e.g. 'Assets/Screenshots' or 'Captures') or absolute path inside the project. Overrides the user's Editor preference. If omitted, falls back to the Editor preference, then to the built-in default (Assets/Screenshots). For render_ui. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

