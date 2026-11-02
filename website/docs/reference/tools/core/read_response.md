---
title: read_response
sidebar_label: read_response
description: "Reads one fixed 256 KiB base64 page of a retained oversized MCP response."
---

# `read_response`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.read_response`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Reads one fixed 256 KiB base64 page of a retained oversized MCP response. Use the returned next_offset until null, concatenate decoded bytes, verify sha256, then decode UTF-8 JSON. Handles expire after 10 minutes; release=true discards the result. Requires the original server, principal, project and legacy session. Never repeats an operation.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `response_id` | `str` | yes |  |
| `offset` | `int` | — |  |
| `release` | `bool` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

