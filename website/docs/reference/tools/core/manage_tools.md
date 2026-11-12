---
title: manage_tools
sidebar_label: manage_tools
description: "Manage visible groups or individual feature tools in this session."
---

# `manage_tools`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_tools`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Manage visible groups or individual feature tools in this session. Actions: list_groups (show all groups and their status), activate/deactivate (select exactly one group or tool), sync (refresh availability from Unity Editor's toggle states), reset (restore configured profile), describe (full schema for one tool, optionally bind operation), catalog (complete group index, paged tools by group or actions by tool), search (bounded lexical catalog suggestions, without activation). Selection changes server tools/list; it does not confirm the client's callable inventory. Use sync after toggling tools in the Unity Editor GUI.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_groups', 'activate', 'deactivate', 'sync', 'reset', 'describe', 'search', 'catalog']` | yes | Action to perform. |
| `group` | `str \| None` | — | Group name for activate/deactivate/catalog; select a group or a single tool. Valid groups: animation, asset_gen, assets, camera, core, docs, graphics, legacy, physics, prefabs, probuilder, profiling, scene, scripting_ext, testing, ui, vfx |
| `tool` | `str \| None` | — | Single tool name for activate/deactivate/describe/catalog. Discover names with catalog. |
| `query` | `str \| None` | — | Original task query or exact identifier for search (1..512 characters). |
| `limit` | `int` | — | Maximum search or catalog page entries (1..20). |
| `include_schema` | `bool` | — | On single-tool activation, attach its schema and readiness lookup. |
| `catalog_query` | `str \| None` | — | For search, translate the user's intent from any language into concise English catalog terms here. Preserve exact tool/API/package identifiers. Omit for an English query or exact identifier. The server performs lexical matching. |
| `offset` | `int` | — | Catalog page offset for a selected group or tool. |
| `operation` | `str \| None` | — | For describe, select one exact action from catalog. The returned full schema binds that action. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

