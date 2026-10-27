---
title: get_test_job
sidebar_label: get_test_job
description: "Polls an async Unity test job by job_id."
---

# `get_test_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.run_tests`

## Description

Polls an async Unity test job by job_id.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | `str` | yes | Job id returned by run_tests |
| `include_failed_tests` | `bool` | — | Include details for failed/skipped tests only (default: false) |
| `include_details` | `bool` | — | Include details for all tests (default: false) |
| `wait_timeout` | `int \| None` | — | If set, wait up to this many seconds for tests to complete before returning. Reduces polling frequency and avoids client-side loop detection. Recommended: 30-60 seconds. Returns immediately if tests complete sooner. |
| `page_size` | `int \| None` | — | Terminal detail page size; requires a detail flag. Running jobs return progress until results are available. |
| `cursor` | `str \| None` | — | Opaque next_cursor from a result page |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
### Wait for a run to finish

> Wait for the test job I just started and show the failures.

```json
{
  "job_id": "<job_id from run_tests>",
  "wait_timeout": 60,
  "include_failed_tests": true
}
```

With `wait_timeout`, the server polls Unity every 2 seconds and returns as soon as `status` is `succeeded`, `failed` or `cancelled` — or after 60 s with the current progress, in which case call it again. This avoids a tight client-side polling loop.

### Check progress without waiting

> How far along is the test run?

```json
{
  "job_id": "<job_id from run_tests>"
}
```

Returns straight away. `data.progress` has `completed` / `total`, the test currently running, and `failures_so_far`. `data.result` (`summary`, plus a `results` list when `include_details` or `include_failed_tests` is set) is only filled when the job succeeded: a run with failing tests ends as `failed` with `result: null`, so read the failures from `data.progress.failures_so_far` (at most 25; `failures_capped` turns `true` once 25 are recorded, so there may be more). `data.error` stays `null` for failing tests; it is only set when the job itself did not finish normally (did not start in time, threw, was canceled, was cleared with `clear_stuck`, or was orphaned by a domain reload).

Polling preserves desktop focus and minimized windows by default. The local server's legacy recovery requires `UNITY_MCP_ENABLE_FOCUS_NUDGE=1` and a server restart; `UNITY_MCP_DISABLE_FOCUS_NUDGE=1` overrides it. With recovery enabled, a running, unfocused job without progress for more than 3 s may temporarily activate Unity and restore a minimized window. Returning focus does not re-minimize Unity. Attempts are bounded per job, and reported background execution skips recovery. Hosted servers never perform it. A long-running test alone does not mean recovery is needed; `data.progress.focus_nudge_status` reports `disabled` when it is off.

### Get details for every test

> Show me the result of every test, not just the failures.

```json
{
  "job_id": "<job_id from run_tests>",
  "include_details": true
}
```

`include_details` returns all tests; `include_failed_tests` returns only failed and skipped ones, which keeps the response small on large suites.
<!-- examples:end -->

