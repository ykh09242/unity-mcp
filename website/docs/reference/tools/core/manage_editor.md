---
title: manage_editor
sidebar_label: manage_editor
description: "Controls Unity editor state and settings. play optionally waits for scene_loaded or first_frame with a bounded persisted job. first_frame confirms simulation frame progress, not rendered pixels or application async initialization. get_pl…"
---

# `manage_editor`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_editor`

## Description

Controls Unity editor state and settings. play optionally waits for scene_loaded or first_frame with a bounded persisted job. first_frame confirms simulation frame progress, not rendered pixels or application async initialization. get_play_mode_job polls; cancel_play_mode_job cancels monitoring without stopping Play Mode. get_game_view_size lists current size and presets for an open Game View. set_game_view_size selects fixed width/height, aspect_ratio W:H, or a preset label; restore_game_view_size uses the returned restore_token. Screenshots do not mutate Game View size. pause toggles pause/resume. Also supports telemetry_status, telemetry_ping, set_active_tool, add_tag, remove_tag, add_layer, remove_layer, deploy_package, restore_package, undo, redo. For prefab stages use manage_prefabs.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['telemetry_status', 'telemetry_ping', 'play', 'pause', 'stop', 'set_active_tool', 'add_tag', 'remove_tag', 'add_layer', 'remove_layer', 'deploy_package', 'restore_package', 'undo', 'redo', 'get_play_mode_job', 'cancel_play_mode_job', 'get_game_view_size', 'set_game_view_size', 'restore_game_view_size']` | yes | Editor operation |
| `tool_name` | `str \| None` | — | Tool name when setting active tool |
| `tag_name` | `str \| None` | — | Tag name when adding and removing tags |
| `layer_name` | `str \| None` | — | Layer name when adding and removing layers |
| `wait_until` | `Literal['scene_loaded', 'first_frame'] \| None` | — | Optional bounded readiness wait for play |
| `timeout_seconds` | `int` | — | Play readiness deadline in seconds |
| `job_id` | `str \| None` | — | Job ID for get_play_mode_job/cancel_play_mode_job |
| `width` | `int \| None` | — | Fixed Game View width; combined limit 16 megapixels |
| `height` | `int \| None` | — | Fixed Game View height |
| `aspect_ratio` | `str \| None` | — | Game View aspect ratio W:H, components at most 8192 |
| `preset` | `str \| None` | — | Unique preset label returned by get_game_view_size |
| `restore_token` | `str \| None` | — | Token returned by set_game_view_size; retained for the latest 16 changes in this editor session |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

