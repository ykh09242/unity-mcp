# Diagnosing a changed Sprite after re-slicing

Take the texture GUID and signed local fileID from the serialized reference or
an earlier `manage_sprite(action="get_geometry", path=...)` observation. A
texture may have multiple Sprites with the same GUID. Use the exact decimal
fileID string, including its sign. Inspect the loaded object by unique name.

```sh
unity-mcp sprite resolve-reference Assets/UI/Icons.png --guid GUID --file-id FILE_ID --scene-target RewardIcon
```

The corresponding MCP action is `manage_sprite(action="resolve_reference",
path=..., guid=..., file_id=..., scene_target=...)`. It loads the selected
asset's current imported objects and reads one loaded GameObject. The result
identifies the imported Sprite by GUID/fileID and gives its name and source
rectangle. `serialized_sprite` comes from the component's current serialized
`m_Sprite` property, and `assigned_sprite` from `SpriteRenderer.sprite` or the
uGUI Image's active `overrideSprite`. Both can be compared with the requested
identity and a previously recorded rectangle. The same fileID can retain its
identity while a re-slice changes the region it draws.

Passing `--expected-rect X Y WIDTH HEIGHT` (MCP field `expected_rect`) reports
`assigned_rect_matches` independently of the GUID/fileID match. A true identity
match and a false rectangle match identify an assigned Sprite whose current
texture region differs from the baseline.

`identity_matches` is null for missing fileIDs, an unavailable asset, inactive
or ambiguous targets and unavailable persistent Sprites. The action never opens
an unloaded scene. In the main stage, the name must be unique across all loaded
ordinary scenes, including inactive objects. While editing a prefab, only its
contents are searched. The response identifies the search scope and the scene
of a unique target. Component assignment alone leaves clipping, camera visibility
and final pixels unknown. Capture the affected frame for a visual conclusion.

Run `SpriteGeometryIntegrityTests.ResolveReferenceIdentifiesWrongSiblingAndUnavailableTarget`
in the disposable Unity test project for a same-GUID wrong sibling and an
unloaded target. On a graphics-enabled Editor using the built-in render pipeline
and the 2D Sprite package, explicitly run
`SpriteGeometryIntegrityTests.ResliceRetainsFileIdButChangesAndRestoresRenderedIcon`.
It creates a red/blue sheet, renders the first Sprite, moves its slice to blue,
reimports, reads the actual component binding, restores the original slice and
checks the rendered colors red, blue and red. The scene objects and imported
assets belong to that fixture.
