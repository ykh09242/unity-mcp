---
title: manage_object_handles
sidebar_label: manage_object_handles
description: "Capture or resolve the original loaded GameObject/Component reference for this Editor session."
---

# `manage_object_handles`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_object_handles`

## Description

Capture or resolve the original loaded GameObject/Component reference for this Editor session. Capture accepts only a nonzero live instance_id; resolve accepts only handle. All handles, including clean saved scenes, expire after 600 seconds or earlier eviction from a 512-entry weak cache. Scene close/open/new-scene events, domain reload/Editor restart, every Play transition and Undo/Redo expire all handles. Direct rename, property edits, same-scene reparenting and save retain the original reference. Resolution requires its original loaded owner/scene/prefab-stage context; observed context mismatch is final. An unobserved scene move and return may retain the same original reference. Current prefab-stage and runtime scene objects are supported; persistent asset objects are not. Never loads assets/scenes, saves, scans or falls back to serialized IDs, names or paths. require_persistent and allow_asset_load are unsupported, including false: omit both. On stale_handle rediscover explicitly and capture again. Requires v2 Editor commands; older Editors fail closed. Returned instance IDs remain ephemeral; no reload retry.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capture', 'resolve']` | yes |  |
| `instance_id` | `int` | — |  |
| `handle` | `str` | — |  |
| `require_persistent` | `bool` | — | Unsupported legacy field: omit entirely. Any presence, including false, fails. |
| `allow_asset_load` | `bool` | — | Unsupported legacy field: omit entirely. Any presence, including false, fails. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

