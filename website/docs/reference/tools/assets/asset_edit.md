---
title: asset_edit
sidebar_label: asset_edit
description: "Create, import, modify, duplicate, move or delete assets and folders."
---

# `asset_edit`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `assets` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create, import, modify, duplicate, move or delete assets and folders.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['import', 'create', 'modify', 'delete', 'duplicate', 'move', 'rename', 'create_folder']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Asset path required for asset operations; search defaults to Assets. Ignored for bundle assignment inspection. |
| `asset_type` | `str \| None` | — | Asset type (e.g., 'Material', 'Folder') - required for 'create'. Note: For ScriptableObjects, use manage_scriptable_object. |
| `properties` | `dict[str, Any] \| str \| None` | — | Dictionary of properties for 'create'/'modify'. Keys are property names, values are property values. |
| `destination` | `str \| None` | — | Target path for 'duplicate'/'move'. |
| `page_size` | `int \| str \| None` | — | Page size: search 1-1000 (default 50), previews 1-32 (default 32), bundle inspection 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | Page number for pagination (1-based). |
| `recursive` | `bool` | — | Include indirect bundle dependencies (default false). |
| `scan_root` | `str \| None` | — | Reference actions: candidate/dependency folder (Assets by default). |
| `max_assets` | `int \| str \| None` | — | Reference actions: at most 1-250000 candidate/visited assets; default 1024. |
| `max_work` | `int \| str \| None` | — | Reference actions: at most 1-4000000 node/edge work units; default 10000. |
| `reference_revision` | `str \| None` | — | Recheck the same bounded read/preview or guard move/delete immediately before mutation. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

