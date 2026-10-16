# AI Asset Generation — Manual Verification Checklist

Live provider behavior requires genuine provider keys and a licensed interactive
Unity Editor. Run the provider sections below manually before shipping. Isolated
native tests cover local model import with tiny owned OBJ fixtures and fake provider
transports; those tests do not verify live services or optional glTF import success.

Job status metadata retains the 100 most recently finished jobs (done, failed, or
canceled), plus every active job. This history survives an unrelated domain reload;
older finished job IDs become unknown. Eviction removes only status metadata and
never deletes generated assets or output files.

## Prerequisites

- [ ] A licensed Unity Editor with the package installed and the bridge connected.
- [ ] Enable the group: `manage_tools` → enable `asset_gen` (it is off by default).
- [ ] Open **Window → Unity MCP (ykh09242) → Asset Gen** tab to enter provider keys
      (stored in the OS secure store — Keychain / Windows Credential Manager / libsecret).

## Tripo (default 3D, text→3D)

- [ ] Enter the Tripo key in the **Asset Gen** tab.
- [ ] Ensure the optional GLB importer is available as described below.
- [ ] `generate_model(provider=tripo, mode=text, prompt="a low-poly oak tree", format=glb)`.
- [ ] Poll `generate_model(action=status, job_id=<id>)` until it reports done.
- [ ] Confirm a GLB appears under `Assets/Generated/Models/`, status reports a `.glb` asset path,
      and the imported model has usable geometry and materials.
- [ ] Repeat with `texture=false` and `model=v3.1-20260211`; confirm the request produces
      geometry without textures. The adapter sends both `texture=false` and `pbr=false`.
- [ ] Request `format=fbx`, `format=obj`, or a nonblank `tier`: confirm a synchronous
      unsupported-option error, no job ID, no provider submission, and no output file.
- [ ] Request `texture=false` with `model=v1.4-20240625`: confirm the same early rejection.

Tripo generation defaults to GLB with textures and no tier. Geometry-only generation is
supported here for `v3.0-20250812`, `v3.1-20260211`, and `P1-20260311`. Other formats need
Tripo's separate conversion task; this integration does not submit it. A GLB container
check before saving does not establish usable mesh content or installed importer support.

## Model import evidence

A model is `usable` only after Unity loads a GameObject hierarchy containing a
MeshFilter or SkinnedMeshRenderer with vertices and an indexed primitive. This is
basic geometry evidence, not a guarantee of materials, rigs, topology quality or rendering.

Direct `import_model_file` responses and terminal model job status include
`data.model_import_stage`:

- `saved`: bytes exist on disk; no registered GUID has been observed.
- `registered`: a GUID exists, but usable model geometry has not been established.
- `usable`: the final imported hierarchy passed the geometry check.

An import failure can retain staged bytes and return `asset_path`, `asset_guid`,
`model_import_stage` and `model_import_error_code` for repair. The original local
source is preserved. A failed import does not claim placement or a usable derived asset.
A corrupt model can have a GUID and still fail with `unusable_model`.
If bounded hierarchy/mesh inspection cannot finish, `model_validation_limit` means
unverified rather than usable; simplify or split the retained model and reimport.

- [ ] Import a tiny valid OBJ with `import_model_file` and confirm success,
      `model_import_stage=usable`, a loadable hierarchy and actual mesh geometry.
- [ ] Import a syntactically supported but mesh-less OBJ (an object declaration and one
      vertex, without a face) directly and from a ZIP. Confirm failure with retained staged
      path/GUID and `registered` evidence; confirm original source bytes are unchanged.
      Unsupported direct OBJ dependency syntax instead fails before staging.
- [ ] Repair or re-export the model, inspect Unity's Console, and reimport; do not use
      a saved file or GUID alone as proof that import succeeded.

## Local model dependency bundles

Direct `import_model_file` discovers core text glTF buffer/image references and
OBJ `mtllib` references. MTL files must be beside the OBJ; unoptioned texture-map
references may use subdirectories beneath that source folder. Supported buffer
files are `.bin`, `.glbin`, `.glbuf`; textures are PNG/JPEG. Recognized base64
buffer/PNG/JPEG data URIs stay embedded and count toward the byte budget.

