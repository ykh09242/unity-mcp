---
title: prefab_overrides
sidebar_label: prefab_overrides
description: "List prefab overrides, trace a serialized property through variants, or apply or revert selected overrides."
---

# `prefab_overrides`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `prefabs` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

List prefab overrides, trace a serialized property through variants, or apply or revert selected overrides.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['list_overrides', 'trace_property', 'revert_overrides', 'apply_overrides']` | yes | Feature action to perform. |
| `prefab_path` | `str \| None` | — | Prefab asset path (e.g., Assets/Prefabs/MyPrefab.prefab). For save/close, when supplied it must match the current Prefab Stage. |
| `target` | `str \| int \| None` | — | For create_from_gameobject: instance ID (integer or numeric string), unique name, or full hierarchy path (e.g. 'Parent/Child' or '/Parent/Child') in the active scene/current prefab stage. Stage names/paths take precedence. Numeric targets are IDs; ambiguous names/paths fail. For modify_contents: saved prefab object name or child path; omit to target the root. Use returned rootObjectName/rootObjectPath, which may differ from the source instance name. IDs do not apply to isolated prefab contents. |
| `override_ids` | `list[str] \| None` | — | Explicit overrideId values from list_overrides; 1-100 unique values. Required for revert_overrides/apply_overrides. Select object/component/property entries via their IDs; no implicit apply-all. |
| `object_id` | `int \| None` | — | list_overrides filter, or exact component ID on the trace_property target. |
| `property_filter` | `str \| None` | — | list_overrides: case-insensitive property-path substring filter. |
| `offset` | `int` | — | list_overrides offset, zero or greater. |
| `page_size` | `int \| None` | — | Page size 1-500. get_hierarchy defaults to 50; list_overrides defaults to 100. |
| `property_path` | `str \| None` | — | trace_property only: exact serialized leaf path. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
List the first 25 overrides on an instance in the scene. Replace `Player` with the scene instance you intend to inspect.

```json title="MCP arguments"
{
  "action": "list_overrides",
  "target": "Player",
  "page_size": 25
}
```
<!-- examples:end -->

