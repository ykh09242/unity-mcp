---
title: validate_script
sidebar_label: validate_script
description: "Validate a C# script and report diagnostics, the effective validation engine, and context limitations."
---

# `validate_script`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_script`

## Description

Validate a C# script and report diagnostics, the effective validation engine, and context limitations. This does not trigger Unity project compilation.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `uri` | `str` | yes | URI of the script to validate under Assets/ directory, mcpforunity://path/Assets/... or file://... or Assets/... |
| `level` | `Literal['basic', 'standard']` | — | basic checks structure; standard adds available compiler syntax and heuristic Unity rules. See returned validation capabilities. |
| `include_diagnostics` | `bool` | — | Include full diagnostics and summary |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

