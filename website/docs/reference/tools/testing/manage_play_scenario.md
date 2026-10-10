---
title: manage_play_scenario
sidebar_label: manage_play_scenario
description: "Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs."
---

# `manage_play_scenario`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_play_scenario`

## Description

Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs. Suite actions use suite_ prefixes, suite_save takes suite and suite_status/cancel take suite_id. save takes a whole definition; get/delete/run take its name; reports accepts optional name. status/cancel require a 32-character lowercase hexadecimal job_id. run returns immediately; each repeat executes setup_steps, steps, then bounded cleanup_steps. Steps load_scene/wait_scene use Assets/... .unity paths; click_ui/wait_object use target_id or exact active-scene hierarchy paths. uGUI click_mode direct dispatches events; raycast verifies a visible hit. wait conditions support stability and object count/active/component/property. log_policy defaults strict; metrics are diagnostic, resources assert registered growth and screenshots/timeline opt-in. wait_state reads an explicit registered state_id scalar without reflection; reset_state invokes registered reset_ids explicitly; query_budget bounds actual target searches. Tags select sequential suite children in one Editor. Unity polls; Python does not. End leaves Play unchanged; load_scene does not reset DontDestroyOnLoad or static state without explicit reset participants.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['save', 'get', 'list', 'delete', 'run', 'status', 'cancel', 'reports', 'suite_save', 'suite_get', 'suite_list', 'suite_delete', 'suite_run', 'suite_status', 'suite_cancel', 'suite_reports']` | yes | save, get, list, delete, run, status, cancel or reports |
| `scenario` | `PlayScenario \| None` | — | Whole scenario definition for save |
| `suite` | `PlayScenarioSuite \| None` | — | Whole suite definition for suite_save |
| `suite_id` | `str \| None` | — | suite_status/suite_cancel ID; optional suite_run key |
| `source_revision` | `str \| None` | — | Optional caller-provided run revision label, at most 128 UTF-16 units |
| `name` | `str \| None` | — | Saved name for get/delete/run; optional reports filter |
| `job_id` | `str \| None` | — | Required for status/cancel; optional run request key |
| `repeat_count` | `int \| None` | — | run repetitions, default 1 |
| `timeout_seconds` | `int \| None` | — | run total timeout in seconds, default 300 |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

