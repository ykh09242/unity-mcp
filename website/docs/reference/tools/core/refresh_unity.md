---
title: refresh_unity
sidebar_label: refresh_unity
description: "Request a Unity asset database refresh and optionally a script compilation."
---

# `refresh_unity`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.refresh_unity`

## Description

Request a Unity asset database refresh and optionally a script compilation. Can wait for native Editor readiness, independently of background file scan coverage. Readiness timeout preserves the latest compilation snapshot and reports readiness blockers separately; compilation_observation_age_seconds describes snapshot age. A timeout never replays the refresh or cancels native compilation.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `mode` | `Literal['if_dirty', 'force']` | — | Refresh mode |
| `scope` | `Literal['assets', 'scripts', 'all']` | — | Refresh scope |
| `compile` | `Literal['none', 'request']` | — | Whether to request compilation |
| `wait_for_ready` | `bool` | — | If true, wait until mcpforunity://editor/state reports data.advice.ready_for_tools true |
| `timeout_seconds` | `float` | — | Total refresh/readiness observation budget in seconds; expiry does not undo native work |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

