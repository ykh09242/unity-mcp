---
title: manage_recording
sidebar_label: manage_recording
description: "Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. start returns job_id immediately; poll status explicitly or stop to finalize."
---

# `manage_recording`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_recording`

## Description

Start, poll or stop a bounded silent MP4 recording of Unity Game View or Scene View. start returns job_id immediately; poll status explicitly or stop to finalize. Game View requires running unpaused Play Mode and a visible focused Game View; Scene View must stay visible. Windows/macOS interactive Editors only, subject to native MediaEncoder codec availability; capabilities reports support. Frames carry actual capture timestamps (variable frame rate); status reports actual frames and skipped sample slots. Output paths are local to the Unity host, inside project Captures/Recordings, with no overwrite or inline base64. Interrupted/failed jobs discard partial video. Resolution is scaled to width/height (aspect ratio differences stretch). Both encoded pixels and source plus encoded pixels have a 2 billion total pixel-frame budget.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'start', 'status', 'stop']` | yes | Recording operation. |
| `job_id` | `str \| None` | — | Required for status/stop; returned by start. |
| `capture_source` | `Literal['game_view', 'scene_view']` | — | View to capture. |
| `duration_seconds` | `float` | — | Maximum wall-clock recording duration. |
| `fps` | `int` | — | Maximum sampling rate; missing frames are not synthesized. |
| `width` | `int` | — | Even output width. |
| `height` | `int` | — | Even output height. |
| `output_folder` | `str \| None` | — | Project-relative directory within Captures/Recordings, or an absolute path inside it. |
| `file_name` | `str \| None` | — | New plain filename, optionally ending in .mp4; generated if omitted. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Check the selected Editor's native recording capability:

```json
{"action":"capabilities"}
```

Start a short recording with the Game View visible and focused in running unpaused Play Mode:

```json
{"action":"start","capture_source":"game_view","duration_seconds":5,"fps":15,"width":1280,"height":720,"file_name":"interaction-check.mp4"}
```

Retain the returned `job_id` and explicitly poll it:

```json
{"action":"status","job_id":"returned-job-id"}
```

Use `stop` with the same ID to finalize early. Inspect terminal status, actual captured frames and the final local path; a start acknowledgement does not prove a playable video was produced. Output is silent and stays within the selected Unity project's `Captures/Recordings` directory. The filename must be new.
<!-- examples:end -->

