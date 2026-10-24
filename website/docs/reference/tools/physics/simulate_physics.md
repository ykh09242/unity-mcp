---
title: simulate_physics
sidebar_label: simulate_physics
description: "Advance physics by a bounded number of explicit simulation steps."
---

# `simulate_physics`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Advance physics by a bounded number of explicit simulation steps.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['simulate_step']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `target` | `str \| None` | — | Target GameObject name or instance ID. |
| `search_method` | `str \| None` | — | Search method for target resolution. |
| `steps` | `int \| None` | — | Number of simulation steps (max 100). |
| `step_size` | `float \| None` | — | Step size in seconds. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

