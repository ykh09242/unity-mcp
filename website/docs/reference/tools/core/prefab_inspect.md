---
title: prefab_inspect
sidebar_label: prefab_inspect
description: "Inspect prefab metadata or a bounded hierarchy page."
---

# `prefab_inspect`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect prefab metadata or a bounded hierarchy page. Inspection may load assets or invoke user-defined callbacks.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['get_info', 'get_hierarchy']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). |
| `page_size` | `int \| None` | — | Page size 1-500. get_hierarchy defaults to 50; list_overrides defaults to 100. |
| `cursor` | `str \| None` | — | get_hierarchy opaque cursor; keep identical page_size/max_depth. |
| `max_depth` | `int` | — | get_hierarchy inclusive depth scope, root=0; default 64, maximum 128. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

