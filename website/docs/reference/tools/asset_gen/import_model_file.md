---
title: import_model_file
sidebar_label: import_model_file
description: "Import a local 3D model file that already exists within the Unity project's Assets folder (e.g. an FBX/OBJ/glTF exported from Blender or another DCC tool) into the Unity project."
---

# `import_model_file`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `asset_gen` &nbsp;·&nbsp; **Module:** `services.tools.import_model_file`

## Description

Import a local 3D model file that already exists within the Unity project's Assets folder (e.g. an FBX/OBJ/glTF exported from Blender or another DCC tool) into the Unity project. The file is copied under Assets/ and run through Unity's model-import pipeline (scale-normalize, material settings; glTF needs an active compatible importer such as glTFast). Carries no API keys and no file bytes over the bridge.

Params: source_path (Assets-relative path, or an absolute path physically within this Unity project's Assets, to a .fbx/.obj/.glb/.gltf/.zip; traversal and symbolic links/junctions are rejected), name, output_folder (under Assets/), target_size, animation_type. Success returns { asset_path, asset_guid, model_import_stage: 'usable' } after Unity loads a hierarchy containing indexed mesh geometry. This does not certify materials, rigs or rendering. Failed imports may retain staged bytes and return asset_path, asset_guid, model_import_stage ('saved' for bytes on disk or 'registered' for a GUID without usable geometry), and model_import_error_code. The original source is preserved. target_size applies only when Auto-Normalize Imported Models is enabled; finite zero or negative values preserve imported scale. Successful normalization of a model without ModelImporter may return a prefab variant while retaining its source model. A missing active glTF importer is 'missing_gltf_importer'; an active importer that produces no usable model is 'unusable_model'. Inspect the retained output and Console, repair or re-export, then reimport.

animation_type (FBX/OBJ only): pass 'generic' or 'humanoid' for a rigged/animated mesh so Unity surfaces its AnimationClips; omitted or 'none' imports no rig (this is the usual cause of a rigged FBX importing with zero clips); 'legacy' selects Unity's legacy Animation system (rarely needed). glTF/GLB ignore it — the selected glTF importer handles animation.

Core text glTF buffer/image sidecars and OBJ same-directory MTL files with simple PNG/JPEG texture maps are copied together into a fresh bundle folder, preserving relative paths and the original model basename. name labels that folder; a single-file import keeps its flat asset name. Dependencies must stay below the source directory without parent traversal, links, absolute/network paths or executable files. Limits include 256 files and 512 MiB combined file/embedded-data bytes. Unsupported text glTF extensions and MTL options or ambiguous filenames fail before staging; no reference is silently dropped.

dependency_bundle reports inspection, stage ('not_staged', 'partial', 'staged'), paths and copy counts. Partial output can remain after a write failure; staged does not mean usable. GLB core external resources are rejected: export core text glTF with its files or a self-contained GLB. Embedded GLB extensions are preserved with explicitly limited inspection. FBX dependencies are not scanned; ZIP keeps its existing archive import behavior.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `source_path` | `str` | yes | Assets-relative or absolute-within-Assets model file path (.fbx/.obj/.glb/.gltf/.zip). |
| `name` | `str \| None` | — | Asset base name for a single file; folder name for a dependency bundle, whose model basename is preserved. |
| `output_folder` | `str \| None` | — | Destination under Assets/. Dependency bundles use a fresh folder; partial or failed imports may retain output for repair. |
| `target_size` | `float \| None` | — | Largest default-pose mesh-bounds dimension in meters when Auto-Normalize Imported Models is enabled; finite zero/negative values preserve scale. Omitted defaults to 1. Normalized glTF may return a prefab variant. |
| `animation_type` | `Literal['none', 'generic', 'humanoid', 'legacy'] \| None` | — | FBX/OBJ only: rig/animation import mode. 'generic' or 'humanoid' surface the model's AnimationClips; 'legacy' selects Unity's legacy Animation system (rarely needed); omitted or 'none' imports no rig. Ignored for glTF/GLB. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

