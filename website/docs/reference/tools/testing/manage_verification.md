---
title: manage_verification
sidebar_label: manage_verification
description: "Run project verification with source-linked stage reports. profiles reads defaults and explicit project overrides without writing. set_profile requires profile_id, profile and expected_hash from profiles (missing for a new file). prepare…"
---

# `manage_verification`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_verification`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Run project verification with source-linked stage reports. profiles reads defaults and explicit project overrides without writing. set_profile requires profile_id, profile and expected_hash from profiles (missing for a new file). prepare requires paths XOR change_set_id and accepts profile_id (script-check default), capture_baseline or baseline_id. It freezes selection and profile; poll status with preparation_id until ready before editing after a requested baseline. run takes preparation_id and returns one run_id; repeating it observes the same run. status accepts run_id XOR preparation_id. cancel and report take run_id. Cancellation intent does not prove child termination: inspect native_settled and retained child IDs. Defaults are script-check, editmode-check and playmode-check. Profiles allow refresh_policy, analysis_required, diagnostic_scope, fail_on severities, impact, test_mode and explicit test/assembly/category selectors plus bounded stage deadlines. No formatter, arbitrary command, scene switching, console clearing, focus change or replay. Unknown console loss prevents a clean pass. Reports last 30 minutes after settlement, bounded to 16 per owner/project and 64 shared; expired or restarted handles never rerun work.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['profiles', 'set_profile', 'prepare', 'run', 'status', 'cancel', 'report']` | yes |  |
| `options` | `dict[str, Any] \| None` | — | Action-specific profile, selection or owned handle parameters. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

