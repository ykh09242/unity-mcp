---
title: manage_asset
sidebar_label: manage_asset
description: "Performs asset operations (import, create, modify, delete, etc.) in Unity."
---

# `manage_asset`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_asset`

## Description

Performs asset operations (import, create, modify, delete, etc.) in Unity. Read-only get_dependencies/get_reverse_references/preview_move/preview_delete use bounded AssetDatabase references, explicit scan_root, work/assets budgets and pages up to 100. reference_revision rechecks observations and optionally guards move/delete using matching scan options. String/custom-loader/Addressables references are not comprehensively discovered. Read-only list_asset_bundles/get_bundle_assets/get_bundle_dependencies inspect AssetDatabase assignments, not built bundle files; use bundle_name and pages of at most 100.

Tip (payload safety): for `action="search"`, prefer paging (`page_size`, `page_number`) and keep `generate_preview=false` (previews can add large base64 blobs). Opt-in metadata_only search avoids explicit object/preview loads and returns a short-lived search_snapshot for reusable pages; keep query, scope, date and page_size unchanged. Label actions get/set/add/remove support 1-32 targets; mutations require observed revisions, are sequential rather than atomic, and never automatically retry an uncertain write.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['import', 'create', 'modify', 'delete', 'duplicate', 'move', 'rename', 'search', 'get_info', 'create_folder', 'get_components', 'list_asset_bundles', 'get_bundle_assets', 'get_bundle_dependencies', 'get_dependencies', 'get_reverse_references', 'preview_move', 'preview_delete', 'get_labels', 'set_labels', 'add_labels', 'remove_labels']` | yes | Perform CRUD operations on assets. |
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
| `metadata_only` | `bool` | — | Search only: return metadata with instanceID=0 and a bounded 60-second Editor-session snapshot; no explicit object/preview loads. Default false preserves legacy behavior. |
| `search_snapshot` | `str \| None` | — | Search only: opaque token from metadata_only=true; repeat the effective query and page_size. |
| `labels` | `list[str] \| None` | — | Exact labels; set replaces, empty set clears. |
| `label_targets` | `list[AssetLabelTarget] \| None` | — | Common-operation batch; each mutation row requires revision. |
| `label_revision` | `str \| None` | — | Opaque revision from get_labels for a single-target mutation. |
| `scan_root` | `str \| None` | — | Reference actions: candidate/dependency folder (Assets by default). |
| `max_assets` | `int \| str \| None` | — | Reference actions: at most 1-4096 candidate/visited assets; default 1024. |
| `max_work` | `int \| str \| None` | — | Reference actions: at most 1-100000 node/edge work units; default 10000. |
| `reference_revision` | `str \| None` | — | Recheck the same bounded read/preview or guard move/delete immediately before mutation. |

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

