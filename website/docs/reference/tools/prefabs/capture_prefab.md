---
title: capture_prefab
sidebar_label: capture_prefab
description: "Render a prefab asset to PNG in an isolated preview scene, then clean up."
---

# `capture_prefab`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.tools.capture_prefab`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Render a prefab asset to PNG in an isolated preview scene, then clean up. Supports root RectTransform UI and front-view renderer bounds. Edit Mode and graphics required. For UI without its own Canvas, pass ui_parent from find_gameobjects to use that scene parent's Canvas scaling policy at the requested resolution and a static parent layout snapshot. Returns ui_context provenance and limitations alongside the image; no parent means authored Canvas or a warned 1x fallback. Does not save the prefab, initialize gameplay, animate or switch locales. Edit-time callbacks may execute.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `prefab_path` | `str` | yes | Project-relative Assets/... or Packages/... .prefab path. |
| `width` | `int` | — |  |
| `height` | `int` | — |  |
| `ui_parent` | `int \| None` | — | Optional nonzero GameObject instance ID of an active scene Canvas or RectTransform under a screen-space Canvas. Overrides the prefab Canvas policy. Parent scripts, siblings and dynamic layout/Safe Area logic are not executed. Resolve in the same Unity instance. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Resolve the intended scene parent with `find_gameobjects` in the same Unity
instance, then use its **GameObject instance ID**:

```json
{
  "prefab_path": "Assets/UI/ConfirmPopup.prefab",
  "width": 1080,
  "height": 1920,
  "ui_parent": 12345
}
```

`12345` is a placeholder; refresh the ID after objects are recreated or the
Editor restarts. The parent must be an active scene Canvas or RectTransform
under an enabled screen-space Canvas. Its scaling policy overrides the prefab's
CanvasScaler and is recalculated for the requested resolution.

Inspect `data.ui_context` and `limitations` with the image. Parent layout is a
RectTransform snapshot; parent scripts, siblings and dynamic Safe Area/layout
processing are omitted. Without a parent, the tool uses an authored root Canvas
or reports a 1x fallback. This is a full-viewport static preview; button actions
and gameplay initialization require a running Game View.
<!-- examples:end -->

