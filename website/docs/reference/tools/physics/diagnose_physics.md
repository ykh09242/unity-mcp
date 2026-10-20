---
title: diagnose_physics
sidebar_label: diagnose_physics
description: "Validate physics setup or inspect a pair of colliders and passive contacts."
---

# `diagnose_physics`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Validate physics setup or inspect a pair of colliders and passive contacts.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['validate', 'diagnose_pair']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `target` | `str \| None` | — | Target GameObject name or instance ID. |
| `collider_type` | `str \| None` | — | Specific collider type to target. |
| `search_method` | `str \| None` | — | Search method for target resolution. |
| `page_size` | `int \| None` | — | Page size for validate results: 1-1000 (default 50). |
| `cursor` | `int \| None` | — | Cursor offset for validate pagination. |
| `component_index` | `int \| None` | — | Zero-based index to select which component when multiple of the same type exist (e.g., multiple HingeJoints or BoxColliders). If omitted, targets the first instance. |
| `other_target` | `str \| None` | — | diagnose_pair: second GameObject name, path or instance ID. |
| `other_collider_type` | `str \| None` | — | diagnose_pair: exact second collider type when needed. |
| `other_component_index` | `int \| None` | — | diagnose_pair: zero-based second collider index after type filtering. |
| `other_search_method` | `Literal['by_id', 'by_name', 'by_path'] \| None` | — | How to resolve other_target; inferred independently when omitted. |
| `contact_limit` | `int \| None` | — | diagnose_pair: 2D contact-point sample size, 1–128 (default16); 3D reports event counts. |
| `observe_contacts` | `bool \| str \| None` | — | diagnose_pair: opt in to a60-second passive3D pair watch; false stops, omitted only reads. Never simulates or enables contact generation. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

