---
title: manage_sprite_atlas
sidebar_label: manage_sprite_atlas
description: "Inspect and explicitly author Sprite Atlas V1 (.spriteatlas) and V2 (.spriteatlasv2) assets."
---

# `manage_sprite_atlas`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `animation` &nbsp;·&nbsp; **Module:** `services.tools.manage_sprite_atlas`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect and explicitly author Sprite Atlas V1 (.spriteatlas) and V2 (.spriteatlasv2) assets. Actions: capabilities, create, read, configure, add_packables, remove_packables, pack. capabilities reports action availability, setting_writes and membership/preview source types per format under formats.V1 and formats.V2. The legacy top-level availability fields describe V2. create requires properties.include_in_build; no overwrite or implicit folder creation. Existing writes require guid, file_id and expected_revision from read. Membership writes take direct Sprite references only, 128 per request and 256 per atlas. properties supports include_in_build, packing, texture, platforms and exact same-format master/master_revision with variant_scale. Read returns bounded packables pages, settings, master/variant and stored platform profiles; continuation requires revision. pack is synchronous editor preview for the explicit active build_target; a variant declares its master in the pack scope. Packing writes native caches/imported output and may unload/reload Sprites. It is not a Player build or proof of runtime delivery. Preview requires the format's packer mode and resolves existing Sprite textures or folders within the bounded source scope. On importer-backed Editors V1 preview requires Unity's 2D Sprite TextureImporter provider and a completed Editor update after observing the revision. Reopen the project before V1 preview if the atlas or its master was created, or its source images were imported or changed, in the current Editor session. V1 writes require a packer mode that preserves .spriteatlas files. No conversions, global settings, prebuild hooks or Addressables changes. Asset saves do not promise Undo or arbitrary callback rollback.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'create', 'read', 'configure', 'add_packables', 'remove_packables', 'pack']` | yes | Explicit authoring, inspection or preview-pack action. |
| `path` | `str \| None` | — | Contained Assets path with a .spriteatlas (V1) or .spriteatlasv2 (V2) extension. |
| `guid` | `str \| None` | — | Existing writes: exact atlas GUID from read. |
| `file_id` | `str \| None` | — | Existing writes: exact persistent local fileID string from read. |
| `expected_revision` | `str \| None` | — | Writes/continuation: exact revision from read. |
| `properties` | `dict[str, JsonValue] \| None` | — | create/configure settings or add/remove packables. Booleans are strict; enums use names. Packing: padding(2,4,8), enableRotation, enableTightPacking, enableAlphaDilation if supported. Texture: anisoLevel,filterMode,generateMipMaps,readable,sRGB. Platform profiles Standalone/Android/iPhone/WebGL: overridden,maxTextureSize(32-4096 power of two),format(Automatic/RGBA32/RGB24/Alpha8),textureCompression,compressionQuality. master uses {guid,file_id} plus master_revision; variant_scale is >0..1. Omitted settings persist. |
| `platforms` | `list[str] \| None` | — | read: selected stored platform profiles, max4; default Standalone, plus default profile. |
| `build_target` | `str \| None` | — | pack: exact active BuildTarget name from capabilities; no platform switch. |
| `offset` | `int \| None` | — | Packables page offset 0-256; continuation requires expected_revision. |
| `page_size` | `int \| None` | — | Packables page size1-100, default50. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Check whether the Editor can work with Sprite Atlas assets before opening or creating an atlas.

```json title="MCP arguments"
{
  "action": "capabilities"
}
```
<!-- examples:end -->

