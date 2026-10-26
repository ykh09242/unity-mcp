---
title: execute_custom_tool
sidebar_label: execute_custom_tool
description: "Execute a project-scoped custom tool registered by Unity."
---

# `execute_custom_tool`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.execute_custom_tool`

## Description

Execute a project-scoped custom tool registered by Unity. Built-in tools and internal native aliases require their canonical public API.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `tool_name` | `str` | yes |  |
| `parameters` | `dict[str, Any] \| None` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

