---
title: list_menu_declarations
sidebar_label: list_menu_declarations
description: "List managed MenuItem declarations in the selected Editor to find a command's exact menu path."
---

# `list_menu_declarations`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.list_menu_declarations`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

List managed MenuItem declarations in the selected Editor to find a command's exact menu path. Match the returned strings by name. Some native entries have no managed declaration. Reading the list does not evaluate availability or invoke commands. Equivalent to mcpforunity://menu-items.

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

