---
title: asset_inspect
sidebar_label: asset_inspect
description: "Search assets or inspect their metadata and components."
---

# `asset_inspect`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Search assets or inspect their metadata and components.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['search', 'get_info', 'get_components']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Asset path required for asset operations; search defaults to Assets. Ignored for bundle assignment inspection. |
| `asset_type` | `str \| None` | — | Asset type (e.g., 'Material', 'Folder') - required for 'create'. Note: For ScriptableObjects, use manage_scriptable_object. |
| `generate_preview` | `bool` | — | Generate previews up to 256 pixels per edge and 256 KiB PNG each; search allows at most 32 results and 4 MiB aggregate base64. |
| `search_pattern` | `str \| None` | — | Search pattern (e.g., '*.prefab' or AssetDatabase filters like 't:MonoScript'). Recommended: put queries like 't:MonoScript' here and set path='Assets'. |
| `filter_type` | `str \| None` | — | Filter type for search |
| `filter_date_after` | `str \| None` | — | Date after which to filter |
| `page_size` | `int \| str \| None` | — | Page size: search 1-1000 (default 50), previews 1-32 (default 32), bundle inspection 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | Page number for pagination (1-based). |
| `metadata_only` | `bool` | — | Search only: return metadata with instanceID=0 and a bounded 60-second Editor-session snapshot; no explicit object/preview loads. Default false preserves legacy behavior. |
| `search_snapshot` | `str \| None` | — | Search only: opaque token from metadata_only=true; repeat the effective query and page_size. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

