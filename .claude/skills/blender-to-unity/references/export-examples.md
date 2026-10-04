# Conditional Blender Export Examples

These adapt the existing Blender operator recipes; inspect the connected Blender version/operator options before executing them. Use the client's actual code-execution capability only if available and authorized. Do not assume a provider-prefixed tool name.

## Static Geometry FBX

Replace `assets_root` with the verified target Unity Assets directory, accessible from the Blender host. This example fails on an occupied output rather than overwriting it. Check physical containment/no linked directories before export; string joins alone do not prove a safe path.

```python
import bpy, os
assets_root = "/verified/UnityProject/Assets"
out = os.path.join(assets_root, "ModelHandoff", "StaticModel.fbx")
if os.path.exists(out):
    raise RuntimeError("Choose a new output filename")
os.makedirs(os.path.dirname(out), exist_ok=True)
bpy.ops.export_scene.fbx(
    filepath=out,
    use_selection=True,
    apply_unit_scale=True,
    bake_space_transform=True,
)
print(out)
```

Confirm the intended nonempty selection first. Static modifier baking can be appropriate; for rigs/shape keys do not blindly reuse these apply settings. Exporting an entire scene instead is an explicit choice, not the default recovery for an empty selection.

## Active Scene GLB

Use only if the target Unity project supports glTFast. This exports the active scene; choose a supported selection option instead when the request is selection-only.

```python
import bpy, os
assets_root = "/verified/UnityProject/Assets"
out = os.path.join(assets_root, "ModelHandoff", "ActiveScene.glb")
if os.path.exists(out):
    raise RuntimeError("Choose a new output filename")
os.makedirs(os.path.dirname(out), exist_ok=True)
bpy.ops.export_scene.gltf(
    filepath=out,
    export_format="GLB",
    use_active_scene=True,
    export_apply=False,
)
print(out)
```

Check exported object count/size and the actual file before import. For rigs/morph targets, applying modifiers can discard deform/morph data; static exports and baked geometry have different needs.

## Material Recovery

The [historical fidelity reference](bridge-fidelity.md) distinguishes exported data from how Unity's chosen shader displays it. Inspect node/image/vertex-color inputs and actual imported materials before changing them.

For a procedural material that must become portable, a conditional bake workflow is: choose the requested objects, ensure UVs, assign an active target image texture, bake the relevant color/emission channel, then connect/pack that image for export. Baking changes Blender state and can be costly; use it only within the user's request.

For FBX material recovery, compare source emission/base-color values with Unity's imported shader properties. Extract/edit only the intended materials when required and preserve unrelated shared assets. There is no universal emission-strength multiplier, render queue or Bloom configuration that guarantees a match.
