---
title: get_game_view_size
sidebar_label: get_game_view_size
description: "Read the current Game View render size in pixels, selected preset and available presets from an existing window in the selected Unity Editor."
---

# `get_game_view_size`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.editor_observations`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read the current Game View render size in pixels, selected preset and available presets from an existing window in the selected Unity Editor. Use render_size.width and render_size.height for screen coordinates. Requires manage_editor.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Read the current Game view dimensions before choosing a screenshot size or comparing two captures.

```json title="MCP arguments"
{}
```
<!-- examples:end -->

