# AI Asset Generation — Manual Verification Checklist

The `asset_gen` tools call real third-party APIs and write real files into a licensed
Unity Editor, so they **cannot be covered headlessly**. Run this checklist by hand with
genuine provider keys and an interactive Editor before shipping.

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

## glTFast / GLB

- [ ] Install **glTFast** from the **Dependencies** tab.
- [ ] `generate_model(provider=tripo, mode=text, prompt="...", format=glb)`, poll status.
- [ ] Confirm the GLB imports correctly (no missing-importer error).

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
