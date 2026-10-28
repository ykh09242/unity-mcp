---
title: get_compile_job
sidebar_label: get_compile_job
description: "Read compilation status, bounded diagnostics and the last successful compile by compile_id."
---

# `get_compile_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.get_compile_job`

## Description

Read compilation status, bounded diagnostics and the last successful compile by compile_id. Unknown means incomplete evidence; readiness and source-revision proof are separate.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `compile_id` | `str` | yes | Opaque compile_id returned by refresh_unity with compile=request |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

