---
title: prefab_stage
sidebar_label: prefab_stage
description: "Open, save or close the Prefab Stage."
---

# `prefab_stage`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Open, save or close the Prefab Stage.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['open_prefab_stage', 'save_prefab_stage', 'close_prefab_stage']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

