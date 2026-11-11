---
title: execute_menu_item
sidebar_label: execute_menu_item
description: "Inspect menu declarations or invoke a menu by exact path. inspect/exists never evaluate validation callbacks."
---

# `execute_menu_item`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.execute_menu_item`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect menu declarations or invoke a menu by exact path. inspect/exists never evaluate validation callbacks. Opt into evaluate_availability to call Menu.GetEnabled, which can run project validation callbacks and requires read, edit and run grants plus Editor consent. Default execution reports invocation only; arbitrary completion and modal handling are not guaranteed. Only Window/General/Console supports console_window_open observation. CONTEXT execution is unsupported and File/Quit is blocked. Inspection and observation require native support; unsupported peers refuse without falling back to execution.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `menu_path` | `str \| None` | — | Exact menu path, maximum 1024 UTF-16 characters/4096 UTF-8 bytes; no NUL/newlines. |
| `action` | `Literal['execute', 'inspect', 'exists', 'evaluate_availability'] \| None` | — | Default execute; inspect/exists reads declarations only; evaluate_availability opts into validation callbacks. |
| `wait_for` | `Literal['none', 'console_window_open'] \| None` | — | Optional allowlisted window-presence observation, not general menu completion. |
| `timeout_ms` | `int \| None` | — | Observation ceiling, default 300000ms; permitted only with a supported wait_for. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

