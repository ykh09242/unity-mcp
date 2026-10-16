---
title: get_compile_job
sidebar_label: get_compile_job
description: "Read a compilation request outcome and bounded diagnostics by compile_id."
---

# `get_compile_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.get_compile_job`

## Description

Read a compilation request outcome and bounded diagnostics by compile_id. Readiness is not compiler success; unknown means evidence or attribution is incomplete.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `compile_id` | `str` | yes | Opaque compile_id returned by refresh_unity with compile=request |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

