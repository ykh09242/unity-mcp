---
title: read_unity_resource
sidebar_label: read_unity_resource
description: "Read the packaged Unity MCP guide on this connection with no arguments."
---

# `read_unity_resource`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.read_unity_resource`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read the packaged Unity MCP guide on this connection with no arguments. For another published observation, use its tool binding. Expand a template with observed values and its fixed_uri_variables. For evaluate_getters=true, use native resources/read.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `uri` | `str \| None` | — | Omit for the packaged guide. Supply a catalog observation URI and use its tool binding's fixed variable values. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