Multi-file inputs use a fresh destination directory named by `name`, preserving
the original model basename, relative layout and source bytes. Existing outputs
are not merged or overwritten; source metadata is not copied or rewritten.

The supported subset rejects traversal, links/junctions, absolute/network paths,
query/fragment syntax, ambiguous names, executable dependencies, text glTF
extensions, nested MTL locations, map options, quoted/escaped-space filenames,
continuations and unknown resource directives. Limits are 256 files, 1024
references, 512 MiB combined file/embedded bytes, 16 MiB JSON, 4 MiB per MTL,
32 MiB total JSON/MTL metadata, JSON depth 64 and 64 KiB per text line. Relative
paths have 1024 characters, 32 segments and 255 UTF-8 bytes per segment.

`data.dependency_bundle` distinguishes staging from import:

- `inspection=complete` covers the supported dependency forms; it does not
  certify materials, rigs or rendering.
- `stage=not_staged` means this operation copied no model bytes. Preflight
  failures create no output.
- `stage=partial` reports retained files after a write/consistency failure.
  `staged_file_count` counts completed copies; `staged_bytes` includes observed
  retained partial bytes.
- `stage=staged` means all planned bytes were copied. The separate
  `model_import_stage` still decides whether usable geometry exists.
- GLB inspection covers core buffer/image resources only. Known external core
  references fail before staging: export core text glTF with its files or a
  self-contained GLB. Embedded extensions such as `KHR_materials_unlit` stay
  intact and report `inspection=not_scanned` and
  `inspection_scope=glb_core_resources_only`.
- FBX reports `not_scanned`; ZIP reports `archive_managed`. Existing archive
  extraction is unchanged. Neither label promises a full dependency audit.

Missing dependencies, unsafe paths, unsupported forms and limits have distinct
preflight error codes. Source changes/write failures retain partial evidence.
Importer failure after complete staging retains the bundle and GEN-03's
saved/registered path, GUID and error evidence.

- [ ] Import owned OBJ + MTL + PNG into a fresh folder. Inspect actual indexed
      mesh, renderer material and the bound texture's asset path. It must point
      inside the staged bundle, not to the original or a same-name decoy.
      Compare source/output bytes and repeat to verify collision isolation.
- [ ] Import core glTF + BIN + PNG and compare copied paths/bytes. Without an
      active compatible importer, require actual `missing_gltf_importer` failure
      and retained bundle evidence; copying alone is not successful glTF import.
- [ ] Only in an authorized environment with an already installed compatible
      glTF importer, check actual mesh/material/texture connections and record
      its version. Do not manufacture a hierarchy or mark an unavailable branch
      passed.
- [ ] Reject external-core GLB before staging; preserve an embedded GLB with
      `KHR_materials_unlit` and report limited inspection even if import fails.
- [ ] Check missing/unsafe sidecars, source changes, limits and later-copy
      failure. Confirm no preflight output, accurate partial evidence, no
      overwrite and unchanged source/pre-existing files.

## glTFast / GLB

- [ ] Install **glTFast** from the **Dependencies** tab.
- [ ] `generate_model(provider=tripo, mode=text, prompt="...", format=glb)`, poll status.
- [ ] Confirm the GLB imports correctly and reports `model_import_stage=usable`.
- [ ] In a project without an active glTF importer, import a valid GLB container and
      confirm `missing_gltf_importer`, retained staged output and no success claim.
- [ ] With an active importer, try a model that imports without usable geometry:
      confirm `unusable_model` and an instruction to inspect/repair/reimport.

Importer availability is determined from the importer Unity actually selected and
the resulting model, not just a runtime assembly being loaded. A compatible alternate
importer can supply usable geometry. A valid downloaded GLB container alone does not
establish importer availability or usable model content.

## Automatic model discovery

- [ ] Open Asset Generation: search live fal image/audio/3D and OpenRouter image choices; catalogs refresh every 24 hours while open.
- [ ] Run `unity-mcp asset-gen list-models --kind model --provider fal --refresh`; repeat while `catalogs[].refreshing` is true. Use `--search`, `--mode`, `--limit` and `--offset` to browse.
- [ ] Confirm failed refreshes preserve cached choices, missing models preserve the saved ID, and background refreshes preserve unsaved key input.
- [ ] Set `MCPFORUNITY_RUN_LIVE_CATALOG=1` and run both catalog fixtures' `LivePublicCatalog*` tests. They check public discovery and compatibility without paid generation.

