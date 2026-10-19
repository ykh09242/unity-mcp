# Material render state

`manage_material` exposes `get_render_state` and `set_render_state`. Reads require
read permission; edits require edit permission at both public and native admission.
These actions complement the existing material-property and renderer-color actions.

Address exactly one material asset with `material_path`, or a renderer with `target`,
optional `search_method`, and a zero-based `slot` (default `0`). Reads use
`Renderer.sharedMaterials` and never instantiate a material. Asset addresses cannot
also contain a renderer address, slot or search method.

Edits require an explicit `mode`:

- `shared` changes the selected material itself, intentionally affecting all its users.
  Persistent shared edits are limited to writable project `Assets` paths.
- `instance` clones only the selected renderer slot and assigns the clone through
  `sharedMaterials`. Other slots and renderers keep their original references.
  An asset address cannot use this mode.

`property_block` and `create_unique` are unsupported for these actions. The complete
patch validates before any material creation, assignment or mutation. Successful
edits form one Undo group; Undo restores the material state or original renderer
reference and removes a created instance.

Example MCP arguments:

```json
{
  "action": "set_render_state",
  "target": "Example/Cube",
  "search_method": "by_path",
  "slot": 0,
  "mode": "instance",
  "render_state": {
    "render_queue": 3001,
    "keywords": { "EXAMPLE_DECLARED_KEYWORD": false },
    "shader_passes": { "UniversalForward": false },
    "texture_transforms": {
      "_BaseMap": { "scale": [2, 2], "offset": [0, 0] }
    }
  }
}
```

The example's keyword, pass and texture must actually exist in the selected shader.
Use `get_render_state` first. `render_state` may also be a JSON object string. Native
transport uses `renderState` for this outer argument and the same nested field names.

The closed patch supports:

| Field | Accepted input | Meaning |
| --- | --- | --- |
| `render_queue` | Strict integer `-1` or `0..5000` | Set the override; `-1` restores the shader queue. Readback reports Unity's effective queue. |
| `keywords` | Declared shader keyword name to strict boolean | Set local material state; never create or mutate global keywords. |
| `shader_passes` | Active SubShader `LightMode` tag to strict boolean | Set the material pass flag; tag lookup is case insensitive. |
| `texture_transforms` | Exact declared texture property to `scale` and/or `offset` | Each vector contains exactly two finite numbers. |
| `render_mode` | `opaque`, `cutout`, `fade`, `transparent` | Explicit Built-in Standard preset; resets the queue and couples mode-dependent state. |

Unknown fields, undeclared keywords, display pass names, untagged pass identifiers,
non-texture properties, null edits, numeric booleans, duplicate case aliases and
oversized maps are rejected. A map has at most 128 entries, and keys have at most
256 characters. Texture property names are exact and have no alias expansion.

Readback contains material/renderer instance identity, asset path/GUID and persistence,
pipeline and shader identity, effective queue and shader queue, keywords, pass flags
and UV transforms. Keyword rows separate `local_enabled`, `is_overridable`,
`global_enabled` and `effective_enabled`. An overridable enabled global keyword can
keep the effective keyword enabled after a local false edit. No global flag changes.

Pass rows include a display name for inspection, but mutation keys are `LightMode`
tags. Repeated tags share one enable flag; untagged passes are explicitly marked
uncontrollable and have null enablement. Flags do not prove that a render pipeline
actually draws a pass. Only the active SubShader is inspected.

Keyword/pass output is limited to 128 entries with counts and truncation flags.
Texture output scans the first 128 shader properties and explicitly reports an
incomplete scan. This is bounded evidence, not a claim to enumerate every shader
variant, SubShader, renderer property block, hidden property or compiled GPU state.

## Pipeline and render-mode coupling

Raw queue/keyword/pass/UV edits do not configure an opaque or transparent material.
Blending, depth writes, tags and shader-specific mode properties can be coupled by
the shader's custom inspector. `render_mode_coupling` reports support separately
from observed mode/blend/depth properties, RenderType tag and alpha keyword state.
Support does not imply that manually edited state is coherent with a mode preset.

An explicit `render_mode` supports only the active Built-in pipeline with the exact
`Standard` or `Standard (Specular setup)` shader and the required float/keyword
declarations. It applies the corresponding StandardShaderGUI mode rules directly,
without running an arbitrary custom inspector:

| Preset | `_Mode` | Source / destination blend | `_ZWrite` | RenderType override | Local alpha keyword enabled | Queue assigned |
| --- | --- | --- | --- | --- | --- | --- |
| `opaque` | 0 | One / Zero | 1 | empty | none | -1 (shader default) |
| `cutout` | 1 | One / Zero | 1 | TransparentCutout | `_ALPHATEST_ON` | 2450 |
| `fade` | 2 | SrcAlpha / OneMinusSrcAlpha | 0 | Transparent | `_ALPHABLEND_ON` | 3000 |
| `transparent` | 3 | One / OneMinusSrcAlpha | 0 | Transparent | `_ALPHAPREMULTIPLY_ON` | 3000 |

Each preset disables the other two owned alpha keywords. Preset application
intentionally resets an existing queue override to the documented default. A patch
cannot also contain `render_queue` or edit any of the three owned alpha keywords;
such conflicts fail before mutation. Independent declared keyword, pass and UV
edits can coexist after whole-patch validation. The Standard preset does not change
pass enable flags, unrelated shader keywords, textures, colors or alpha cutoff.
All coupled state belongs to the same shared/instance Undo transaction.
The opaque preset clears the RenderType override; effective `render_type` readback
then comes from the Standard shader's `Opaque` tag.
An active overridable global alpha keyword that would defeat a preset's disabled
local state also causes a preflight rejection; global flags are never rewritten.

URP, HDRP and custom shader presets are explicitly unsupported. The inspected native
verification environment has no installed URP/HDRP package; no package version or
adapter behavior is inferred. Generic raw state editing is still available. This
tool does not run ShaderGUI, guess package versions or alter global keywords.

Unity documents that an SRP material can use its default SubShader until first used
by a batch; queue observation follows Unity's current initialization state. Inspect
again after rendering when initialization matters.

The API floor is Unity 2021.3. The owned test shader requires no optional render
pipeline package. Runtime rendering and pipeline preset behavior need separate
evidence from source compilation and raw state readback.

## Primary API references

- [Unity 2021.3 Shader API inventory](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Shader.html)
- [Unity 2021.3 Shader bindings, including active-SubShader pass tags](https://github.com/Unity-Technologies/UnityCsReference/blob/2021.3/Runtime/Export/Shaders/Shader.bindings.cs)
- [Unity 2021.3 Material.SetKeyword](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Material.SetKeyword.html)
- [Unity 2021.3 StandardShaderGUI preset rules](https://github.com/Unity-Technologies/UnityCsReference/blob/2021.3/Editor/Mono/Inspector/StandardShaderGUI.cs#L312)
- [Unity 6 StandardShaderGUI preset rules](https://github.com/Unity-Technologies/UnityCsReference/blob/6000.0/Editor/Mono/Inspector/StandardShaderGUI.cs#L318)
- [Unity 2021.3 Material.SetShaderPassEnabled](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Material.SetShaderPassEnabled.html)
- [Unity 6 Material.renderQueue initialization caveat](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Material-renderQueue.html)
- [Unity Material.SetTextureScale](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Material.SetTextureScale.html)
