---
title: wait_for_operation
sidebar_label: wait_for_operation
description: "Observe an existing build, package mutation/resolve, or single scenario job."
---

# `wait_for_operation`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.wait_for_operation`

## Description

Observe an existing build, package mutation/resolve, or single scenario job. Never starts, retries or cancels native work. wait_timeout bounds only this observation (0 is one poll). Reports waiting/running/finalizing/terminal/unknown with the original job evidence; a failed job is a valid observation. An authenticated connection is captured once; reload/disconnect requires a fresh explicit observation. Opt-in modern HTTP Tasks can durably retain this bounded wait; other clients receive ordinary polling results.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `kind` | `Literal['build', 'package', 'scenario']` | yes |  |
| `job_id` | `str` | yes |  |
| `wait_timeout` | `int` | — |  |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

