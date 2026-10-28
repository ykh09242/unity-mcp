---
title: capture_prefab
sidebar_label: capture_prefab
description: "Render a prefab asset to PNG in an isolated preview scene, then clean up."
---

# `capture_prefab`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.tools.capture_prefab`

## Description

Render a prefab asset to PNG in an isolated preview scene, then clean up. Supports root RectTransform UI and front-view renderer bounds. Edit Mode and graphics required. Does not save the prefab, initialize gameplay, animate or switch locales. Edit-time callbacks may execute.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `prefab_path` | `str` | yes | Project-relative Assets/... or Packages/... .prefab path. |
| `width` | `int` | — |  |
| `height` | `int` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

