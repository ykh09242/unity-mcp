---
title: manage_play_scenario
sidebar_label: manage_play_scenario
description: "Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs."
---

# `manage_play_scenario`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_play_scenario`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs. Suite actions use suite_ prefixes, suite_save takes suite and suite_status/cancel take suite_id. run accepts an optional dataset; typed explicit scalar bindings are expanded and validated before saving. save takes a whole definition; get/delete/run take its name; reports accepts optional name. status reads the existing job's runtime step results and terminal outcome; run only starts it. status/cancel require a 32-character lowercase hexadecimal job_id. run returns immediately; each repeat executes setup_steps, steps, then bounded cleanup_steps. Steps load_scene/wait_scene use Assets/... .unity paths; click_ui/wait_object/wait_ui_focus use target_id or exact hierarchy paths in the active or explicitly selected scene. wait_ui_focus reads native uGUI EventSystem selection. uGUI click_mode direct dispatches events; raycast verifies a visible hit. wait conditions support stability and object count/active/component/property. log_policy defaults strict; metrics are diagnostic, resources assert registered growth and screenshots/timeline opt-in. wait_state reads an explicit registered state_id scalar without reflection; reset_state invokes registered reset_ids explicitly; query_budget bounds actual target searches. Tags select sequential suite children in one Editor. Unity polls; Python does not. End leaves Play unchanged; load_scene does not reset DontDestroyOnLoad or static state without explicit reset participants.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['save', 'get', 'list', 'delete', 'run', 'status', 'cancel', 'reports', 'suite_save', 'suite_get', 'suite_list', 'suite_delete', 'suite_run', 'suite_status', 'suite_cancel', 'suite_reports']` | yes | save, get, list, delete, run, status, cancel or reports |
| `scenario` | `PlayScenario \| None` | — | Whole scenario definition for save |
| `suite` | `PlayScenarioSuite \| None` | — | Whole suite definition for suite_save |
| `suite_id` | `str \| None` | — | suite_status/suite_cancel ID; optional suite_run key |
| `dataset` | `str \| None` | — | Optional saved dataset case for run; omission uses parameter defaults |
| `source_revision` | `str \| None` | — | Optional caller-provided run revision label, at most 128 UTF-16 units |
| `name` | `str \| None` | — | Saved name for get/delete/run; optional reports filter |
| `job_id` | `str \| None` | — | Required for status/cancel; optional run request key |
| `repeat_count` | `int \| None` | — | run repetitions, default 1 |
| `timeout_seconds` | `int \| None` | — | run total timeout in seconds, default 300 |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
### Save and run a scene smoke test

Enable the `testing` tool group and use the same connected Unity instance throughout. In a permission-scoped connection, `save` needs `edit`, `run` needs `run`, and `status` needs `read`. This example uses the repository's `TestProjects/VerificationProjects/Harbor` fixture. Its `Assets/Dock.unity` scene registers `harbor.berth` through `BerthProbe`. When adapting it, use an existing scene and a state provider from your project. The run may enter Play Mode and will load the declared scene. Save any open scene edits before starting.

**1. Save the scenario.** Use a new name in the project to preserve existing definitions. Continue after a successful response.

```json title="MCP arguments"
{
  "action": "save",
  "scenario": {
    "name": "harbor-berth-smoke",
    "steps": [
      {"name": "Load project scene", "action": "load_scene", "scene": "Assets/Dock.unity"},
      {
        "name": "Read project state",
        "action": "wait_state",
        "state_id": "harbor.berth",
        "state_equals": 3,
        "timeout_seconds": 5
      }
    ]
  }
}
```

**2. Start the run.** Replace the example `job_id` with a fresh 32-character lowercase hexadecimal ID and keep it with the request. The tool returns immediately. Save `data.job_id` from a successful start. If the acknowledgment is lost, check `status` with the ID you supplied before attempting another start.

```json title="MCP arguments"
{
  "action": "run",
  "name": "harbor-berth-smoke",
  "job_id": "0123456789abcdef0123456789abcdef",
  "repeat_count": 1,
  "timeout_seconds": 60
}
```

**3. Poll the job and inspect the terminal report.** Replace the example `job_id` with the same ID from step 2. Poll with a short delay while `data.status` is `running`, including during finalization. A successful scenario has `data.status` equal to `succeeded`, `data.runner_resources_released` equal to `true`, and no `data.report_error`. `failed`, `timed_out`, and `cancelled` are other terminal outcomes. Use `data.steps` and `data.logs` to investigate a failure. Unity may still be in Play Mode after the job ends.

```json title="MCP arguments"
{
  "action": "status",
  "job_id": "0123456789abcdef0123456789abcdef"
}
```
<!-- examples:end -->

