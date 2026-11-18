---
title: set_active_instance
sidebar_label: set_active_instance
description: "Select a Unity instance by Name@hash, hash prefix, or stdio port."
---

# `set_active_instance`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.set_active_instance`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Select a Unity instance by Name@hash, hash prefix, or stdio port. On sessionless clients, include_context returns a request-scoped observation.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `instance` | `str` | yes | Target instance (Name@hash, hash prefix, or port number in stdio mode) |
| `include_context` | `bool` | — | Attach a bounded read-only snapshot of the selected Editor. |
| `evidence_needed` | `list[str] \| None` | — | Caller-supplied checks still needed by the receiving agent. Supplying this includes context. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

