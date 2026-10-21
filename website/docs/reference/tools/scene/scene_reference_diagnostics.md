---
title: scene_reference_diagnostics
sidebar_label: scene_reference_diagnostics
description: "Inspect scene references within explicit work and result budgets."
---

# `scene_reference_diagnostics`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect scene references within explicit work and result budgets. Inspection may load assets or invoke user-defined callbacks.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['validate_references']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Scene path, under Assets/ or Packages/. A bare path is treated as relative to Assets/. |
| `page_size` | `int \| str \| None` | — | Page size for get_hierarchy paging. |
| `reference_scope` | `Literal['active_scene', 'loaded_scenes', 'prefab', 'scriptable_object'] \| None` | — | validate_references only; loaded scopes never open scenes. |
| `max_objects` | `int \| str \| None` | — | validate_references: maximum 1-250000 owners/objects, default 1024. |
| `max_work` | `int \| str \| None` | — | validate_references: maximum 1-4000000 traversal/property steps, default 100000. |
| `page_number` | `int \| str \| None` | — | validate_references: 1-based page, maximum page_size 100. |
| `reference_revision` | `str \| None` | — | validate_references: refuse if the repeated observation has drifted. |
| `reference_snapshot` | `bool \| None` | — | validate_references: capture stable paged evidence without rescanning later pages. |
| `reference_cursor` | `str \| None` | — | validate_references: returned snapshot cursor; captured evidence, not a live revision recheck. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

