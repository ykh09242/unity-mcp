---
title: view_test_results
sidebar_label: view_test_results
description: "Read one completed Unity test-result page (up to 100 failed/skipped details)."
---

# `view_test_results`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.view_test_results`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Read one completed Unity test-result page (up to 100 failed/skipped details). An Apps host may show a local filter and expandable details for this page only.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | `str` | yes |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
To open a local Unity test result, connect an MCP Apps host to the server and select the
Editor that ran the test. Use the `job_id` returned by `run_tests` and wait for
`get_test_job` to report completion. Then call `view_test_results` with that same ID.

```json
{"job_id": "<completed-job-id>"}
```

The host can render a card with a filter and expandable details for the returned page.
Text-only clients receive the same result data. To preview with sample data in a local
checkout, open `Server/tests/fixtures/test_results_host.html` in a browser, choose
`Server/src/services/test_results_view.html`, and click **Run controlled lifecycle cases**.
<!-- examples:end -->

