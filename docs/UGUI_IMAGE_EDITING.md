# uGUI Image editing

`manage_ugui(action="set_image", target=..., properties={...})` edits one existing
`UnityEngine.UI.Image` in a loaded scene or the current prefab stage. It uses the
same target selectors, Edit Mode restriction, Undo transaction and prefab-instance
recording as the other uGUI setters. It never adds an Image or changes a source
prefab/sprite asset. Generic `manage_components` editing and `create-image` remain
available with their existing contracts.

```json
{
  "action": "set_image",
  "target": "Canvas/Progress",
  "properties": {
    "sprite": {"path": "Assets/UI/icons.asset", "name": "Progress"},
    "type": "Filled",
    "fillMethod": "Horizontal",
    "fillOrigin": 0,
    "fillAmount": 0.5,
    "preserveAspect": true,
    "raycastTarget": false
  }
}
```

The CLI equivalent is one request:

```sh
unity-mcp ui set-image "Canvas/Progress" --properties '{"type":"Filled","fillAmount":0.5}'
```

For a negative instance ID, place options before the standard Click argument
separator: `ui set-image --properties '{"fillAmount":0.5}' -- -123`.

## Supported properties

| Property | Contract |
| --- | --- |
| `sprite` | `null` clears only the base sprite; a path selects the only Sprite at that asset path; `{path,name}` selects one exact, case-sensitive sub-sprite name. |
| `type` | `Simple`, `Sliced`, `Tiled`, or `Filled`, case-insensitive enum names. |
| `fillMethod` | `Horizontal`, `Vertical`, `Radial90`, `Radial180`, or `Radial360`. |
| `fillOrigin` | Integer 0..1 for Horizontal/Vertical; 0..3 for radial methods. |
| `fillAmount` | Finite native Single value in 0..1; no silent out-of-range clamp. |
| `fillClockwise` | Boolean. |
| `preserveAspect` | Boolean; accepted only with final Simple/Filled type. |
| `fillCenter` | Boolean; accepted only with final Sliced/Tiled type. |
| `color` | Four finite 0..1 channels, using the existing uGUI color contract. |
| `raycastTarget` | Boolean. |

Fill fields other than `fillCenter` require final `Filled` type. Sliced editing
requires a non-null base sprite with a nonzero border. Unspecified fields use the
current component values when validating the final combination. Therefore a
request may set `type` and its dependent fields together, regardless of JSON key
order. Unknown properties, malformed scalar types and invalid combinations are
rejected before opening the Undo group or applying an earlier valid property.

Changing `fillMethod` can reset Unity's origin. This action applies method before
origin and preserves an unrequested origin when valid for the new method. If it
would be invalid, supply `fillOrigin` explicitly in the same request.

Sprite paths must be canonical `Assets/` or `Packages/` paths with no traversal,
backslashes, colon or NUL, limited to 1024 UTF-16 units. An optional name is
limited to 256 UTF-16 units. Zero or multiple matches fail; no first-match choice,
global search, texture importer changes, asset writes or explicit refresh/import
calls occur. Selection inspects at most 4096 returned sub-assets. Unity's initial
asset-loading call itself is not a hard work or elapsed-time budget.

## Readback and limits

Success returns `instance_id`, `path`, the requested `applied` property names and
an `image` object read from the component after the assignments. Sprite metadata
includes observed path/name, persistence, GUID and an Int64 local ID represented
as a string. Nonpersistent existing sprites have null GUID/local ID.

`image.overrideSprite` reports the public Unity getter; when there is no override,
Unity may return the base sprite. This is not an authored-override-presence flag.
The action does not change the override, so an existing override may continue to
control the displayed image after a base-sprite edit.

Readback describes stored Image properties, not rendered pixels, generated mesh,
input delivery or layout. Ordinary Undo rollback does not promise to reverse
arbitrary user callback side effects. Optional uGUI/property API absence is an
explicit failure; the tool does not install a package or substitute another
component. API documentation/source review is not minimum-version runtime
certification.

Scoped remote credentials require `edit`, through the existing shared public,
selected-plugin and final-native classification. This is operation admission,
not a sandbox for user callbacks.

API references: [Image](https://docs.unity3d.com/Packages/com.unity.ugui@1.0/api/UnityEngine.UI.Image.html),
[sub-assets](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/AssetDatabase.LoadAllAssetsAtPath.html),
[persistent identity](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/AssetDatabase.TryGetGUIDAndLocalFileIdentifier.html),
and [prefab instance changes](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/PrefabUtility.RecordPrefabInstancePropertyModifications.html).
