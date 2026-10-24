---
title: manage_timeline
sidebar_label: manage_timeline
description: "Inspect/create optional Timeline Assets/*.playable assets and explicitly add built-in animation/audio tracks and clips."
---

# `manage_timeline`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `animation` &nbsp;·&nbsp; **Module:** `services.tools.manage_timeline`

## Description

Inspect/create optional Timeline Assets/*.playable assets and explicitly add built-in animation/audio tracks and clips. Actions: capabilities, create, read, add_track, add_clip, assign, bind, read_director, preview_start, seek, preview_stop. Existing edits and preview require exact asset guid/file_id and expected_revision from read; director actions use target component instance ID, scene_handle and expected_director_revision from read_director. Clip sources use exact {guid,file_id}; timing is seconds. Track/clip/binding authoring uses native editor Undo; creating the asset does not promise Undo. No implicit overwrite, folders, packages or custom tracks. Manual preview supports bounded Transform-only nonhumanoid animation without events/object-reference curves and muted AudioSources. It refuses existing director graphs, external AnimationMode, custom/control tracks and markers; seek evaluates once, never starts realtime playback. preview_stop or 120-second idle expiry stops the owned graph and conditionally restores captured state, preserving/reporting external writers. No universal callback rollback, global Undo or audible playback guarantee. Read pages contain at most 32 tracks and 32 clips per track, within 256 KiB; continue next_offset using expected_revision. Continue a track's next_clip_offset using track_id,clip_offset and the same expected_revision. Display names are bounded to 1024 characters. read_director pages use offset,page_size and expected_director_revision; graph size is not an admission limit.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'create', 'read', 'add_track', 'add_clip', 'assign', 'bind', 'read_director', 'preview_start', 'seek', 'preview_stop']` | yes | Explicit inspection, authoring or owned-preview action. |
| `path` | `str \| None` | — | Contained existing/new Assets/*.playable path. |
| `guid` | `str \| None` | — | Exact existing Timeline GUID from read. |
| `file_id` | `str \| None` | — | Exact existing Timeline fileID string from read. |
| `expected_revision` | `str \| None` | — | Asset/source revision from read; also required for continuation. |
| `properties` | `dict[str, JsonValue] \| None` | — | create: name optional. add_track: kind(animation/audio),name. add_clip: track_id,source{guid,file_id},start,duration, optional clip_in(0),time_scale(1),display_name. bind: track_id,binding_target exact component ID. |
| `target` | `int \| None` | — | Exact PlayableDirector component instance ID. |
| `scene_handle` | `int \| None` | — | Exact loaded director scene handle. |
| `expected_director_revision` | `str \| None` | — | Revision from read_director before assign/bind/preview_start or binding continuation. |
| `preview_id` | `str \| None` | — | Owned session token from preview_start; required by seek/preview_stop. |
| `time` | `int \| float \| None` | — | seek: finite absolute time in the supported Timeline range. |
| `offset` | `int \| None` | — | read/read_director: nonnegative track/binding offset, default0. |
| `page_size` | `int \| None` | — | read/read_director: page size1-32, default16. |
| `track_id` | `str \| None` | — | read: exact saved track fileID for a clip page; omit offset. |
| `clip_offset` | `int \| None` | — | read: nonnegative clip offset in track_id; continuation requires expected_revision. |
| `clip_page_size` | `int \| None` | — | read: clips per track1-32, default16. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

