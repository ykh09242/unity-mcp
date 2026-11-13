---
title: find_in_file
sidebar_label: find_in_file
description: "Searches a C# script with a regex pattern and returns line numbers, excerpts and identities of the searched snapshot. bytes_sha256 identifies raw file bytes when supplied by the Editor; sha256 identifies decoded UTF-8 content."
---

# `find_in_file`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.find_in_file`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Searches a C# script with a regex pattern and returns line numbers, excerpts and identities of the searched snapshot. bytes_sha256 identifies raw file bytes when supplied by the Editor; sha256 identifies decoded UTF-8 content. Continue with next_cursor and the same search options; source or option changes reject the cursor. Pages follow regex engine order, including reverse and zero-width searches. Existing source, total-match and execution limits apply to every page.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `uri` | `str` | yes | The C# script URI or path under Assets/ (.cs or extensionless) |
| `pattern` | `str` | yes | The regex pattern to search for |
| `project_root` | `str \| None` | — | Optional project root path |
| `max_results` | `int` | — | Cap results to avoid huge payloads |
| `ignore_case` | `bool \| str \| None` | — | Case insensitive search |
| `cursor` | `str \| None` | — | Opaque next_cursor from the preceding page |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