## fal.ai (default 2D image)

- [ ] Enter the fal key.
- [ ] `generate_image(provider=fal, prompt="a pixel-art coin", transparent=true)`.
- [ ] Confirm a PNG sprite under `Assets/Generated/Images/` with **alpha** preserved
      and **correct sRGB** color.

## Image background removal availability

Background removal is unsupported in this version. Image provider capabilities
advertise `text` and `image` generation modes; `remove_background` is excluded from
the supported action enum and native supported-action list.

Existing direct Python/native calls using `action=remove_background` receive an
explicit failure with `data.reason=unsupported_capability`,
`data.capability=remove_background` and `data.supported=false`. Python rejects the
request before Unity instance resolution or transport dispatch; native rejection
precedes provider lookup, job creation and output writes. Source images are not
modified. MCP SDK clients reject the removed action during enum validation before
dispatch; that validation error is separate from the direct-call error envelope.

Use `generate` for supported text-to-image or image-to-image work. The
`transparent` option only controls Unity's `TextureImporter.alphaIsTransparency`
flag. It preserves available alpha during import and does not generate or remove
a background. It does not enable the unsupported operation.

## OpenRouter (2D image)

- [ ] Enter the OpenRouter key.
- [ ] `generate_image(provider=openrouter, prompt="...")`.
- [ ] Confirm the inline-image path works (image bytes decode and import as a sprite).

## Sketchfab (3D import)

- [ ] Enter the Sketchfab token.
- [ ] `import_model(action=search, query="wooden chair")`, then
      `import_model(action=import, uid=<from search>)`.
- [ ] Confirm the downloaded zip extracts and the model imports.
- [ ] Confirm the **path-traversal guard** holds (no files written outside the target dir).

## Meshy (3D)

- [ ] Enter the Meshy key.
- [ ] `generate_model(provider=meshy, mode=text, prompt="...")`, poll status.
- [ ] Confirm the model imports.

## Image input & provider params (verify the post-review fixes)

These paths are covered by unit tests at the request-shaping layer only — confirm them against
**real provider APIs** (the unit tests can't validate that the provider accepts the shape).

- [ ] **Meshy text→3D textures:** `generate_model(provider=meshy, mode=text, prompt="...", texture=true)`
      → confirm the result is **textured** (Meshy runs a preview then a refine task internally).
- [ ] **Local image→3D (Meshy):** `generate_model(provider=meshy, mode=image, image_path=Assets/refs/x.png)`
      → confirm it imports a model derived from the local image.
- [ ] **Local image→image (fal):** `generate_image(provider=fal, mode=image, image_path=Assets/refs/x.png, prompt="make it night")`
      → confirm fal's `/edit` endpoint accepts the request and returns an edited image.
- [ ] **Local image→image (OpenRouter):** `generate_image(provider=openrouter, mode=image, image_path=..., prompt="...")`
      → confirm the reference image influences the result.
- [ ] **fal output size:** `generate_image(provider=fal, width=512, height=512, prompt="...")`
      → confirm the returned image is 512×512.
- [ ] **Tripo local image is rejected clearly:** `generate_model(provider=tripo, mode=image, image_path=...)`
      → confirm the job fails with a clear "Tripo requires a hosted image_url" message (no silent text fallback).
- [ ] **Sketchfab search filters/paging:** `import_model(action=search, query="chair", categories=furniture-home, count=12, cursor=<from prior cursors.next>)`
      → confirm filtering works and `cursors.next` advances the page.
- [ ] **Transparency is import-only:** `generate_image(transparent=true)` sets the Unity alpha-is-transparency
      flag but does NOT produce a transparent background (fal/FLUX limitation) — confirm the expectation.

## Multi-agent / security spot-check

- [ ] Confirm no key value ever appears in MCP tool output.
- [ ] Confirm no key value appears in logs.
- [ ] Confirm no key value appears in the job `status` payload.
- [ ] Confirm no key value is committed to git.
