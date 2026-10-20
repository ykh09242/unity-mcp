---
title: capture_camera
sidebar_label: capture_camera
description: "Capture Game or Scene views, including surround and orbit batches."
---

# `capture_camera`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `camera` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Capture Game or Scene views, including surround and orbit batches.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['screenshot', 'screenshot_multiview']` | yes | Feature action to perform. |
| `screenshot_file_name` | `str \| None` | — | Screenshot file name (optional). Defaults to timestamp. |
| `screenshot_super_size` | `int \| str \| None` | — | Screenshot supersize multiplier (integer 1 to 4). |
| `camera` | `str \| None` | — | Camera to capture from (name, path, or instance ID). Omit to use ScreenCapture API (captures all layers including Screen Space Overlay UI). Specify only when you need a particular camera viewpoint; note that Screen Space - Overlay canvases will NOT appear in camera-rendered captures. |
| `include_image` | `bool \| str \| None` | — | If true, return screenshot as inline base64 PNG. Default false. |
| `max_resolution` | `int \| str \| None` | — | Max resolution (longest edge px) for inline image, 1 to 8192. Default 640. The Editor also enforces frame and aggregate pixel budgets. |
| `capture_source` | `Literal['game_view', 'scene_view'] \| None` | — | Screenshot source. 'game_view' (default) captures the game/camera path; 'scene_view' captures the active Unity Scene View viewport. |
| `batch` | `str \| None` | — | Batch capture mode: 'surround' (6 angles) or 'orbit' (configurable grid). |
| `view_target` | `str \| int \| list[float] \| None` | — | Target to focus on. GameObject name/path/ID or [x,y,z]. For game_view: aims camera at target. For scene_view: frames the Scene View on the target. |
| `view_position` | `list[float] \| str \| None` | — | World position [x,y,z] to place camera for positioned capture. |
| `view_rotation` | `list[float] \| str \| None` | — | Euler rotation [x,y,z] for camera. Overrides view_target if both provided. |
| `orbit_angles` | `int \| str \| None` | — | Number of azimuth samples for batch='orbit' (default 8, max 36). |
| `orbit_elevations` | `list[float] \| str \| None` | — | Elevation angles in degrees for batch='orbit' (default [0, 30, -15]). 1 to 16 finite angles; at most 128 total orbit shots. |
| `orbit_distance` | `float \| str \| None` | — | Camera distance from target for batch='orbit' (default auto). |
| `orbit_fov` | `float \| str \| None` | — | Camera FOV in degrees for batch='orbit' (default 60). |
| `output_folder` | `str \| None` | — | Optional folder for screenshot output. Project-relative (e.g. 'Assets/Screenshots' or 'Captures') or absolute path inside the project. Overrides the user's Editor preference. If omitted, falls back to the Editor preference, then to the built-in default (Assets/Screenshots). |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

