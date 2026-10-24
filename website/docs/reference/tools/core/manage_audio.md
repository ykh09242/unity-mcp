---
title: manage_audio
sidebar_label: manage_audio
description: "Diagnose observed scene audio state without proving output-device audibility; inspect or explicitly set existing mixer-group routing; read/apply per-platform AudioImporter policies."
---

# `manage_audio`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_audio`

## Description

Diagnose observed scene audio state without proving output-device audibility; inspect or explicitly set existing mixer-group routing; read/apply per-platform AudioImporter policies. Routing records Undo and dirties the scene; saving the scene is separate. Importer policy saves/reimports and reads native settings back; reimport may limit Undo. Play or stop an existing scene AudioSource in Play mode. Play requires an active, enabled source and a usable AudioClip; optionally supply a clip asset path under Assets/ or Packages/. Stop also supports inactive, disabled or clipless sources. No component is created. A successful response confirms the request was issued, not audible output. Omit search_method for automatic ID, path or name resolution.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['play', 'stop', 'diagnose', 'get_routing', 'set_routing', 'read_import_settings', 'apply_import_settings']` | yes | Audio observation, explicit routing/import policy, or playback action. |
| `target` | `str \| int` | yes | Scene GameObject name, hierarchy path or integer instance ID. Importer actions instead require an AudioClip asset path under Assets/. |
| `clip` | `str \| None` | — | Optional AudioClip asset path for play only; omit to use the source's current clip. |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_id_or_name_or_path'] \| None` | — | Target selector. Omitted or null uses automatic ID, path or name resolution. |
| `mixer_path` | `str \| None` | — | get_routing inventory or set_routing existing AudioMixer asset path. |
| `group_id` | `str \| None` | — | set_routing exact persistent group local file ID from get_routing inventory; never a first-name match. |
| `clear_routing` | `bool` | — | set_routing explicit reset to the default output; excludes mixer_path/group_id. |
| `platform` | `str \| None` | — | Importer platform: Default (omitted) or exact BuildTargetGroup name, e.g. Standalone, Android, iOS. Read values without override are Unity-translated defaults. |
| `settings` | `AudioImportPolicy \| None` | — |  |
| `clear_override` | `bool` | — | apply_import_settings removes the selected platform override; excludes settings and Default platform. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

