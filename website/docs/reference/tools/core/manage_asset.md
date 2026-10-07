---
title: manage_asset
sidebar_label: manage_asset
description: "Performs asset operations (import, create, modify, delete, etc.) in Unity."
---

# `manage_asset`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_asset`

## Description

Performs asset operations (import, create, modify, delete, etc.) in Unity. Read-only list_asset_bundles/get_bundle_assets/get_bundle_dependencies inspect AssetDatabase assignments, not built bundle files; use bundle_name and pages of at most 100.

Tip (payload safety): for `action="search"`, prefer paging (`page_size`, `page_number`) and keep `generate_preview=false` (previews can add large base64 blobs).

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['import', 'create', 'modify', 'delete', 'duplicate', 'move', 'rename', 'search', 'get_info', 'create_folder', 'get_components', 'list_asset_bundles', 'get_bundle_assets', 'get_bundle_dependencies']` | yes | Perform CRUD operations on assets. |
| `path` | `str \| None` | — | Asset path required for asset operations; search defaults to Assets. Ignored for bundle assignment inspection. |
| `asset_type` | `str \| None` | — | Asset type (e.g., 'Material', 'Folder') - required for 'create'. Note: For ScriptableObjects, use manage_scriptable_object. |
| `properties` | `dict[str, Any] \| str \| None` | — | Dictionary of properties for 'create'/'modify'. Keys are property names, values are property values. |
| `destination` | `str \| None` | — | Target path for 'duplicate'/'move'. |
| `generate_preview` | `bool` | — | Generate previews up to 256 pixels per edge and 256 KiB PNG each; search allows at most 32 results and 4 MiB aggregate base64. |
| `search_pattern` | `str \| None` | — | Search pattern (e.g., '*.prefab' or AssetDatabase filters like 't:MonoScript'). Recommended: put queries like 't:MonoScript' here and set path='Assets'. |
| `filter_type` | `str \| None` | — | Filter type for search |
| `filter_date_after` | `str \| None` | — | Date after which to filter |
| `page_size` | `int \| str \| None` | — | Page size: search 1-1000 (default 50), previews 1-32 (default 32), bundle inspection 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | Page number for pagination (1-based). |
| `bundle_name` | `str \| None` | — | Registered bundle assignment name for get_bundle_* actions. |
| `recursive` | `bool` | — | Include indirect bundle dependencies (default false). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
List AssetDatabase bundle assignments:

```json
{"action":"list_asset_bundles","page_size":50,"page_number":1}
```

Use an exact returned bundle name to inspect assigned assets or dependencies:

```json
{"action":"get_bundle_assets","bundle_name":"environment","page_size":50}
```

```json
{"action":"get_bundle_dependencies","bundle_name":"environment","recursive":true}
```

These actions do not load or extract built bundle files. Other asset actions still
require their target path; only search supplies `Assets` when the path is omitted.
<!-- examples:end -->

