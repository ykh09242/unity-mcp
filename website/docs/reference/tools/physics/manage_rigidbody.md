---
title: manage_rigidbody
sidebar_label: manage_rigidbody
description: "Inspect or configure a Rigidbody or Rigidbody2D."
---

# `manage_rigidbody`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `physics` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Inspect or configure a Rigidbody or Rigidbody2D.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['get_rigidbody', 'configure_rigidbody']` | yes | Feature action to perform. |
| `dimension` | `str \| None` | — | Physics dimension: '3d' (default) or '2d'. |
| `target` | `str \| None` | — | Target GameObject name or instance ID. |
| `search_method` | `str \| None` | — | Search method for target resolution. |
| `properties` | `dict[str, Any] \| None` | — | Direct property dict for joints or materials. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

