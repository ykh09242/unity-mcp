---
title: ui_source
sidebar_label: ui_source
description: "Create, read or edit UXML/USS assets, link stylesheets or preview source patches."
---

# `ui_source`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Create, read or edit UXML/USS assets, link stylesheets or preview source patches.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['create', 'read', 'update', 'delete', 'link_stylesheet', 'list', 'patch_source']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Assets-relative path (e.g., 'Assets/UI/MainMenu.uxml' or 'Assets/UI/Styles.uss'). For render_ui: optional UXML path to render directly without a scene GameObject. |
| `contents` | `str \| None` | — | File content (UXML or USS markup). Plain text - encoding handled automatically. |
| `source_selector` | `str \| None` | — | patch_source: unique UXML #name, or exact simple USS type/.class/#name rule. |
| `source_changes` | `dict[str, str] \| None` | — | patch_source: 1..32 existing UXML text/tooltip/class attributes or USS declarations; values <=4096 characters. No insertion/removal or template ownership. |
| `preview` | `bool` | — | patch_source: true (default) returns SHA256 and exact value edits without writing. False persists source and reimports; live view refresh is separate. |
| `expected_source_hash` | `str \| None` | — | patch_source apply requires source_hash from preview. Detects changed source before write. UTF8 files <=1 MiB; preserves BOM, surrounding bytes and .meta. |
| `stylesheet` | `str \| None` | — | Path to USS stylesheet to link (e.g., 'Assets/UI/Styles.uss'). For link_stylesheet. |
| `filter_type` | `str \| None` | — | Filter UI assets by type: 'uxml', 'uss', 'PanelSettings', or omit for all. For list. |
| `page_size` | `int \| None` | — | Number of results per page: 1-1000 (default 50). For list. |
| `page_number` | `int \| None` | — | Page number, 1-based (default 1). For list. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

