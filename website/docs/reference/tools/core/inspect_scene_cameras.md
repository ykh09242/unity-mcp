---
title: inspect_scene_cameras
sidebar_label: inspect_scene_cameras
description: "Read the selected Editor's scene camera inventory."
---

# `inspect_scene_cameras`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.inspect_scene_cameras`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read the selected Editor's scene camera inventory. Count entries and read names in unityCameras and cinemachineCameras. Includes brain state, lens information, Cinemachine priorities, pipeline components and follow/lookAt targets. With Cinemachine installed, reads provider methods and component getters. Equivalent to mcpforunity://scene/cameras.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

