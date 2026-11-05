---
title: cancel_test_job
sidebar_label: cancel_test_job
description: "Request cancellation of an owned native test job."
---

# `cancel_test_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.cancel_test_job`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Request cancellation of an owned native test job. A queued job cancelled before native execution can return status=cancelled with null native_control. For a started job, poll native_control for confirmed framework scheduler termination after task-cleanup attempts. User iterator finally, NUnit TearDown and UnityTearDown are not guaranteed. Cancelling a started job requires native control support.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | `str` | yes | Job ID returned by run_tests |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

