---
title: export_test_job
sidebar_label: export_test_job
description: "Export completed Unity test evidence as JSON or JUnit."
---

# `export_test_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.export_test_job`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Export completed Unity test evidence as JSON or JUnit. Returns one bounded UTF-8 chunk; pass next_cursor to continue. No remote output paths.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | `str` | yes | Completed job id from run_tests |
| `format` | `Literal['json', 'junit']` | — | Export format |
| `cursor` | `str \| None` | — | Opaque next_cursor from the previous export chunk |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Export JUnit evidence for a completed `run_tests` job. Use its actual job ID, and pass each `next_cursor` back to collect the remaining chunks in order.

```json title="MCP arguments"
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "format": "junit"
}
```
<!-- examples:end -->

