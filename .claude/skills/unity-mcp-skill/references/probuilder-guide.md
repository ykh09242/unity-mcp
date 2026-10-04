# ProBuilder Topology Workflows

Use ProBuilder when the request needs editable mesh topology and `com.unity.probuilder` is already installed. It is optional, not a reason to replace ordinary primitives or the user's modeling workflow. Discover `manage_probuilder` and check `action="ping"`; installation is a separate requested action.

## Create And Inspect

```json
{"tool":"manage_probuilder","params":{"action":"create_shape","properties":{"shape_type":"Cube","name":"EditableBlock","width":2,"height":1,"depth":2}}}
```

Retain `data.instanceId` from creation and use it as the target instead of a guessed name. Inspect faces before selecting them:

```json
{"tool":"manage_probuilder","params":{"action":"get_mesh_info","target":"12345","search_method":"by_id","properties":{"include":"faces"}}}
```

Faces include `index`, `normal`, `center`, `direction`, smoothing and UV metadata. Direction can be null for non-axis-aligned faces. Choose by geometry, not the sample index. Face output is capped at 100 and edge output at 200; check `truncated`/`edgesTruncated` before assuming the whole mesh is present.

## Edit And Reinspect

A top-face extrusion, after replacing index 0 with the actual selected face:

```json
{"tool":"manage_probuilder","params":{"action":"extrude_faces","target":"12345","search_method":"by_id","properties":{"faceIndices":[0],"distance":0.5,"method":"FaceNormal"}}}
```

Re-query after topology changes: indices are not stable across extrude/subdivide/delete. For a doorway or roof, inspect the resulting geometry at each dependent step rather than reusing a hardcoded house recipe. `subdivide` uses ProBuilder's connecting operation; do not assume conventional quad subdivision.

Per-face material assignment:

```json
{"tool":"manage_probuilder","params":{"action":"set_face_material","target":"12345","search_method":"by_id","properties":{"faceIndices":[0],"materialPath":"Assets/Materials/Accent.mat"}}}
```

Verify the material exists and select the intended faces; an empty face list can mean all faces for some actions, not a harmless no-op.

## Smoothing, Pivots And Validation

```json
{"tool":"manage_probuilder","params":{"action":"auto_smooth","target":"12345","search_method":"by_id","properties":{"angleThreshold":30}}}
```

Choose the threshold for the desired hard/smooth edges. Manual groups use `set_smoothing` with inspected face indices and `smoothingGroup`. Pivot/transform operations alter editing behavior: center/freeze only when requested or needed, not as routine cleanup.

Use `validate_mesh` and inspect the result before requesting `repair_mesh`; repair can modify topology. `set_pivot` and `convert_to_probuilder` are supported actions with compatibility handling, not categorically broken APIs. Reflection/package-version failures should be surfaced, not hidden by success assumptions or an unrequested package upgrade.

For repetitive independent creation, batches can reduce round trips; dependent face edits need the preceding returned topology. Inspect native errors and per-command results. Finally capture the relevant scene view and compare shape, transforms, normals and material slots; an API acknowledgement is not a visual verification.
