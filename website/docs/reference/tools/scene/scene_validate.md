---
title: scene_validate
sidebar_label: scene_validate
description: "Validate the scene, with optional explicit repair."
---

# `scene_validate`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Validate the scene, with optional explicit repair.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['validate']` | yes | Feature action to perform. |
| `auto_repair` | `bool \| str \| None` | — | For validate: remove missing scripts after a complete bounded scan. Not undoable; cancellation before commit only; an admitted commit finishes its plan. |
| `max_milliseconds` | `int \| str \| None` | — | validate: cooperative time budget 1-1000ms, default 25; native calls cannot be interrupted. |
| `continuation` | `str \| None` | — | validate: opaque read-only continuation, valid for five minutes in the same unchanged Editor scene. |
| `max_objects` | `int \| str \| None` | — | validate: 1-10000 objects per slice, default 1000; validate_references: 1-250000, default 1024. |
| `scene_handle` | `int \| None` | — | validate/save: exact scene_handle from get_loaded_scenes. For save, all source selectors must match one loaded scene; omit them to save the active scene. |
| `refresh_if_dirty` | `bool` | — | Set false to skip optional asset refreshes before this command. True preserves the operation's existing refresh policy and permissions. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Check the selected scene without applying repairs. Follow a returned continuation before treating the bounded scan as complete.

```json title="MCP arguments"
{
  "action": "validate",
  "auto_repair": false,
  "max_objects": 100
}
```
<!-- examples:end -->

