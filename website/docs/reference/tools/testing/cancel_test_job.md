---
title: cancel_test_job
sidebar_label: cancel_test_job
description: "Request cancellation of an owned native test job."
---

# `cancel_test_job`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.cancel_test_job`

## Description

Request cancellation of an owned native test job. Acceptance is intent; poll native_control for confirmed framework scheduler termination after task-cleanup attempts. User iterator finally, NUnit TearDown and UnityTearDown are not guaranteed. Unsupported versions reject without cancellation.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | `str` | yes | Job ID returned by run_tests |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

