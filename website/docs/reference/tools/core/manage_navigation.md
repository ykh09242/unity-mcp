---
title: manage_navigation
sidebar_label: manage_navigation
description: "Read and explicitly author navigation surfaces, agents, obstacles and links."
---

# `manage_navigation`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_navigation`

## Description

Read and explicitly author navigation surfaces, agents, obstacles and links. Use capabilities to detect the optional AI Navigation package/version. Scene operations require an explicit loaded scene_handle; targets are exact component or GameObject instance IDs. add creates one component, configure applies supported public properties with Undo. Bake requires an active surface using Children collection and returns a queued job; poll bake_status for native completion. Queued jobs can be cancelled; the synchronous native bake cannot be interrupted. No automatic global bake or project-setting changes. Path and sample queries use agent_type_id/area_mask and report scene attribution limits of Unity's global NavMesh. Save authored scenes explicitly.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'list', 'read', 'add', 'configure', 'bake', 'bake_status', 'cancel_bake', 'sample_position', 'calculate_path']` | yes | Explicit navigation action. |
| `scene_handle` | `int \| None` | — | Loaded scene handle; required for scene operations. |
| `kind` | `Literal['surface', 'agent', 'obstacle', 'link'] \| None` | — | Component kind. |
| `target` | `int \| None` | — | Exact component or scene GameObject instance ID. |
| `properties` | `dict[str, JsonValue] \| None` | — | Supported component public properties; vectors are [x,y,z], enums use names. Surface: agentTypeID/collectObjects/useGeometry/layerMask/defaultArea/center/size, tile and voxel overrides. Agent: agentTypeID/areaMask/movement/avoidance. Obstacle: shape/center/size/radius/height/carving. Link: agentTypeID/area/startPoint/endPoint/width/bidirectional/costModifier/autoUpdate. |
| `job_id` | `str \| None` | — | Bake job ID for bake_status or cancel_bake. |
| `position` | `list[float] \| None` | — | World point for sample_position. |
| `start` | `list[float] \| None` | — | World start for calculate_path. |
| `end` | `list[float] \| None` | — | World end for calculate_path. |
| `agent_type_id` | `int \| None` | — | Configured NavMesh agent type; required for queries. |
| `area_mask` | `int \| None` | — | Query area bitmask; default all areas (-1). |
| `max_distance` | `float \| None` | — | Sample radius, greater than zero and at most 100. |
| `offset` | `int \| None` | — | List offset, nonnegative. |
| `limit` | `int \| None` | — | List page size 1..100 (default 50). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

