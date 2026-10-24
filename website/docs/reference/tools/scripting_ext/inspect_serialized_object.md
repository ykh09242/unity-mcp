---
title: inspect_serialized_object
sidebar_label: inspect_serialized_object
description: "Inspect one exact Component or ScriptableObject instance ID through SerializedObject."
---

# `inspect_serialized_object`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scripting_ext` &nbsp;·&nbsp; **Module:** `services.tools.inspect_serialized_object`

## Description

Inspect one exact Component or ScriptableObject instance ID through SerializedObject. The initial Unity ID lookup MAY load a previously unloaded asset and invoke OnEnable or serialization callbacks before type/context admission. No explicit asset search/import/save is performed. Fields returns one immediate level; value reads one canonical property; array reads only the requested slice. Use canonical paths such as groups.Array.data[2].values. Managed and object references remain terminal descriptors. Pages are live, not snapshots: restart after edits/reload; IDs, field paths and array indices are not persistent identity. Maximum 64 records, 16 path hops, 2048 charged managed operations and 65536 response bytes. These limits do not bound synchronous Unity serialization or native lookup complexity. Native editability is separate from setter-specific supported input shapes; enum names are recommended for component writes. Requires a compatible Editor; no legacy fallback or reload retry.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `instance_id` | `int` | yes |  |
| `mode` | `Literal['fields', 'value', 'array']` | — |  |
| `property_path` | `str` | — |  |
| `page_size` | `int` | — |  |
| `after_path` | `str` | — |  |
| `start_index` | `int` | — |  |
| `include_values` | `bool` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

