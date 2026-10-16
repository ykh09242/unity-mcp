---
title: export_test_job
sidebar_label: export_test_job
description: "Export completed Unity test evidence as JSON or JUnit."
---

# `export_test_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.export_test_job`

## Description

Export completed Unity test evidence as JSON or JUnit. Returns one bounded UTF-8 chunk; pass next_cursor to continue. No remote output paths.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | `str` | yes | Completed job id from run_tests |
| `format` | `Literal['json', 'junit']` | — | Export format |
| `cursor` | `str \| None` | — | Opaque next_cursor from the previous export chunk |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

