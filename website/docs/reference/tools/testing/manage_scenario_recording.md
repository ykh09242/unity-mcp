---
title: manage_scenario_recording
sidebar_label: manage_scenario_recording
description: "Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode."
---

# `manage_scenario_recording`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_scenario_recording`

## Description

Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode. An enabled Input System observer captures delivered key/mouse/touch/wheel events from game Dynamic/Fixed updates; Old Input Handling uses read-only UnityEngine.Input observation in game Update. Status input_observation identifies source, frame clock and replay limits. Old-mode raw observation cannot replay into UnityEngine.Input: retain target-based click_ui for uGUI and review/remove raw steps before Old-mode replay. No input handling, focus or background settings are changed. Manual Input System update mode is unsupported. Observed holds and spacing are quantized to 1..36000 game updates, with diagnostics for simultaneous events and incomplete coverage; subframe timing and gameplay outcomes are not inferred. start returns recording_id immediately; status polls explicitly. stop saves a NEW scenario with draft=true and requires review before running; cancel discards it. max_events counts emitted clicks, raw input steps, transitions and requested checkpoints, plus one initial load_scene step (at most 128 total steps). checkpoint requests a named screenshot in the future replay, never an inferred assertion. Opt-in all_loaded captures a bounded initial scene context and exact scene-qualified clicks; transitions are observed waits, never inferred loading/unloading effects. UI Toolkit element-target recording remains manual; raw packets do not prove which handler consumed them. At least one supported observer is required: optional com.unity.ugui for Button clicks or Input System or Old Input Handling for raw observation. Pause, reload or Play Mode exit stops observation and retains a status snapshot; domain-reloaded sessions cannot be resumed or saved.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['start', 'status', 'stop', 'cancel', 'checkpoint']` | yes | start, status, stop, cancel or checkpoint |
| `name` | `str \| None` | — | NEW saved draft slug for start: 1-64 lowercase letters/digits, hyphens or underscores; start with a lowercase letter/digit, e.g. menu-flow-01 (pattern ^[a-z0-9][a-z0-9_-]{0,63}$) |
| `recording_id` | `str \| None` | — | Required for stop/cancel/checkpoint; optional current status filter |
| `max_events` | `int \| None` | — | start event budget, excluding initial load_scene; default 31 |
| `duration_seconds` | `int \| None` | — | start wall-clock limit in seconds; default 300 |
| `scene_scope` | `Literal['active', 'all_loaded'] \| MISSING` | — | start-only scene scope: active (default) or all_loaded |
| `checkpoint_name` | `str \| None` | — | Screenshot checkpoint slug, required for checkpoint: 1-64 lowercase letters/digits, hyphens or underscores, starting with a letter/digit; e.g. menu-ready |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

