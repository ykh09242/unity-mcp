---
title: ui_element
sidebar_label: ui_element
description: "Change a visual element's text, classes, style or state."
---

# `ui_element`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Change a visual element's text, classes, style or state.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['modify_visual_element']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target GameObject name or path for attach_ui_document / get_visual_tree / render_ui. |
| `component_type` | `Literal['auto', 'ui_document', 'panel_renderer'] \| None` | — | UI component for attach/detach, get_visual_tree, modify_visual_element and render_ui. Default auto preserves an existing UIDocument, otherwise resolves PanelRenderer; new attachments prefer PanelRenderer when available and fall back to UIDocument. panel_renderer requires Unity 6.5+ API support. |
| `element_name` | `str \| None` | — | Visual element name. Legacy first match unless strict_name=true. Optional exact name guard with element_path or element_id. For modify_visual_element. |
| `element_path` | `str` | — | Current snapshot/content-container child-index path, e.g. '/0/1'; empty string selects root. Zero-based decimal indices, no leading zeros. No persistent identity or replacement detection. Mutually exclusive with element_id; no name fallback. Omit when unused; explicit null is invalid. |
| `element_id` | `str` | — | Exact current UI Toolkit snapshot node ID. Uses the snapshot's auto-selected UI component; use element_path for another component_type. Semantic path, not a persistent handle; same-path replacement is not detected. No name fallback. Omit when unused; null/empty invalid. |
| `strict_name` | `bool \| None` | — | Require exactly one element_name match over the UQuery root and physical control internals. Default false keeps legacy first match. Active initialized UI required; bounded scan may refuse. Explicit selectors take precedence; element_name then guards the selected name. |
| `text` | `str \| None` | — | New text content for Label/Button elements. For modify_visual_element. |
| `add_classes` | `list[str] \| None` | — | USS class names to add to the element. For modify_visual_element. |
| `remove_classes` | `list[str] \| None` | — | USS class names to remove from the element. For modify_visual_element. |
| `toggle_classes` | `list[str] \| None` | — | USS class names to toggle on the element. For modify_visual_element. |
| `style` | `dict[str, Any] \| None` | — | Inline styles for modify_visual_element. Legacy scalar values remain supported. Typed lengths: {'value': 50, 'unit': '%'} or {'keyword': 'auto'} where supported. Inspect data.style_results for every applied/rejected entry and final inline overrides. |
| `enabled` | `bool \| None` | — | Set element enabled/disabled state. For modify_visual_element. |
| `visible` | `bool \| None` | — | Set element visibility (display: flex/none). For modify_visual_element. |
| `tooltip` | `str \| None` | — | Set element tooltip text. For modify_visual_element. |
| `strict_style` | `bool` | — | Reject every requested mutation before application if any inline style is rejected. Only modify_visual_element; default false preserves lenient compatibility. Validation is not callback rollback. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

