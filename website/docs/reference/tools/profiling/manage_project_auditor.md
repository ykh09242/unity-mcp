---
title: manage_project_auditor
sidebar_label: manage_project_auditor
description: "Optional Project Auditor 1.0.1 integration. availability checks the installed package, API and bundled rules without starting analysis. start requests one Editor-session audit and returns audit_id; reuse that ID after an uncertain respon…"
---

# `manage_project_auditor`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `profiling` &nbsp;·&nbsp; **Module:** `services.tools.manage_project_auditor`

**Maturity:** `experimental` (support status; independent of enabled state, readiness and permissions).

## Description

Optional Project Auditor 1.0.1 integration. availability checks the installed package, API and bundled rules without starting analysis. start requests one Editor-session audit and returns audit_id; reuse that ID after an uncertain response. status observes its queued/running/completed/failed/unavailable/interrupted state. issues pages captured diagnostics after completion (page_size 1..100, opaque cursor). Analysis uses the current build target and may compile Player assemblies and save Auditor settings. Missing rules and failed modules are distinct from completed zero diagnostics. The package is never installed automatically. No cancellation or arbitrary report file writes are supported.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['availability', 'start', 'status', 'issues']` | yes | availability, start, status, or issues. |
| `audit_id` | `str \| None` | — | Editor-session audit identity. Omit on start to allocate once; required for status/issues. |
| `page_size` | `int \| None` | — | issues only: row limit (default 50). |
| `cursor` | `str \| None` | — | issues only: opaque cursor returned by the previous page. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

