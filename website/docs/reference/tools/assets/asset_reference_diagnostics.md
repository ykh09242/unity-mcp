---
title: asset_reference_diagnostics
sidebar_label: asset_reference_diagnostics
description: "Inspect dependencies and reverse references, or preview asset moves and deletion."
---

# `asset_reference_diagnostics`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `assets` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect dependencies and reverse references, or preview asset moves and deletion. Read-only inspection; results can change with Unity state.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['get_dependencies', 'get_reverse_references', 'preview_move', 'preview_delete']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Asset path required for asset operations; search defaults to Assets. Ignored for bundle assignment inspection. |
| `destination` | `str \| None` | — | Target path for 'duplicate'/'move'. |
| `page_size` | `int \| str \| None` | — | Page size: search 1-1000 (default 50), previews 1-32 (default 32), bundle inspection 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | Page number for pagination (1-based). |
| `recursive` | `bool` | — | Include indirect bundle dependencies (default false). |
| `scan_root` | `str \| None` | — | Reference actions: candidate/dependency folder (Assets by default). |
| `max_assets` | `int \| str \| None` | — | Reference actions: at most 1-250000 candidate/visited assets; default 1024. |
| `max_work` | `int \| str \| None` | — | Reference actions: at most 1-4000000 node/edge work units; default 10000. |
| `reference_snapshot` | `bool \| None` | — | Reference reads/previews only: retain captured scalar pages for ten minutes idle. |
| `reference_cursor` | `str \| None` | — | Reference reads/previews only: continue a captured observation with identical scan options. Imported changes invalidate it. |
| `reference_revision` | `str \| None` | — | Recheck the same bounded read/preview or guard move/delete immediately before mutation. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

