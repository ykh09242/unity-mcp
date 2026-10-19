# UI capture request ownership

`manage_ui(action="render_ui", ...)` captures the whole composited Game view in
Play mode, including UI Toolkit overlays. Its target identifies the originating
request and response metadata; it does not crop the image to that target.

Start a capture without `capture_id`, then retain the returned ID:

```python
started = await manage_ui(
    ctx=ctx,
    action="render_ui",
    target="UIRoot",
    screenshot_file_name="ui-preview.png",
    output_folder="Captures",
    include_image=True,
)
capture_id = started["data"]["capture_id"]

# Repeat this call while data.pending is true. The successful completed response
# contains the PNG path and uses the settings from the start request.
result = await manage_ui(ctx=ctx, action="render_ui", capture_id=capture_id)
```

The pending response describes how to poll and cancel. To discard an owned
capture, call:

```python
cancelled = await manage_ui(
    ctx=ctx,
    action="render_ui",
    capture_id=capture_id,
    cancel_capture=True,
)
```

Only one deferred request or completed result is retained at a time, including
opt-in Edit-mode warmup. A call
without `capture_id` always represents a new request, even when its settings are
identical to another request. It cannot retrieve, cancel or replace a live
owner's result. After retrieval, cancellation or expiry, a new start receives a
new ID. IDs become invalid on Play-mode transitions, assembly reload and editor
shutdown.

An ID-only poll uses the original target, component selection, output path,
dimensions, warmup count and inline-image settings. If a caller repeats any of these settings,
they must match the originating request. The output path is selected once at
start, including automatic name generation and existing-file collision handling.
A later change to the screenshot folder preference cannot redirect the result.

The pending capture and completed result each have a 30-second retention limit.
The screenshot coroutine also has its existing short timeout for a paused or
unfocused Game view. Expiry and cancellation release the owned texture and
invalidate late callbacks; a stale or foreign ID cannot release a newer result.

## Migration from implicit Play-mode polling

Older callers invoked `render_ui` a second time with the same target and output
arguments to consume a global pending result. Update that second call to echo
`data.capture_id` from the start response. Settings alone cannot distinguish an
owner polling its result from an independent caller requesting the same image.
ID-less calls now receive an explicit busy/result-pending error while another
request owns the slot.

## Edit-mode readback and bounded warmup

The default `warmup_updates=0` retains immediate, best-effort panel RenderTexture
readback without a capture ID. Assigning a target does not prove that UI Toolkit
has painted it. The first immediate call retains the existing warmup binding;
another explicitly requested immediate capture can read that cached target.

To wait before a single readback, supply `warmup_updates` from **1 to 4** on the
start request. It returns `capture_id`; poll or cancel that ID exactly as above.
The tool requests Editor player-loop updates and counts Editor update callbacks,
then reads once. It does not retry until opaque pixels appear, resize/focus the
Game view, or promise a completed UI repaint. A zero-alpha result is successful.
Play-mode starts require zero warmup updates because their existing coroutine
already waits for the end of a frame.

The panel binding remains owned during warmup. Cancellation, expiry, mode changes,
reload and shutdown release its temporary resources. After deferred readback, the
previous target is restored if the panel still points to the owned target. An
external replacement is preserved and causes that capture to fail instead of
reading another owner's image. Existing per-frame and cached RenderTexture
budgets apply; completed image retention remains 30 seconds.

## Readiness and image evidence

Completed responses describe the full-resolution saved PNG, even when the inline
image is downscaled:

| Field | Meaning |
| --- | --- |
| `capture_source` | `panel_render_texture` in Edit mode; `game_view` in Play mode. |
| `full_game_view` | True only for the composited Game view. Target metadata does not imply a crop. |
| `readiness` | `target_just_assigned` or `existing_target_readback` for immediate Edit capture; `bounded_editor_updates_elapsed` for deferred Edit capture; `end_of_frame_capture` for Play capture. |
| `warmup_updates_requested`, `warmup_updates_observed` | Bounded Edit update counts, not a count of proven repaint completions. |
| `ui_repaint_completion` | `not_observed`; no UI Toolkit completion callback is inferred. |
| `hasContent` | Legacy Boolean: at least one image pixel has nonzero alpha. It is not a UI-presence verdict. |
| `content_check.method` | `full_alpha_scan`, inspecting every pixel without sparse sampling. |
| `content_check.pixel_basis` | `full_resolution_saved_png`. |
| `content_check.meaning` | `image_alpha_occupancy`. |
| `content_check.pixels_examined` | The full image pixel count. |
| `content_check.nonzero_alpha_pixels` | Pixels whose alpha byte is nonzero. |
| `content_check.alpha_occupancy` | Nonzero-alpha pixels divided by all image pixels. |
| `content_check.ui_presence` | `not_determined`, including an opaque image with no visible UI. |

A single opaque pixel counts even when it falls between old sample positions.
Conversely, an opaque camera or panel background can fill the image without
establishing that UI is present. Transparency alone is neither a capture error
nor proof of missing UI. Pending responses report `awaiting_editor_updates` or
`awaiting_end_of_frame`; they do not contain completed-image content evidence.

Focused native fixtures compare real 64x64 transparent, tiny opaque and opaque
background captures with independently decoded PNG pixels, exercise cold versus
bounded warmup, and compare a real full Game view capture with its PNG. Separate
ownership tests cover cancellation, expiry and external target replacement.

API references: [Texture2D.GetPixels32](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Texture2D.GetPixels32.html),
[EditorApplication.QueuePlayerLoopUpdate](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/EditorApplication.QueuePlayerLoopUpdate.html),
and [ScreenCapture.CaptureScreenshotAsTexture](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/ScreenCapture.CaptureScreenshotAsTexture.html).
