---
title: manage_play_scenario
sidebar_label: manage_play_scenario
description: "Save/get/list/delete scenarios, reports history and run/status/cancel Unity-owned jobs. save takes a whole definition; get/delete/run take its name; reports accepts optional name. status/cancel require a 32-character lowercase hexadecima…"
---

# `manage_play_scenario`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_play_scenario`

## Description

Save/get/list/delete scenarios, reports history and run/status/cancel Unity-owned jobs. save takes a whole definition; get/delete/run take its name; reports accepts optional name. status/cancel require a 32-character lowercase hexadecimal job_id. run returns immediately; each repeat executes setup_steps, steps, then bounded cleanup_steps. Steps load_scene/wait_scene use Assets/... .unity paths; click_ui/wait_object use exact active-scene hierarchy paths. uGUI clicks dispatch direct events, not UI Toolkit or occlusion tests. wait conditions support stability and object count/active/component/property. log_policy defaults strict; metrics are diagnostic and screenshots opt-in. Unity polls; Python does not. End leaves Play unchanged; repeats do not reset DontDestroyOnLoad or static state.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['save', 'get', 'list', 'delete', 'run', 'status', 'cancel', 'reports']` | yes | save, get, list, delete, run, status, cancel or reports |
| `scenario` | `PlayScenario \| None` | — | Whole scenario definition for save |
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

