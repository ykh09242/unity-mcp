# Current uGUI camera projection

`manage_ugui(action="diagnose_projection", target=..., camera=...)` reads current camera and world-quad geometry without rendering, forcing layout, moving a camera, resizing a target or changing scene data. Enable the `ui` tool group to use it. The tool retains mixed read/write annotations because other `manage_ugui` actions mutate UI.

For a **World Space** Canvas, supply an explicit camera GameObject name, hierarchy path or instance ID. The Canvas-assigned camera can be an event camera and does not select a rendering view. The result records both IDs. For **Screen Space - Camera**, omission selects only the camera currently returned by the effective root Canvas's `worldCamera` getter. An explicit camera must be that same camera. This is a current Unity observation, not a guarantee that the getter returns the last assigned value. The tool performs no separate `Camera.main`, first-camera or current-camera search. The new action refuses Screen Space - Overlay; existing `diagnose` remains the separate Overlay layout-preview action.

```python
result = await manage_ugui(
    ctx,
    action="diagnose_projection",
    target="WorldHUD",
    camera="GameplayCamera",
    max_nodes=200,
    max_depth=10,
    max_evaluations=20000,
    max_characters=65536,
)
```

Targets and cameras use strict existing scene/prefab-stage lookup. Ambiguous names refuse; an explicit hierarchy path or current instance ID can disambiguate. Diagnostic paths and instance IDs in this artifact are current observations, not UI snapshot identities or persistent mutation handles. UI snapshot identity maps do not bind projection requests.

## Evidence and coordinates

The companion artifact is `kind="ugui_projection_diagnostics"`, `schema_version=1`. Its evidence identifies current unforced layout, copied CPU camera matrices in column-major order, camera-local normalized viewport coordinates and bottom-left target pixels. A hypothetical resolution, device profile, DPI or safe-area input is not accepted. Current Canvas scale is already present in the observed world corners and is not applied twice.

Each emitted RectTransform has its four ordered 3D world corners, original camera-depth range, camera-plane relation, frustum relation, named cut/reject planes and a surviving viewport polygon. Clipping uses all six current frustum planes before perspective division. A known empty clip is `outside`; zero-area geometry is `degenerate`; a surviving cut polygon is `partial`; unsupported or unfinished geometry is `unknown`. Original corners behind the camera never produce mirrored unclipped screen bounds. Small numerical viewport drift up to 1e-6 can clamp to the boundary and sets `boundary_clamped`; it is not a pixel or overlap tolerance.

For an ordinary created 2D RenderTexture, target pixels are available only when the current `camera.rect` is exactly `(0, 0, 1, 1)` and the other pixel-route checks pass. That route uses the full texture and retains `camera_rect_ignored=true`. This field records the selected diagnostic route; it does not establish how every native coordinate API treats camera rect/pixelRect.

A RenderTexture with any other camera rect retains valid normalized geometry but returns `status="partial"` with output `pixel_status="unavailable"`, `viewport_pixels=null` and `camera_rect_ignored=null`. Node `clipped_target_pixel_bounds` values are null. The observed camera rect/pixelRect remain available as context; the tool does not infer a full-texture mapping or a replacement pixel formula. Unity's [targetTexture documentation](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Camera-targetTexture.html) describes the rendering extent. It does not establish the mapping used by [WorldToScreenPoint](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Camera.WorldToScreenPoint.html).

Display routing uses the camera target display and observed viewport, with `camera_rect_ignored=false`. Display 0 dimensions come from the current Screen/Game View; secondary dimensions come only from an active display. Diagnosis never activates a display. Missing/inactive displays, dynamic resolution, jitter mismatch, unsupported texture routes or inconsistent viewport dimensions retain valid normalized geometry but leave target pixels unavailable. Camera settings are reported from their current getters; a prior assignment alone does not prove the observed state.

The result describes camera target properties. It does not establish final submission through MRT/SetTargetBuffers, SRP camera stacks, custom compositors, late pipeline changes, shaders, masks, occlusion, interaction or event delivery. Camera/activity/layer conditions are observations, not visibility proof. A disabled camera can still have valid current property-based projection. Actual backbuffer subviewport verification is distinct from rendering into a newly allocated texture.

## Bounds and partial results

| Option | Default | Accepted range |
| --- | ---: | --- |
| `max_nodes` | 200 | 1..1000 |
| `max_depth` | 10 | 0..24 |
| `max_evaluations` | 20000 | 1..200000 |
| `max_characters` | 65536 | 1..262144 UTF-16 units |
| `include_inactive` | false | strict Boolean |

Native null budgets mean their defaults. In the Python public API, omitted/null optional depth, evaluation and character budgets use defaults; the existing strict non-null `max_nodes` SDK schema remains unchanged. Omit `max_nodes` for its default; public `max_nodes: null` is refused. Node admission includes plain Transform gaps; only RectTransforms emit geometry. Paths use current physical child indices. A depth limit can exclude descendants without recursion or full-tree precollection. Ancestor preflight is limited to 128 steps. Evaluation categories account for camera observations, ancestors, admissions, child fetches, world corners, plane distances, intersections and projected vertices. Work is charged before the corresponding observation/math. Shared characters include context names and node names/paths; local names cap at 256 UTF-16 units without splitting surrogate pairs. Local truncation permits later siblings while shared allowance remains. Total native data is limited to 2 MiB before publication.

`traversal` reports visited/emitted counts, category totals, copied characters, effective budgets and truncation reasons. `partial` preserves admitted evidence without converting incomplete nodes into clean geometry. `unavailable` has an explicit bounded cause and no fabricated context. Unsupported arguments and ambiguous references return constant errors without echoing rejected content. New-action mutation/profile/preview options refuse before lookup or transport. Python accepts only the declared action-specific parameters; native aliases are exact snake/camel spellings and duplicate aliases refuse.

These bounds cover managed traversal, retained strings and explicit geometric operations. Scene lookup, Unity native getter allocation/time, render timing and wall time are not bounded by these counters. Current layout is observed, not certified settled. Active stereo, nonconventional/singular/nonfinite matrices and unsupported projection conventions remain unavailable.

## Snapshot compatibility

`capture_ui_snapshot` versions 2 and 3 continue to store world-XY AABBs. Camera-only movement can change actual pixels while leaving those semantic snapshots equal. The snapshot limitation points to this companion action; its schema, control-state privacy, semantic identities and offline comparator are unchanged. Compare projection evidence as its own closed artifact rather than feeding it to the semantic snapshot comparator. Successful replies lacking this contract are refused by the Python boundary with `unsupported_projection_response`; unrecognized native failures are reduced to `projection_unavailable`. An older plugin may therefore return a bounded refusal without a usable artifact; use matching updated plugin/server versions.
