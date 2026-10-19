---
title: manage_input_actions
sidebar_label: manage_input_actions
description: "Author optional Input System1.7+ .inputactions assets in idle Edit Mode. capabilities/read are bounded reads; read returns exact map/action/binding GUIDs and a raw source revision. create_asset requires an unoccupied Assets path and exis…"
---

# `manage_input_actions`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_input_actions`

## Description

Author optional Input System1.7+ .inputactions assets in idle Edit Mode. capabilities/read are bounded reads; read returns exact map/action/binding GUIDs and a raw source revision. create_asset requires an unoccupied Assets path and existing parent. add_map/add_action/add_binding/set_binding/remove_binding require expected_revision; exact GUID selectors avoid name ambiguity. Simple bindings only; existing composite/control-scheme data and PlayerInput asset linkage are preserved. Edits save/import immediately; configured importer wrapper generation may compile scripts. Conditional file-backed Undo/Redo is limited to32 edits in the current Editor domain, refuses external drift and does not survive domain reload. Asset creation is not Undoable. Does not simulate input, pair devices, enable actions or install packages.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'read', 'create_asset', 'add_map', 'add_action', 'add_binding', 'set_binding', 'remove_binding']` | yes |  |
| `path` | `str \| None` | — |  |
| `expected_revision` | `str \| None` | — |  |
| `map_id` | `str \| None` | — |  |
| `action_id` | `str \| None` | — |  |
| `binding_id` | `str \| None` | — |  |
| `name` | `str \| None` | — |  |
| `action_type` | `Literal['Button', 'Value', 'PassThrough'] \| None` | — |  |
| `expected_control_type` | `str \| None` | — |  |
| `binding_path` | `str \| None` | — |  |
| `groups` | `str \| None` | — |  |
| `interactions` | `str \| None` | — |  |
| `processors` | `str \| None` | — |  |
| `page_size` | `int \| None` | — |  |
| `cursor` | `int \| None` | — |  |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

