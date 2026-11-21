---
title: inspect_editor_state
sidebar_label: inspect_editor_state
description: "Read the selected Editor snapshot including active scene and current compiling flag."
---

# `inspect_editor_state`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.editor_observations`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read the selected Editor snapshot including active scene and current compiling flag. Check staleness and field presence. Equivalent to mcpforunity://editor/state.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Read the Editor snapshot before a scene operation. Check the active scene, compile flag, and snapshot freshness before relying on them.

```json title="MCP arguments"
{}
```
<!-- examples:end -->

