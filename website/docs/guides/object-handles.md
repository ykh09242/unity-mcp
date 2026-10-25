---
title: Session Object Handles
description: Keep the original loaded GameObject or Component reference during bounded Editor work.
---

`manage_object_handles` captures an opaque handle to the original loaded GameObject or Component. Use it when several requests must address the same object while names, properties or hierarchy positions change.

All handles are session handles, including objects in clean saved scenes. They are backed by weak references to the original objects, with a fixed 600-second lifetime and a 512-entry cache that may evict earlier. Resolution does not extend that deadline. A handle does not keep an object alive, restore a deleted object, or identify a recreated object.

## Capture and resolve

Discover the current live instance ID with the existing scene tools/resources, then capture:

```json
{"action":"capture","instance_id":-123}
```

The response descriptor includes `contractVersion: 2`, `persistence: "session"`, the opaque `handle`, `objectKind`, current `instanceID`, bounded name/type metadata and `validUntil`. A Component descriptor also includes its current owner GameObject ID. `validUntil.editorTimeSeconds` is an Editor uptime deadline, not a wall-clock timestamp; `ttlSeconds` is 600 and `mayEvict` is true.

Pass the returned handle unchanged:

```json
{"action":"resolve","handle":"<handle returned by capture>"}
```

The tool resolves only the original reference in its original loaded owner, scene and prefab-stage context. It never loads or saves a scene/asset, scans the hierarchy, or falls back to a serialized ID, name, path or current component index. Capture supports saved, dirty, unsaved and runtime scene objects, plus the current prefab stage. Persistent asset objects are unsupported; use explicit asset tools for asset work.

## Lifetime and recovery

All handles expire when a scene closes, opens, or is newly created, when a scene unloads, on domain reload or Editor restart, on every Play mode transition, and on Undo/Redo. Scene lifecycle and Undo/Redo invalidation are global, including handles for surviving objects in other loaded scenes. These boundaries apply even when Unity restores the same serialized identity or a similarly named object.

Direct rename/property changes, same-scene reparenting and ordinary save retain the original reference. A context mismatch observed during resolution permanently invalidates that handle: moving the object back does not revive it. An unobserved move to another scene and back may still resolve the same original reference after return; handles do not record every historical movement.

On `stale_handle`, rediscover the intended current object explicitly and capture a new handle. Do not retry with a guessed token or treat a replacement with the same name, path or serialized identity as the old object. Returned instance IDs and child links retain their existing ephemeral lifetimes.

## GameObject resource

Read bounded metadata with the same public resource URI:

```text
mcpforunity://scene/gameobject-by-handle/<handle returned by capture>
```

This resource accepts GameObject handles and returns `identity` plus the existing bounded `gameObject` metadata. Component handles are refused with `object_kind_mismatch`. The original GameObject resource policy still applies. The reference/lifetime is validated again after metadata serialization; a lifecycle change or destroyed target cannot return a successful identity read.

## Compatibility

The public tool alias and resource URI remain stable. Compatible Editors expose native commands `manage_object_handles_v2` and `get_gameobject_by_handle_v2`; older Editors fail closed without fallback or reload retry. Old v1 tokens are refused and must be recaptured.

Call the public `manage_object_handles` tool with its capture/resolve schema. The native command name `manage_object_handles_v2` is an implementation route and is not separately published as a custom MCP tool. Existing public capture/resolve calls and the resource URI are unchanged.

`require_persistent` and `allow_asset_load` are unsupported. Omit both fields entirely: explicit false is also an error before context selection, preflight or outbound dispatch. Their Python schema entries remain only to reject legacy callers explicitly; neither is a supported option. Capture accepts only `action` and `instance_id`; resolve accepts only `action` and `handle`.

Handles have no persistent scene/asset identity or asset dependency-revision guarantee. The registry writes no markers, sidecars, scenes or assets. Use explicit rediscovery after any expiry boundary.
