# Device-profile uGUI diagnostics

`manage_ugui(action="diagnose_device_profiles")` is a READ operation with a separate,
closed `ugui_device_profile_diagnostics` version 1 report. Legacy `diagnose`,
`resolutions`, and `diagnose_projection` retain their contracts. The new action does
not add a CLI command.

Supply `target` and `device_profiles` containing 1 to 8 records. Optional arguments
are `camera`, `include_inactive`, `max_nodes` (200, 1..1000), `max_depth` (10, 0..24),
`max_evaluations` (20000, 1..200000), `max_characters` (65536, 1..262144), and
`minimum_touch_target_mm`. All other action-specific arguments are rejected.

```json
{
  "action": "diagnose_device_profiles",
  "target": "Canvas/Continue",
  "minimum_touch_target_mm": 7,
  "device_profiles": [
    {
      "source": "virtual", "width": 800, "height": 600, "dpi": 160,
      "safe_area": [0, 0, 800, 600], "orientation": "landscape_left"
    },
    {"source": "observed"}
  ]
}
```

Virtual records require exactly `source`, integer `width`/`height` (64..8192),
positive finite `dpi`, nonempty contained `safe_area=[x,y,width,height]`, and
`orientation` (`portrait`, `portrait_upside_down`, `landscape_left`, or
`landscape_right`). Numeric validity includes Unity float narrowing and derived
rectangle arithmetic. A positive double that underflows to zero or overflows
Single is invalid. Request validation precedes scene lookup and transport.

An observed record accepts only `source="observed"`. It captures current Screen
dimensions, DPI, safe area, and concrete orientation in the report's frame.
Unavailable/invalid safe area or nonconcrete orientation produces unavailable
profile evidence; no full-screen rectangle is substituted. Unknown DPI withholds
millimetre findings while still permitting valid geometric safe-area assessment.
Observed metadata applies only to the primary current-screen output. Matching
dimensions do not associate Screen metadata with a render texture or secondary
display. Editor observations do not certify a physical device.

Screen Space Overlay uses an owned preview scene. The source is never rebuilt.
Only exact installed built-in Horizontal/Vertical/GridLayoutGroup, LayoutElement,
ContentSizeFitter, AspectRatioFitter, Image, and RawImage providers are copied.
Source custom layout, Graphic, Selectable, and ILayoutIgnorer getters are never
invoked. Exact installed `TMPro.TextMeshProUGUI` fixed rectangles are supported
without cloning TMP or calculating text/font metrics. This includes glyph auto
sizing within a fixed rectangle, GridLayoutGroup cells, and linear layout groups
with both child-control axes disabled. A TMP rectangle remains partial when
`autoSizeTextContainer` is enabled, an enabled ContentSizeFitter on the text fits
either axis, or an enabled direct-parent Horizontal/VerticalLayoutGroup controls
either child axis. These cases need text preferred metrics, which are not reused
from source caches across virtual profile dimensions. TMP subclasses and other
layout providers, including legacy Text, are omitted and mark the preview partial.
Affected size, safe-area, and touch findings become unknown.
Nested Canvas contexts are also partial. This intentionally conservative scope
avoids claiming that an incomplete layout passed.

Constant Physical Size uses both DPI/unit scale and
`referencePixelsPerUnit * unitDPI / defaultSpriteDPI`, including Image preferred
layout. CanvasScaler fallback DPI explains layout when Screen DPI is unavailable;
it never supplies measured millimetres. Invalid derived scale, density, or root
size makes that profile unavailable. Orientation describes the supplied output
coordinate frame: the diagnostic does not rotate or swap profile dimensions.

Screen Camera and World Space use the existing current-camera projection report,
retained unchanged under `projection` with `evidence.profile=null`. World Space
requires an explicit camera. Assessment requires the same output dimensions as
the observed pixel route. Different-resolution camera resimulation is unsupported.
Current corners already contain CanvasScaler effects and are not scaled again.
Safe-area intersection uses output coordinates, including subviewport offsets.
Camera assessments use the clipped projected quad's AABB; they do not characterize
portions outside the frustum.

`width_mm`/`height_mm` are geometric DPI estimates. A 48px rectangle is 7.62mm at
160 DPI and 3.81mm at 320 DPI. Only an explicit caller threshold yields
`meets_caller_threshold` or `below_caller_threshold`, and only for an observed
exact built-in raycast rectangle candidate. No universal accessibility minimum
is assumed. A candidate does not establish effective interactability, hit order,
pointer/event delivery, or hardware touch accuracy.

`inside_geometric_safe_area` describes an AABB intersection, and
`possible_unsafe_region` describes a nonzero area outside the supplied safe area.
Neither proves notch occlusion, rendered clipping, or rasterized visibility.
Rotation, masks, sprite alpha, and custom render pipelines require other evidence.

The work budget is shared across resolver/snapshot work, ancestors, preview
transforms/components, profile setup, layout/assessment pairs, projection work,
and output admission. Exhaustion produces partial/unknown evidence, including
when later profiles cannot be reached. `max_characters` bounds retained dynamic
node/projection JSON in UTF-16 units; the bounded control envelope, at most eight
profile receipts, counters, and fixed limitations remain reportable at tiny
budgets. A separate 2 MiB artifact cap bounds the full response. Managed work
accounting does not impose a hard deadline on Unity native getters, scene lookup,
or approved built-in preview layout internals.

Target and explicit camera names/paths use this action's bounded selector. Scene
and root enumeration, every visited node, child fetch, path extension and candidate
comparison are charged against the same request budget. A single request also
caps selector visits at 1000, ancestry at 128, and selector/path text at 4096 UTF-16
units. Exhaustion emits partial evidence without choosing a match whose uniqueness
has not been established. Existing legacy action selectors are unchanged.

Single-valued request echoes and retained bounds are compared in Unity's float
domain. Safe-area endpoints and intersection subtraction use the same Single
arithmetic as Rect before double area calculations, including fractional values.

The operation never sets Screen resolution/orientation/safe area, edits source
Canvas/scaler/RectTransforms, rebuilds source layout, invokes custom callbacks,
renders, or dispatches pointer/selection events. Preview cleanup runs in `finally`.
The server rejects unknown peer fields, unqualified legacy output, incorrect
budget/profile/threshold echoes, impossible physical/safe-area findings, and
invalid retained projection evidence.

A finite rectangle with zero or negative extent is omitted with
`degenerate_geometry`; NaN, Infinity, or nonfinite endpoints use
`nonfinite_geometry`. Reasons apply to the profile: another valid node can retain
finite bounds alongside a reason caused by an omitted degenerate node. Neither
reason certifies that every node has invalid bounds. Older peer reports using
`nonfinite_geometry` remain accepted.
