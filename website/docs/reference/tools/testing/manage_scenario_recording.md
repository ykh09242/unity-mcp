---
title: manage_scenario_recording
sidebar_label: manage_scenario_recording
description: "Record observed uGUI Button pointer clicks and active-scene changes during unpaused Editor Play Mode into a bounded scenario draft. start returns recording_id immediately; status polls explicitly. stop saves a NEW scenario with draft=tru…"
---

# `manage_scenario_recording`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_scenario_recording`

## Description

Record observed uGUI Button pointer clicks and active-scene changes during unpaused Editor Play Mode into a bounded scenario draft. start returns recording_id immediately; status polls explicitly. stop saves a NEW scenario with draft=true and requires review before running; cancel discards it. max_events counts clicks, transitions and explicitly requested checkpoints, plus one initial load_scene step (at most 32 total steps). checkpoint requests a named screenshot in the future replay, never an inferred assertion. No timing, expected game state, raw input, keyboard, drag, scroll or UI Toolkit steps are invented. Diagnostics identify ambiguous targets and incomplete coverage. The optional com.unity.ugui backend is required. Reload or Play Mode exit stops observation and retains a status snapshot; domain-reloaded sessions cannot be resumed or saved.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['start', 'status', 'stop', 'cancel', 'checkpoint']` | yes | start, status, stop, cancel or checkpoint |
| `name` | `str \| None` | — | NEW saved draft name for start |
| `recording_id` | `str \| None` | — | Required for stop/cancel/checkpoint; optional current status filter |
| `max_events` | `int \| None` | — | start event budget, excluding initial load_scene; default 31 |
| `duration_seconds` | `int \| None` | — | start wall-clock limit in seconds; default 300 |
| `checkpoint_name` | `str \| None` | — | Explicit named screenshot checkpoint; required for checkpoint |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

