---
title: manage_play_scenario
sidebar_label: manage_play_scenario
description: "Save/get/list/delete repeatable Play Mode scenarios and run/status/cancel Unity-owned jobs. save takes a whole scenario definition; get/delete/run take its name. status/cancel require a 32-character lowercase hexadecimal job_id. run retu…"
---

# `manage_play_scenario`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_play_scenario`

## Description

Save/get/list/delete repeatable Play Mode scenarios and run/status/cancel Unity-owned jobs. save takes a whole scenario definition; get/delete/run take its name. status/cancel require a 32-character lowercase hexadecimal job_id. run returns immediately, enters Play if needed and reloads the first scene each repeat. Steps load_scene/wait_scene use Assets/... .unity paths; click_ui/wait_object use exact active-scene hierarchy paths. uGUI clicks dispatch direct events, not UI Toolkit or occlusion tests. Conditions are polled in Unity; Python creates no polling task. Completion/cancel leaves Play unchanged; repeats do not reset DontDestroyOnLoad or static state.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['save', 'get', 'list', 'delete', 'run', 'status', 'cancel']` | yes | save, get, list, delete, run, status or cancel |
| `scenario` | `PlayScenario \| None` | — | Whole scenario definition for save |
| `name` | `str \| None` | — | Saved scenario name for get/delete/run |
| `job_id` | `str \| None` | — | Required for status/cancel; optional run request key |
| `repeat_count` | `int \| None` | — | run repetitions, default 1 |
| `timeout_seconds` | `int \| None` | — | run total timeout in seconds, default 300 |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

