---
title: manage_scenario_recording
sidebar_label: manage_scenario_recording
description: "Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode."
---

# `manage_scenario_recording`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_scenario_recording`

## Description

Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode. An optional enabled Input System observer also captures delivered raw key/mouse/touch/wheel events from game Dynamic/Fixed updates; manual Input System update mode is unsupported. Observed holds and spacing are quantized to 1..600 input updates, with diagnostics for simultaneous events and incomplete coverage; subframe timing and gameplay outcomes are not inferred. start returns recording_id immediately; status polls explicitly. stop saves a NEW scenario with draft=true and requires review before running; cancel discards it. max_events counts emitted clicks, raw input steps, transitions and requested checkpoints, plus one initial load_scene step (at most 32 total steps). checkpoint requests a named screenshot in the future replay, never an inferred assertion. Opt-in all_loaded captures a bounded initial scene context and exact scene-qualified clicks; transitions are observed waits, never inferred loading/unloading effects. UI Toolkit element-target recording remains manual; raw packets do not prove which handler consumed them. At least one supported observer is required: optional com.unity.ugui for Button clicks or optional Input System for raw input. Pause, reload or Play Mode exit stops observation and retains a status snapshot; domain-reloaded sessions cannot be resumed or saved.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['start', 'status', 'stop', 'cancel', 'checkpoint']` | yes | start, status, stop, cancel or checkpoint |
| `name` | `str \| None` | — | NEW saved draft name for start |
| `recording_id` | `str \| None` | — | Required for stop/cancel/checkpoint; optional current status filter |
| `max_events` | `int \| None` | — | start event budget, excluding initial load_scene; default 31 |
| `duration_seconds` | `int \| None` | — | start wall-clock limit in seconds; default 300 |
| `scene_scope` | `Literal['active', 'all_loaded'] \| MISSING` | — | start-only scene scope: active (default) or all_loaded |
| `checkpoint_name` | `str \| None` | — | Explicit named screenshot checkpoint; required for checkpoint |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

