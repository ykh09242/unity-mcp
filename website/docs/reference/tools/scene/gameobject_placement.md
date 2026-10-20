---
title: gameobject_placement
sidebar_label: gameobject_placement
description: "Preview or apply alignment and distribution of selected GameObjects."
---

# `gameobject_placement`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Preview or apply alignment and distribution of selected GameObjects.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['placement']` | yes | Feature action to perform. |
| `space` | `Literal['local', 'world'] \| None` | — | Position/rotation space; local default. Scale is local only. |
| `targets` | `list[str] \| None` | — | Placement: 2–128 unique selectors in explicit anchor/endpoint order. |
| `placement_mode` | `Literal['align', 'distribute'] \| None` | — | Placement along one axis; align uses first target. |
| `axis` | `Literal['x', 'y', 'z'] \| None` | — | Placement axis; x default. |
| `preview` | `bool \| str \| None` | — | Placement preview defaults true; false applies a validated preview token. |
| `preview_token` | `str \| None` | — | Placement apply: single-use native preview token, expires after five minutes. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

