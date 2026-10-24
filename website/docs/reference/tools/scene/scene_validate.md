---
title: scene_validate
sidebar_label: scene_validate
description: "Validate the scene, with optional explicit repair."
---

# `scene_validate`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Validate the scene, with optional explicit repair.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['validate']` | yes | Feature action to perform. |
| `auto_repair` | `bool \| str \| None` | — | For validate: true to auto-fix missing scripts (undoable). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

