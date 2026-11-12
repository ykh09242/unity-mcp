---
title: wait_for_operation
sidebar_label: wait_for_operation
description: "Observe an existing test, build, package query/mutation/resolve, single scenario, recording, or Project Auditor job."
---

# `wait_for_operation`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.wait_for_operation`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Observe an existing test, build, package query/mutation/resolve, single scenario, recording, or Project Auditor job. Never starts, retries or cancels native work. wait_timeout bounds only this observation (0 is one poll). Reports waiting/running/finalizing/terminal/unknown with the original job evidence; a failed job is a valid observation. An authenticated connection is captured once; reload/disconnect requires a fresh explicit observation. Opt-in modern HTTP Tasks can durably retain this bounded wait; other clients receive ordinary polling results.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `kind` | `Literal['build', 'package', 'scenario', 'recording', 'auditor', 'test']` | yes |  |
| `job_id` | `str` | yes |  |
| `wait_timeout` | `int` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

