---
title: gameobject_placement
sidebar_label: gameobject_placement
description: "Preview or apply alignment and distribution of selected GameObjects."
---

# `gameobject_placement`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scene` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

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
| `refresh_if_dirty` | `bool` | — | Set false to skip optional asset refreshes before this command. True preserves the operation's existing refresh policy and permissions. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Preview aligning three markers on the world X axis. Replace the names with three unique scene objects. The first target is the anchor. Review the returned positions before applying a placement.

```json title="MCP arguments"
{
  "action": "placement",
  "space": "world",
  "targets": [
    "Marker A",
    "Marker B",
    "Marker C"
  ],
  "placement_mode": "align",
  "axis": "x",
  "preview": true
}
```
<!-- examples:end -->

