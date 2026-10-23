---
title: manage_tools
sidebar_label: manage_tools
description: "Manage visible groups or individual feature tools in this session."
---

# `manage_tools`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_tools`

## Description

Manage visible groups or individual feature tools in this session. Actions: list_groups (show all groups and their status), activate/deactivate (select exactly one group or tool), sync (refresh availability from Unity Editor's toggle states), reset (restore defaults). Activating a group makes its tools appear; deactivating hides them. Use sync after toggling tools in the Unity Editor GUI.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_groups', 'activate', 'deactivate', 'sync', 'reset']` | yes | Action to perform. |
| `group` | `str \| None` | — | Group name for activate/deactivate; select a group or a single tool. Valid groups: animation, asset_gen, assets, camera, core, docs, graphics, legacy, physics, prefabs, probuilder, profiling, scene, scripting_ext, testing, ui, vfx |
| `tool` | `str \| None` | — | Single tool name for activate/deactivate. Discover names with list_groups. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

