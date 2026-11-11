---
title: manage_script_quality
sidebar_label: manage_script_quality
description: "Observe optional pinned Unity/C# diagnostics without writing source. capabilities reports explicit Editor setup/consent. analyze and capture_baseline require options.paths (1..256 Assets/Packages .cs files) XOR options.change_set_id; inc…"
---

# `manage_script_quality`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_script_quality`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Observe optional pinned Unity/C# diagnostics without writing source. capabilities reports explicit Editor setup/consent. analyze and capture_baseline require options.paths (1..256 Assets/Packages .cs files) XOR options.change_set_id; include_suppressed defaults false, timeout_seconds 1..300 defaults 120. They return a project-owned job_id. status requires job_id and accepts page_size 1..500 (default 100) and the returned opaque cursor. A complete capture publishes baseline_id; analyze accepts options.baseline_id. Status classifies introduced/existing/resolved/unclassified issues only with comparable evidence. Results expire after 30 minutes and may be evicted within bounded per-project/shared budgets; IDs never replay work. cancel requests owned job cancellation; status confirms settlement. impact requires analysis_id. Prepared change sets inspect existing original bytes; applied sets inspect verified candidate bytes. No implicit Git selection, refresh, package installation, formatting, or automatic fixes. Partial/stale/context diagnostics are distinct from a complete selected-file result.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'capture_baseline', 'analyze', 'status', 'cancel', 'impact']` | yes |  |
| `options` | `dict[str, Any] \| None` | — | Action-specific explicit selection, job or page parameters. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

