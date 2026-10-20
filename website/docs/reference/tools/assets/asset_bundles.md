---
title: asset_bundles
sidebar_label: asset_bundles
description: "Inspect asset bundles, their contents and dependencies."
---

# `asset_bundles`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `assets` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect asset bundles, their contents and dependencies.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_asset_bundles', 'get_bundle_assets', 'get_bundle_dependencies']` | yes | Feature action to perform. |
| `page_size` | `int \| str \| None` | — | Page size: search 1-1000 (default 50), previews 1-32 (default 32), bundle inspection 1-100 (default 50). |
| `page_number` | `int \| str \| None` | — | Page number for pagination (1-based). |
| `bundle_name` | `str \| None` | — | Registered bundle assignment name for get_bundle_* actions. |
| `recursive` | `bool` | — | Include indirect bundle dependencies (default false). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

