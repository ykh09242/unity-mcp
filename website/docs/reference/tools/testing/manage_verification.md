---
title: manage_verification
sidebar_label: manage_verification
description: "Run project verification with source-linked stage reports. profiles reads defaults and explicit project overrides without writing. set_profile requires profile_id, profile and expected_hash from profiles (missing for a new file). observe…"
---

# `manage_verification`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_verification`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Run project verification with source-linked stage reports. profiles reads defaults and explicit project overrides without writing. set_profile requires profile_id, profile and expected_hash from profiles (missing for a new file). observe_assembly reads an asmdef path and source from Editor and active-target Player compiler inputs. observe_texture reads a texture .meta path, importer platform and direct material/scene references within search_folder. For scripts, prepare takes paths XOR change_set_id and optional profile_id, capture_baseline or baseline_id. For registered local standalone builds, prepare or run takes candidate_id with optional baseline_candidate_id and source_mode (current or frozen). execute_selected_checks=true also runs mapped checks in the selected Editor, using selected_test_mode (EditMode by default or PlayMode). It freezes selection and profile; poll status with preparation_id until ready before editing after a requested baseline. run takes preparation_id, or paths XOR change_set_id with an optional profile_id/baseline_id to prepare and start together. wait_seconds (0..5, default 0) observes the original run without canceling it on expiry. Repeating run with preparation_id observes that same run. Capture pre-edit baselines through prepare. status accepts run_id XOR preparation_id. cancel and report take run_id. Cancellation intent does not prove child termination: inspect native_settled and retained child IDs. Defaults are script-check, editmode-check and playmode-check. Profiles allow refresh_policy, analysis_required, diagnostic_scope, fail_on severities, impact, test_mode and explicit test/assembly/category selectors plus bounded stage deadlines. Saved scenarios may enter Play Mode and load their declared scenes. Script profiles preserve scene and focus. Unknown console loss prevents a clean pass. Reports last 30 minutes after settlement, bounded to 16 per owner/project and 64 shared; expired or restarted handles never rerun work.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['profiles', 'set_profile', 'prepare', 'run', 'status', 'cancel', 'report', 'observe_assembly', 'observe_texture']` | yes |  |
| `options` | `dict[str, Any] \| None` | — | Action-specific profile, selection or owned handle parameters. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
### Verify a changed C# script

Enable the `testing` tool group, keep the `core` tools available for compile observation and refresh, and use the same connected Unity instance for every call. Check analyzer setup with [`manage_script_quality`](./manage_script_quality.md) using `capabilities`. In a permission-scoped connection, the built-in `script-check` profile needs `read`, `edit`, and `run` for observation, refresh if needed, and analysis. This example uses `Assets/BerthProbe.cs` from the repository's `TestProjects/VerificationProjects/Harbor` fixture. In your project, use the asset path of the changed script. If `script-check` has a project override, inspect it with `profiles` before starting.

**1. Prepare the script selection.** Preparation freezes the selection and profile. Check `success` and save `data.preparation_id`. Continue when `data.ready` is `true`. This example uses no baseline. For a pre-edit comparison, prepare with `capture_baseline: true` and poll `status` with `options.preparation_id` until `data.ready` is `true` before making the edit.

```json title="MCP arguments"
{
  "action": "prepare",
  "options": {
    "paths": ["Assets/BerthProbe.cs"],
    "profile_id": "script-check"
  }
}
```

**2. Start the prepared run.** Replace the example ID with `data.preparation_id` from step 1. Save the returned `data.run_id`. With `wait_seconds` omitted, the call returns without waiting for the stages. To recover an uncertain start, use the same preparation ID. Repeating `run` for that preparation observes the original run.

```json title="MCP arguments"
{
  "action": "run",
  "options": {"preparation_id": "0123456789abcdef0123456789abcdef"}
}
```

**3. Poll the run.** Replace the example ID with the `data.run_id` from step 2. Repeat with a short delay while `data.state` is `running` or `data.native_settled` is `false`. If native work remains unsettled within your observation window, retain the run ID and inspect `data.children` for the outstanding jobs. If the server reports `verification_not_found`, the handle is no longer available to this owner and target.

```json title="MCP arguments"
{
  "action": "status",
  "options": {"run_id": "fedcba9876543210fedcba9876543210"}
}
```

**4. Read the report.** Use the same run ID. A clean pass requires `data.state` equal to `passed` and `data.native_settled` equal to `true`. `failed`, `stale`, `incomplete`, and `canceled` are other terminal orchestration states. Read `data.stages` for the stage verdicts and `data.children` for native job settlement. Fetch the report while the handle is retained.

```json title="MCP arguments"
{
  "action": "report",
  "options": {"run_id": "fedcba9876543210fedcba9876543210"}
}
```
<!-- examples:end -->

