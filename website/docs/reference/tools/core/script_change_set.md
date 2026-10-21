---
title: script_change_set
sidebar_label: script_change_set
description: "Prepare, apply, inspect or recover 1–256 existing C# source replacements as one bounded change set."
---

# `script_change_set`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.script_change_set`

## Description

Prepare, apply, inspect or recover 1–256 existing C# source replacements as one bounded change set. Prepare takes final contents plus each original raw-byte SHA256 (including BOM), validates each file and retains a local recovery manifest without source writes. Apply checks all original source/metadata preconditions before writes. Failures retain ordered confirmed, restored or uncertain outcomes; recovery never overwrites a changed external source. This is not a filesystem-wide transaction, semantic rename or callback rollback. UTF-8 BOM/newlines and metadata are preserved. This request does not schedule a refresh; Unity automatic/deferred source import may still compile changed scripts.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['prepare', 'apply', 'status', 'recover']` | yes |  |
| `files` | `list[ScriptChangeFile] \| None` | — | prepare only: ordered final source buffers. |
| `change_set_id` | `str \| None` | — | apply/status/recover: identifier returned by prepare. |
| `validation` | `Literal['basic', 'standard', 'comprehensive', 'strict']` | — |  |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

