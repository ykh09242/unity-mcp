# Model size normalization

`target_size` is the largest dimension of the imported model's default-pose
mesh-bounds envelope, in Unity units (meters). The measurement includes root and
child translation, rotation, mirrored scale and nonuniform scale, including inactive
children. It is not a tight vertex bound or an animation/deformation envelope.
Skinned meshes use their stored mesh bounds; bones and blend shapes are not baked.

## Preference and values

| Input | Auto-Normalize Imported Models enabled | Preference disabled |
| --- | --- | --- |
| Positive finite target | Normalize and verify final dimensions | Keep imported dimensions |
| Zero or negative finite target | Keep imported dimensions | Keep imported dimensions |
| NaN or infinity | Reject | Reject |

Omitting the value retains the existing defaults: one meter for `import_model_file`,
`generate_model` and marketplace `import_model`; zero for Blender's `import_model`.
The preference is the global opt-out, including when a positive target is explicit.

Two intentional compatibility changes make the paths consistent: generated and
marketplace jobs no longer turn zero/negative targets into one meter, and Blender
placement no longer overrides a disabled preference or performs a second resize.
Blender's `scale_factor_applied` describes placement and is now one; import may have
already normalized the returned asset. An unplaced asset and its placed instance
start with the same dimensions.

## Assets and references

FBX/OBJ keep their imported asset path and GUID. Normalization preserves the
importer's file-unit policy, adjusts its global scale and reloads the final result.
Source file bytes are not rewritten. Repeating the same target does not accumulate
scale drift within the verification tolerance.

A usable model imported without `ModelImporter`, such as supported glTF, is normalized
through a prefab variant when a size adjustment is needed. The returned `asset_path`
and `asset_guid` identify that variant. The original imported model, meshes,
materials and clips remain referenced; they are not copied into new mesh assets or
rewritten. The fresh sibling directory is `<sourceBase>_Normalized[_N]`, containing
`model.prefab`; existing assets are not overwritten. Already-sized models do not need
a variant. Unity must support the imported hierarchy as a prefab source; otherwise
normalization fails with retained original evidence.

For local dependency bundles, `dependency_bundle.main_asset_path` still identifies
the staged model bytes and can differ from the normalized `asset_path`. Existing
references to the original model retain its original size. Use the returned path
when instantiating the normalized result. Blender resolves the original model for
embedded animation clips without inserting an extra transform into clip paths.
Manual edits or later reimports of the original source can change variant dimensions;
there is no automatic renormalization postprocessor.

## Verification and retained failures

Success checks the final imported hierarchy and largest measured dimension within
`max(0.00001, target_size * 0.001)` units. Traversal stops with an explicit failure
beyond 10,000 nodes, depth 256 or 10,000 mesh contributions. It does not allocate a
whole hierarchy component array or accept a partial bound. A positive target requires
a finite nonzero starting dimension above 0.000001. Built-in importer scale must fit
0.0001 through 1,000,000; requests are rejected instead of silently clamped.

Failure codes include `normalization_bounds_limit`, `normalization_invalid_bounds`,
`target_size_out_of_range`, `target_size_not_achieved`,
`normalization_variant_unsupported`, `normalization_output_conflict` and
`normalization_variant_failed`. A failed operation can retain the source or a saved
variant. Inspect `asset_path`, `asset_guid`, `model_import_stage` and
`model_import_error_code`; Blender also preserves this importer failure data and any
local dependency-bundle evidence. A registered GUID alone does not establish a
completed normalized import. Repair the retained asset or use a nonpositive target
to keep its imported scale.

Missing optional glTF importers still produce `missing_gltf_importer` before any
normalization variant. Generic scripted-importer fixture tests are not evidence of
actual glTF importer support. No importer is installed automatically.

## Manual checks

- Import a model with two offset mesh children and nonuniform child scale using
  `target_size=2`. Instantiate the returned asset and measure its largest static
  mesh-bounds dimension; repeat with a rotated child and confirm the target remains two.
- Disable Auto-Normalize Imported Models and repeat: imported dimensions stay unchanged,
  whether Blender placement is enabled or disabled. Restore your preferred setting.
- Repeat with zero and a negative target, then with a built-in model importer using
  file-unit conversion. Confirm source bytes/GUID and file-unit policy are retained.
- With an already-installed compatible glTF importer, confirm the returned variant's
  source model, mesh/material/texture references and embedded clips survive reload.
  Do not treat the absence of such an importer as a passing glTF-success check.
