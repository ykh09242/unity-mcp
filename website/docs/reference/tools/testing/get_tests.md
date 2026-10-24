---
title: get_tests
sidebar_label: get_tests
description: "Lists Unity tests without running them."
---

# `get_tests`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.get_tests`

## Description

Lists Unity tests without running them. Returns one page with totalCount, nextCursor, hasMore and partial; pass nextCursor as cursor to read further pages. Omit mode to discover both EditMode and PlayMode tests.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `mode` | `Literal['EditMode', 'PlayMode'] \| None` | — | Filter by test mode; omit to include both modes. |
| `name_pattern` | `str \| None` | — | Case-insensitive substring of the test name or full name (not regex). |
| `page_size` | `int` | — | Maximum tests per page (1..200). |
| `cursor` | `int` | — | Zero-based offset; use the previous nextCursor. |
| `timeout_seconds` | `int` | — | Total discovery observation budget in seconds, including both modes (default 300). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

