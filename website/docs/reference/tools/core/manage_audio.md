---
title: manage_audio
sidebar_label: manage_audio
description: "Play or stop an existing scene AudioSource in Play mode."
---

# `manage_audio`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_audio`

## Description

Play or stop an existing scene AudioSource in Play mode. Play requires an active, enabled source and a usable AudioClip; optionally supply a clip asset path under Assets/ or Packages/. Stop also supports inactive, disabled or clipless sources. No component is created. A successful response confirms the request was issued, not audible output. Omit search_method for automatic ID, path or name resolution.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['play', 'stop']` | yes | Playback action to perform. |
| `target` | `str \| int` | yes | Scene GameObject name, hierarchy path or integer instance ID. |
| `clip` | `str \| None` | — | Optional AudioClip asset path for play only; omit to use the source's current clip. |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_id_or_name_or_path'] \| None` | — | Target selector. Omitted or null uses automatic ID, path or name resolution. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

