---
title: find_in_file
sidebar_label: find_in_file
description: "Searches a C# script with a regex pattern and returns line numbers and excerpts."
---

# `find_in_file`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.find_in_file`

## Description

Searches a C# script with a regex pattern and returns line numbers and excerpts.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `uri` | `str` | yes | The C# script URI or path under Assets/ (.cs or extensionless) |
| `pattern` | `str` | yes | The regex pattern to search for |
| `project_root` | `str \| None` | — | Optional project root path |
| `max_results` | `int` | — | Cap results to avoid huge payloads |
| `ignore_case` | `bool \| str \| None` | — | Case insensitive search |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

