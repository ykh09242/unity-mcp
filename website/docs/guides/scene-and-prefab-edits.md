---
title: Scene and Prefab Edits
description: Review scene changes, create a prefab from an explicit object, and apply guarded prefab edits.
---

Select the intended Editor before editing. Verify the loaded scene, object identity and destination
asset path. The JSON examples on the linked tool pages are request arguments; replace project paths,
object IDs and returned tokens with values from that Editor.

## Preview scene properties and hierarchy

Use [`manage_scene_edits`](../reference/tools/core/manage_scene_edits.md#examples) in Edit Mode.
Its property example moves an existing `Player` Transform, matching the
[scene-edit workflow](https://github.com/ykh09242/unity-mcp/blob/beta/docs/play-scenario-workflows.md#preview-scene-edits-and-apply-one-undo-group).
Inspect the returned before/after values before calling `apply` with its `preview_id`. The token is
valid for ten minutes and one attempt; drift, expiry or a failed attempt requires a new preview.
Do not send the token and the original edit list together. Direct property apply instead requires
`expected_old` on every edit.

Structural previews use an integer ID for a scene-root scope and ordered operations. The example
creates a Transform-only child, then a grandchild using an earlier temporary ID. Only ordinary
Transform-only subtrees are supported; prefab instances and objects with other components are
refused. Inspect the preview and pass its token to `apply_structure`. Each successful apply records
one named Undo group. Save the scene explicitly after reviewing the result; Undo does not provide
filesystem or callback rollback.

## Create a prefab from one object

[`prefab_create`](../reference/tools/prefabs/prefab_create.md#examples) creates an asset from an
existing scene or prefab-stage object. Use a verified integer instance ID or an unambiguous full
hierarchy path. Numeric strings also mean instance IDs. Current prefab-stage names and paths take
precedence over the active scene, so an ID is preferable when both contain similarly named objects.

Choose a new path such as `Assets/Prefabs/Crate.prefab`. `allow_overwrite` defaults to false. If the selected object
belongs to a prefab instance, review its exact unpack scope before opting into `unlink_if_instance`;
the selected object must be its outermost instance root. A nested object cannot authorize unpacking
its outer instance and siblings. After a failed save, inspect the returned error and the scene and
destination before retrying. If unpacking occurred, the error advises Undo if available or manual
restoration. Retain the source object until the saved asset is verified.

The result includes `rootObjectName` and `rootObjectPath` from the saved asset, whose root name can
differ from the scene instance. Use those values for later asset edits, or omit `target` to edit the
asset root. Scene instance IDs do not select objects inside isolated prefab contents.

## Preview changes to an existing prefab

[`prefab_contents`](../reference/tools/prefabs/prefab_contents.md#examples) offers a guarded root
Transform/active-state preview and token apply. The example uses `preview_modify_contents` and
`apply_preview`. This route accepts supported prefabs without nested instances or custom components;
it rejects source or dependency drift before saving. The apply token is valid for ten minutes and
one attempt. Supply only that token and the action to apply it.

For broader component or child edits, `modify_contents` with `preview_only: true` inspects isolated
contents without saving. That preview returns no apply token. Inspect its unsupported fields and
truncation indicators, then review a separate save request; save callbacks and a later run may differ.
Prefab asset saves do not promise Undo or rollback of callback side effects. See the
[legacy prefab examples](../reference/tools/legacy/manage_prefabs.md#examples) for returned-root
targeting and the full compatibility surface.

## Save a scene to another path

[`manage_scene`](../reference/tools/legacy/manage_scene.md#examples) saves the active scene normally
when no other destination is selected. Save As to another existing scene requires
`allow_overwrite: true` after reviewing that destination. The CLI equivalent is
`unity-mcp scene save --path Assets/Scenes/Existing.unity --allow-overwrite`.
