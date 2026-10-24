---
title: manage_sprite_atlas
sidebar_label: manage_sprite_atlas
description: "Inspect and explicitly author SpriteAtlas V2 Assets/*.spriteatlasv2 files."
---

# `manage_sprite_atlas`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `animation` &nbsp;·&nbsp; **Module:** `services.tools.manage_sprite_atlas`

## Description

Inspect and explicitly author SpriteAtlas V2 Assets/*.spriteatlasv2 files. Actions: capabilities, create, read, configure, add_packables, remove_packables, pack. create requires properties.include_in_build; no overwrite or implicit folder creation. Existing writes require guid, file_id and expected_revision from read. Direct Sprite packables only, 128 references per request and 256 per atlas; no folders or texture expansion. properties supports include_in_build, packing, texture, platforms and exact master/master_revision with variant_scale. Read returns bounded packables pages, settings, master/variant and stored platform profiles; continuation requires revision. pack is synchronous editor preview for the explicit active build_target; a variant declares its master in the pack scope. Packing writes native caches/imported output and may unload/reload Sprites. It is not a Player build or proof of runtime delivery. Disabled/non-V2 modes refuse packing; no global settings, prebuild hooks or Addressables changes. Asset saves do not promise Undo or arbitrary callback rollback.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'create', 'read', 'configure', 'add_packables', 'remove_packables', 'pack']` | yes | Explicit authoring, inspection or preview-pack action. |
| `path` | `str \| None` | — | Existing or new contained Assets/*.spriteatlasv2 asset path. |
| `guid` | `str \| None` | — | Existing writes: exact atlas GUID from read. |
| `file_id` | `str \| None` | — | Existing writes: exact persistent local fileID string from read. |
| `expected_revision` | `str \| None` | — | Writes/continuation: exact revision from read. |
| `properties` | `dict[str, JsonValue] \| None` | — | create/configure settings or add/remove packables. Booleans are strict; enums use names. Packing: padding(2,4,8), enableRotation, enableTightPacking, enableAlphaDilation if supported. Texture: anisoLevel,filterMode,generateMipMaps,readable,sRGB. Platform profiles Standalone/Android/iPhone/WebGL: overridden,maxTextureSize(32-4096 power of two),format(Automatic/RGBA32/RGB24/Alpha8),textureCompression,compressionQuality. master uses {guid,file_id} plus master_revision; variant_scale is >0..1. Omitted settings persist. |
| `platforms` | `list[str] \| None` | — | read: selected stored platform profiles, max4; default Standalone, plus default profile. |
| `build_target` | `str \| None` | — | pack: exact active BuildTarget name from capabilities; no platform switch. |
| `offset` | `int \| None` | — | Packables page offset 0-256; continuation requires expected_revision. |
| `page_size` | `int \| None` | — | Packables page size1-100, default50. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

