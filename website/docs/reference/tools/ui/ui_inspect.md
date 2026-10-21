---
title: ui_inspect
sidebar_label: ui_inspect
description: "Inspect the visual tree or diagnose UI Toolkit layout and text."
---

# `ui_inspect`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect the visual tree or diagnose UI Toolkit layout and text. Inspection may load assets or invoke user-defined callbacks.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'get_visual_tree', 'diagnose']` | yes | Feature action to perform. |
| `target` | `str \| None` | — | Target GameObject name or path for attach_ui_document / get_visual_tree / render_ui. |
| `component_type` | `Literal['auto', 'ui_document', 'panel_renderer'] \| None` | — | UI component for attach/detach, get_visual_tree, modify_visual_element and render_ui. Default auto preserves an existing UIDocument, otherwise resolves PanelRenderer; new attachments prefer PanelRenderer when available and fall back to UIDocument. panel_renderer requires Unity 6.5+ API support. |
| `max_depth` | `int \| None` | — | get_visual_tree / diagnose inclusive depth: root is 0; default 10, maximum 24. |
| `max_nodes` | `int \| None` | — | get_visual_tree / diagnose emitted node budget, including root (default 200). |
| `max_evaluations` | `int \| None` | — | get_visual_tree operation budget: node admissions, child fetches and class probes; diagnose charges ancestors, admissions, child fetches, fixed style probes and pairs (default 5000). Not a time or scene-lookup bound. |
| `max_characters` | `int \| None` | — | get_visual_tree / diagnose shared copied UTF-16 string budget including metadata (default 65536). get_visual_tree per-string limit 8192/class limit 64; diagnose name/type limit 256 and whole physical paths. |
| `max_pairs` | `int \| None` | — | diagnose physical sibling pairs examined before filters (default 2000); zero disables overlap and yields partial evidence. |
| `max_findings` | `int \| None` | — | diagnose retained candidate findings (default 200); reaching the cap reports partial coverage. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

